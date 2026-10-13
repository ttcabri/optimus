"""Чек: бот читает фото, показывает на проверку, после «Всё верно» разносит:
продукты в Дом (с количеством), сам чек в Мозг → Документы → Чеки, сумму в Деньги."""
import html
import json
import logging
import re
import time

from services import brain, house, money, quest

logger = logging.getLogger(__name__)


READ = """Это фото чека из магазина в Нидерландах. Прочитай его ПОСТРОЧНО и аккуратно, как бухгалтер.
Подпись человека: "{caption}"

Как читать голландский чек:
- Строка товара: название и цена справа. Строка вида «2 x 1,29» или «2 st à 1,29» относится к соседнему товару: amt = количество штук, price = итог этой позиции (2.58).
- Весовой товар: «0,512 kg x 3,99/kg» → amt 512, unit г, price итог строки.
- BONUS, KORTING, ACTIE, «-0,50», «Voordeel» это скидка. Если видно, к какому товару: вычти из его price и запиши размер скидки в disc этого товара. Общая скидка на весь чек (BONUSKAART итого, «uw voordeel» в конце как итог сэкономленного) НЕ вычитается второй раз: если она уже учтена в строках, не пиши её; если это отдельная строка с минусом, которая не отнесена к товару, запиши в extra как disc с минусом.
- STATIEGELD / EMBALLAGE / «statiegeld fles», «blik»: залог за бутылки и банки (обычно 0,15 за банку и маленькую бутылку, 0,25 за большую бутылку). Не товар: в extra как deposit с плюсом, n = что и сколько («3 × 0,15»).
- «STATIEGELD BON», «EMBALLAGEBON», «retour», минус за сданные бутылки: в extra как bon с МИНУСОМ.
- «tas», «draagtas», «zak», «plastic tas»: пакет, в extra как bag.
- Не путай: залог и пакеты входят в итог, но это не продукты.
- TOTAAL, TE BETALEN, «Totaal», «PIN» = итог к оплате → total.
- Сокращения AH: «AH» = своя марка, «HV» = halfvolle (полужирное), «KIPFILET» = куриное филе, «GEHAKT» = фарш, «STOKBROOD» = багет, «KAAS» = сыр, «EIEREN» = яйца, «MELK» = молоко, «BROOD» = хлеб.
- Дата обычно внизу (dd-mm-yyyy или dd/mm/yy), магазин вверху.

Для каждой позиции: name = по-русски коротко (молоко, куриное филе, яйца), orig = как на чеке, cat = meat|dairy|veg|grain|canned|bread|drinks|spice|other|home (home = мыло, порошок, бумага, губки, бытовое), amt = число (вес/объём упаковки × количество, если видно на чеке или понятно по названию «1L», «500G», «10st»; иначе штуки), unit = г|кг|мл|л|шт, price = итог позиции в € после скидки.
В конце проверь: сумма price всех товаров + сумма amt в extra должна равняться total. Не сходится: перечитай строки. Не видно строку: пропусти, не выдумывай. Если чек нечитаемый, items пустой и в note скажи почему (смазано, обрезано, мелко).

Ответь ТОЛЬКО JSON без markdown:
{{"shop": "", "date": "YYYY-MM-DD или null", "total": число или null, "items": [{{"name": "", "orig": "", "cat": "", "amt": число, "unit": "", "price": число, "disc": число или null}}], "extra": [{{"k": "deposit|bon|bag|disc", "n": "", "amt": число со знаком}}], "note": ""}}"""

EXTRA = {"deposit": ("♻️", "Залог за тару"), "bon": ("♻️", "Сдал бутылки"), "bag": ("🛍", "Пакет"), "disc": ("🏷", "Скидка")}


async def read(image_bytes: bytes, caption: str = "") -> dict | None:
    """Отдельное внимательное чтение чека сильной моделью (первый проход лишь понял, что это чек)."""
    import base64
    from services import claude
    try:
        resp = await claude._create(max_tokens=4000, messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode()}},
            {"type": "text", "text": READ.format(caption=caption[:200])}]}])
        t = "".join(b.text for b in resp.content if b.type == "text")
        m = re.search(r"\{.*\}", t, re.S)
        data = json.loads(m.group(0)) if m else None
    except Exception as e:
        logger.error(f"receipt read: {e}")
        return None
    if not isinstance(data, dict):
        return None
    logger.info(f"receipt read: {data.get('shop')} total {data.get('total')} items {len(data.get('items') or [])} {data.get('note') or ''}")
    return data


def _doc(state: dict, r: dict) -> dict:
    """Чек сразу лежит в Документы → Чеки (даже до проверки), потом обновляется."""
    items = brain.get(state)["items"]
    it = next((x for x in items if x.get("id") == r.get("doc")), None)
    if not it:
        it = {"id": brain._new_id(), "f": "docs", "c": "receipts", "fit": "later", "src": "tg"}
        items.append(it)
        r["doc"] = it["id"]
    it.update({"t": f"Чек {r['shop'] or ''} {r['date'][8:10]}.{r['date'][5:7]}".strip(), "s": ", ".join(i["name"] for i in r["items"])[:300],
               "d": r["date"], "shop": r["shop"], "sum": r.get("total"), "rcpt": {k: r.get(k) for k in ("shop", "date", "total", "items", "extra")}})
    if r.get("file"):
        it["file"] = {"id": r["file"], "kind": r.get("fkind") or "photo", "name": "чек"}
    brain.touch(it)
    return it


def pending(state: dict, res: dict, file_id: str | None, fkind: str = "photo") -> dict:
    items = []
    for x in res.get("items") or []:
        it = {"name": str(x.get("name") or "")[:60], **({"orig": str(x["orig"])[:40]} if x.get("orig") else {}), "cat": x.get("cat") if x.get("cat") in house.CATS else house.guess_cat(str(x.get("name") or ""))}
        if isinstance(x.get("amt"), (int, float)):
            it["amt"], it["unit"] = x["amt"], x.get("unit") or "шт"
        elif x.get("qty"):
            it["qty"] = str(x["qty"])[:20]
        if isinstance(x.get("price"), (int, float)):
            it["price"] = round(float(x["price"]), 2)
        if isinstance(x.get("disc"), (int, float)) and x["disc"]:
            it["disc"] = round(abs(float(x["disc"])), 2)
        if it["name"]:
            items.append(it)
    extra = []
    for x in res.get("extra") or []:
        k = x.get("k") if x.get("k") in EXTRA else None
        if k and isinstance(x.get("amt"), (int, float)) and x["amt"]:
            a = abs(float(x["amt"])) * (-1 if k in ("bon", "disc") else 1)
            extra.append({"k": k, "n": str(x.get("n") or "")[:40], "amt": round(a, 2)})
    total = res.get("total")
    if not isinstance(total, (int, float)):
        total = round(sum(i.get("price") or 0 for i in items) + sum(e["amt"] for e in extra), 2) or None
    r = {"id": "r" + format(int(time.time() * 1000), "x"), "shop": res.get("shop") or "", "date": res.get("date") or quest.today_key(),
         "total": total, "items": items, "extra": extra, "file": file_id, "fkind": fkind}
    if not re.match(r"\d{4}-\d{2}-\d{2}$", str(r["date"])):
        r["date"] = quest.today_key()
    old = state.get("rcpt") or {}
    if old.get("doc") and old.get("file") == file_id:
        r["doc"] = old["doc"]
    _doc(state, r)
    state["rcpt"] = r
    return r


def _q(i: dict) -> str:
    n = house.norm_amt(i["amt"], i.get("unit")) if i.get("amt") is not None else None
    return house.fmt_amt({"amt": n[0], "unit": n[1]}) if n else (i.get("qty") or "")


def dep(r: dict) -> float:
    """Залог минус сданные бутылки: эти деньги вернутся в автомате, это не еда."""
    return round(sum(e["amt"] for e in r.get("extra") or [] if e["k"] in ("deposit", "bon")), 2)


def check(r: dict) -> float | None:
    """Сколько не сходится: итог минус (товары + доп. строки). None, если итога нет."""
    if not isinstance(r.get("total"), (int, float)):
        return None
    return round(r["total"] - sum(i.get("price") or 0 for i in r["items"]) - sum(e["amt"] for e in r.get("extra") or []), 2)


def split(r: dict) -> dict:
    """Сумма по категориям денег: хозтовары в «Быт», остальное (с пакетами и залогом) в «Еда»."""
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
        lines.append(f"• {html.escape(i['name'])}" + (f" <i>({html.escape(i['orig'])})</i>" if i.get("orig") else "") + (f" {q}" if q else "") + (f" · €{i['price']:g}" if i.get("price") else "") + (" 🧴" if i.get("cat") == "home" else ""))
    for e in r.get("extra") or []:
        ic, nm = EXTRA[e["k"]]
        lines.append(f"{ic} {nm}" + (f" ({html.escape(e['n'])})" if e.get("n") else "") + f" · {'−' if e['amt'] < 0 else ''}€{abs(e['amt']):.2f}")
    saved = round(sum(i.get("disc") or 0 for i in r["items"]), 2)
    if saved:
        lines.append(f"🏷 Скидки уже в ценах: −€{saved:.2f}")
    d = check(r)
    if d:
        lines.append(f"⚠️ Строки не сходятся с итогом на €{d:.2f}. Проверь или поправь.")
    sp = split(r)
    where = []
    if any(i.get("cat") != "home" for i in r["items"]):
        where.append("продукты в Дом")
    if any(i.get("cat") == "home" for i in r["items"]):
        where.append("🧴 в Хозтовары")
    if sp:
        where.append(" + ".join(f"€{v:g} в траты «{money.CATS[k][1]}»" for k, v in sp.items()))
    if dep(r) > 0:
        where.append(f"♻️ €{dep(r):.2f} залога запомню (вернёшь, сдав тару)")
    where.append("чек уже лежит в Документы → Чеки")
    w = ", ".join(where)
    return "\n".join(lines) + "\n\n" + w[:1].upper() + w[1:] + ".\nВсё верно? Поправить можно кнопкой ✏️ или словами."


def context(state: dict) -> str:
    r = state.get("rcpt")
    if not r:
        return ""
    return ("ЧЕК НА ПРОВЕРКЕ (ждёт «Всё верно», ещё не записан в Дом и Деньги; править инструментом receipt):\n"
            + f"{r.get('shop') or 'магазин?'} {r.get('date')} итог {r.get('total')}\n"
            + "\n".join(f"- {i['name']}" + (f" ({i['orig']})" if i.get("orig") else "") + (f" {_q(i)}" if _q(i) else "") + (f" €{i['price']:g}" if i.get("price") else "") for i in r["items"])
            + "".join(f"\n- {EXTRA[e['k']][1]} {e.get('n') or ''} €{e['amt']:g}" for e in r.get("extra") or []) + "\n\n")


def kb(r: dict):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    from config import WEBAPP_URL
    edit = (InlineKeyboardButton(text="✏️ Править", web_app=WebAppInfo(url=WEBAPP_URL + ("&" if "?" in WEBAPP_URL else "?") + "rcpt=1"))
            if WEBAPP_URL and WEBAPP_URL.startswith("https://") else InlineKeyboardButton(text="✏️ Поправить", callback_data=f"rc:edit:{r['id']}"))
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Всё верно", callback_data=f"rc:ok:{r['id']}"), edit],
                                                 [InlineKeyboardButton(text="Не записывать", callback_data=f"rc:no:{r['id']}")]])


def apply(state: dict, data: dict) -> dict | None:
    """Правка чека руками из приложения: те же правила, что при чтении, id и фото те же."""
    r = state.get("rcpt")
    if not r or not isinstance(data, dict):
        return None
    out = pending(state, data, r.get("file"), r.get("fkind") or "photo")
    out["id"] = r["id"]
    return out


FIX = """Вот чек, который ты прочитал, в JSON:
{r}
Человек поправил: «{fix}»
Исправь чек по его словам (убери, добавь, поменяй количество, цену, итог, магазин, дату, залог за бутылки, пакеты, скидки). Итог total пересчитай, если он поменял позиции и не назвал итог.
extra: k = deposit (залог за тару, плюс), bon (сдал бутылки, минус), bag (пакет), disc (общая скидка, минус).
Ответь ТОЛЬКО JSON в том же виде: {{"shop": "", "date": "YYYY-MM-DD", "total": число, "items": [{{"name": "", "orig": "", "cat": "", "amt": число или null, "unit": "г|кг|мл|л|шт", "price": число или null, "disc": число или null}}], "extra": [{{"k": "", "n": "", "amt": число}}]}}"""


async def fix(state: dict, correction: str) -> dict | None:
    from services import claude
    r = state.get("rcpt")
    if not r:
        return None
    try:
        resp = await claude._create(max_tokens=1500, messages=[{"role": "user", "content": FIX.format(
            r=json.dumps({k: r.get(k) for k in ("shop", "date", "total", "items", "extra")}, ensure_ascii=False), fix=correction[:500])}])
        t = "".join(b.text for b in resp.content if b.type == "text")
        m = re.search(r"\{.*\}", t, re.S)
        new = json.loads(m.group(0)) if m else None
    except Exception as e:
        logger.error(f"receipt fix: {e}")
        return None
    if not isinstance(new, dict):
        return None
    out = pending(state, new, r.get("file"), r.get("fkind") or "photo")
    out["id"] = r["id"]
    return out


def commit(state: dict, uid: int) -> str:
    r = state.pop("rcpt", None)
    if not r:
        return "Этот чек уже записан или отменён."
    h = house.get(state, uid)
    got = house.add_items(h, [i for i in r["items"]])
    it = _doc(state, r)
    it["ok"] = True
    warns = []
    dp = dep(r)
    for cat, amt in split(r).items():
        o, w = money.add(state, "exp", amt, cat, None, r["shop"] or "чек", r["date"] if r["date"] <= quest.today_key() else None, "rcpt:" + it["id"])
        if dp and cat == "food":
            o["dep"] = dp
        if w:
            warns.append(w)
    return ("✅ Записал.\n🏠 Дом: " + (html.escape(", ".join(got)) if got else "ничего") +
            (f"\n💰 Траты: €{r['total']:g}" if r.get("total") else "")
            + (f"\n♻️ Залог за тару к возврату: €{money.deposit(money.get(state)):.2f}" if dp else "")
            + "\n🧾 Чек лежит в Документы → Чеки."
            + ("\n" + "\n".join(warns) if warns else ""))
