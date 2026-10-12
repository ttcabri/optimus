"""Чек: бот читает фото, показывает на проверку, после «Всё верно» разносит:
продукты в Дом (с количеством), сам чек в Мозг → Документы → Чеки, сумму в Деньги."""
import html
import json
import logging
import re
import time

from services import brain, house, money, quest

logger = logging.getLogger(__name__)


def pending(state: dict, res: dict, file_id: str | None) -> dict:
    items = []
    for x in res.get("items") or []:
        it = {"name": str(x.get("name") or "")[:60], "cat": x.get("cat") if x.get("cat") in house.CATS else house.guess_cat(str(x.get("name") or ""))}
        if isinstance(x.get("amt"), (int, float)):
            it["amt"], it["unit"] = x["amt"], x.get("unit") or "шт"
        elif x.get("qty"):
            it["qty"] = str(x["qty"])[:20]
        if isinstance(x.get("price"), (int, float)):
            it["price"] = round(float(x["price"]), 2)
        if it["name"]:
            items.append(it)
    total = res.get("total")
    if not isinstance(total, (int, float)):
        total = round(sum(i.get("price") or 0 for i in items), 2) or None
    r = {"id": "r" + format(int(time.time() * 1000), "x"), "shop": res.get("shop") or "", "date": res.get("date") or quest.today_key(),
         "total": total, "items": items, "file": file_id}
    if not re.match(r"\d{4}-\d{2}-\d{2}$", str(r["date"])):
        r["date"] = quest.today_key()
    state["rcpt"] = r
    return r


def _q(i: dict) -> str:
    n = house.norm_amt(i["amt"], i.get("unit")) if i.get("amt") is not None else None
    return house.fmt_amt({"amt": n[0], "unit": n[1]}) if n else (i.get("qty") or "")


def split(r: dict) -> dict:
    """Сумма по категориям денег: хозтовары в «Быт», остальное в «Еда»."""
    home = sum(i.get("price") or 0 for i in r["items"] if i.get("cat") == "home")
    tot = float(r.get("total") or 0)
    out = {}
    if home and tot:
        out["home"] = round(min(home, tot), 2)
    rest = round(tot - out.get("home", 0), 2)
    if rest > 0:
        out["food"] = rest
    return out


def text(r: dict) -> str:
    lines = [f"🧾 <b>{html.escape(r['shop'] or 'Чек')} · {r['date'][8:10]}.{r['date'][5:7]}" + (f" · €{r['total']:g}" if r.get("total") else "") + "</b>", "Прочитал:"]
    for i in r["items"]:
        q = _q(i)
        lines.append(f"• {html.escape(i['name'])}" + (f" {q}" if q else "") + (f" · €{i['price']:g}" if i.get("price") else "") + (" 🧴" if i.get("cat") == "home" else ""))
    sp = split(r)
    where = []
    if any(i.get("cat") != "home" for i in r["items"]):
        where.append("продукты в Дом")
    if any(i.get("cat") == "home" for i in r["items"]):
        where.append("🧴 в Хозтовары")
    if sp:
        where.append(" + ".join(f"€{v:g} в траты «{money.CATS[k][1]}»" for k, v in sp.items()))
    where.append("чек в Документы → Чеки")
    w = ", ".join(where)
    return "\n".join(lines) + "\n\n" + w[:1].upper() + w[1:] + ".\nВсё верно?"


def kb(r: dict):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Всё верно", callback_data=f"rc:ok:{r['id']}"),
                                                  InlineKeyboardButton(text="✏️ Поправить", callback_data=f"rc:edit:{r['id']}")],
                                                 [InlineKeyboardButton(text="Не записывать", callback_data=f"rc:no:{r['id']}")]])


FIX = """Вот чек, который ты прочитал, в JSON:
{r}
Человек поправил: «{fix}»
Исправь чек по его словам (убери, добавь, поменяй количество, цену, итог, магазин, дату). Итог total пересчитай, если он поменял позиции и не назвал итог.
Ответь ТОЛЬКО JSON в том же виде: {{"shop": "", "date": "YYYY-MM-DD", "total": число, "items": [{{"name": "", "cat": "", "amt": число или null, "unit": "г|кг|мл|л|шт", "price": число или null}}]}}"""


async def fix(state: dict, correction: str) -> dict | None:
    from services import claude
    r = state.get("rcpt")
    if not r:
        return None
    try:
        resp = await claude._create(max_tokens=1500, messages=[{"role": "user", "content": FIX.format(
            r=json.dumps({k: r[k] for k in ("shop", "date", "total", "items")}, ensure_ascii=False), fix=correction[:500])}])
        t = "".join(b.text for b in resp.content if b.type == "text")
        m = re.search(r"\{.*\}", t, re.S)
        new = json.loads(m.group(0)) if m else None
    except Exception as e:
        logger.error(f"receipt fix: {e}")
        return None
    if not isinstance(new, dict):
        return None
    out = pending(state, new, r.get("file"))
    out["id"] = r["id"]
    return out


def commit(state: dict, uid: int) -> str:
    r = state.pop("rcpt", None)
    if not r:
        return "Этот чек уже записан или отменён."
    h = house.get(state, uid)
    got = house.add_items(h, [i for i in r["items"]])
    it = {"id": brain._new_id(), "f": "docs", "c": "receipts", "t": f"Чек {r['shop'] or ''} {r['date'][8:10]}.{r['date'][5:7]}".strip(),
          "s": ", ".join(i["name"] for i in r["items"])[:300], "fit": "later", "src": "tg", "d": r["date"],
          "shop": r["shop"], "sum": r.get("total")}
    if r.get("file"):
        it["file"] = {"id": r["file"], "kind": "photo", "name": "чек"}
    brain.get(state)["items"].append(brain.touch(it))
    warns = []
    for cat, amt in split(r).items():
        _, w = money.add(state, "exp", amt, cat, None, r["shop"] or "чек", r["date"] if r["date"] <= quest.today_key() else None, "rcpt:" + it["id"])
        if w:
            warns.append(w)
    pdv = money.per_day(state)
    return ("✅ Записал.\n🏠 Дом: " + (html.escape(", ".join(got)) if got else "ничего") +
            (f"\n💰 Траты: €{r['total']:g}" if r.get("total") else "") + "\n🧾 Чек в Документы → Чеки."
            + ("\n" + "\n".join(warns) if warns else "") + (f"\nДо конца месяца можно ~€{pdv[0]:g} в день." if pdv else ""))
