"""Дом: холодильник, припасы, бытовое, покупки, дела по дому, проекты (переезд).

Хранится как отдельное «хозяйство» с участниками (members), чтобы потом добавить семью.
Каждая запись имеет id и u (время изменения, мс). Удалённые id лежат в del.
Слияние приложение/бот: по id побеждает больший u, удалённое не воскресает.
Та же логика есть в webapp/index.html.
"""
import time
from datetime import datetime, timedelta

from services import quest

# Категории: еда по видам (как плитки в приложении) и хозтовары. Срок по умолчанию в днях, None = без срока.
CATS = {"prep": "Заготовки", "meat": "Мясо, рыба", "dairy": "Молочное, яйца", "veg": "Овощи, фрукты",
        "grain": "Крупы, макароны", "canned": "Консервы, соусы", "bread": "Хлеб, сладкое", "drinks": "Напитки",
        "spice": "Специи, масло", "other": "Другое", "home": "Хозтовары"}
CAT_DAYS = {"prep": 4, "meat": 3, "dairy": 5, "veg": 5, "bread": 4}
CAT_WORDS = {
    "prep": ("заготов", "мил-преп", "порци"),
    "meat": ("мяс", "фарш", "курин", "курица", "индейк", "говяд", "свинин", "колбас", "сосиск", "ветчин", "бекон", "рыб", "лосос", "тунец свеж", "креветк", "филе"),
    "dairy": ("молок", "кефир", "творог", "сметан", "йогурт", "сыр", "сливк", "масло слив", "яйц", "ряженк"),
    "veg": ("овощ", "помидор", "томат", "огур", "картош", "картоф", "лук", "морков", "капуст", "перец", "салат", "зелень", "брокол", "кабач", "фрукт", "яблок", "банан", "апельсин", "лимон", "груш", "виноград", "ягод", "авокадо", "чеснок", "гриб"),
    "grain": ("рис", "гречк", "макарон", "паста", "спагетти", "овсян", "хлопь", "мука", "крупа", "булгур", "киноа", "лапш", "мюсли"),
    "canned": ("консерв", "тунец", "фасол", "горошек", "кукуруз", "соус", "кетчуп", "майонез", "горчиц", "томатная паста", "варенье", "мёд", "мед"),
    "bread": ("хлеб", "батон", "лаваш", "булк", "печень", "шоколад", "конфет", "торт", "вафл", "круассан", "чипс", "орех"),
    "drinks": ("вода", "сок", "чай", "кофе", "кола", "пиво", "вино", "напит", "лимонад", "энергет"),
    "spice": ("соль", "сахар", "перец молот", "специ", "приправ", "масло раст", "оливк", "масло подсол", "уксус", "паприк", "куркум", "базилик", "орегано", "соевый"),
}
LEVELS = {"ok": "есть", "low": "мало", "out": "нет"}

# Точнее, чем по категории: сколько дней живёт после покупки
SHELF = [("фарш", 1), ("рыба", 1), ("курин", 2), ("курица", 2), ("мясо", 3), ("говяд", 3), ("свинин", 3),
         ("молоко", 5), ("кефир", 5), ("творог", 5), ("сметан", 7), ("йогурт", 7), ("ветчин", 5), ("колбас", 7),
         ("сыр", 14), ("яйц", 21), ("масло слив", 21), ("зелень", 4), ("салат", 3), ("банан", 4)]
OLD_CATS = {"fridge", "pantry"}  # было до категорий

DEFAULT_CHORES = [
    {"id": "c_wash", "name": "Стирка", "every": 7, "dur": 20},
    {"id": "c_clean", "name": "Уборка комнаты", "every": 7, "dur": 40},
    {"id": "c_trash", "name": "Вынести мусор", "every": 3, "dur": 5},
    {"id": "c_bed", "name": "Сменить постельное", "every": 14, "dur": 15},
]

# Права в доме. Должности настраивает создатель: какие права у какой должности.
PERMS = {
    "see_food": "Видеть холодильник и припасы",
    "edit_food": "Менять холодильник и припасы",
    "shop": "Список покупок",
    "chores": "Дела по дому (отмечать)",
    "assign": "Назначать дела другим",
    "projects": "Проекты (переезд и т.п.)",
    "notes": "Память дома",
    "members": "Приглашать людей и менять должности",
}
DEFAULT_ROLES = [
    {"id": "owner", "name": "Создатель", "perms": list(PERMS), "fixed": True},
    {"id": "adult", "name": "Взрослый", "perms": ["see_food", "edit_food", "shop", "chores", "assign", "projects", "notes"]},
    {"id": "helper", "name": "Помощник", "perms": ["see_food", "shop", "chores"]},
    {"id": "guest", "name": "Гость", "perms": ["shop"]},
]


def can(h: dict, uid, perm: str) -> bool:
    m = next((x for x in h.get("members", []) if isinstance(x, dict) and str(x.get("uid")) == str(uid)), None)
    if not m:
        return False
    role = next((r for r in h.get("roles", []) if r["id"] == m.get("role")), None)
    return bool(role) and (role["id"] == "owner" or perm in role.get("perms", []))


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str = "h") -> str:
    import os
    return f"{prefix}{now_ms():x}{os.urandom(2).hex()}"


HOME_WORDS = ("мыло", "гель", "порошок", "бумаг", "губк", "шампун", "зубн", "средство", "пакет", "салфет", "кондиционер", "таблетки для", "отбел", "дезодор")


def guess_cat(name: str) -> str:
    n = name.lower()
    if any(w in n for w in HOME_WORDS):
        return "home"
    for cat, words in CAT_WORDS.items():
        if any(w in n for w in words):
            return cat
    return "other"


def shelf_days(name: str, cat: str | None = None) -> int | None:
    """Сколько дней годно после покупки; None = без срока."""
    n = name.lower()
    cat = cat or guess_cat(name)
    if cat in ("home", "grain", "canned", "spice", "drinks", "other"):
        return None
    for k, d in SHELF:
        if k in n:
            return d
    return CAT_DAYS.get(cat)


def until_for(name: str, cat: str, days: int | None = None, today: str | None = None) -> str | None:
    days = days or shelf_days(name, cat)
    if not days:
        return None
    return (datetime.strptime(today or quest.today_key(), "%Y-%m-%d") + timedelta(days=int(days))).strftime("%Y-%m-%d")


def get(state: dict, uid: int | None = None) -> dict:
    """Дом из состояния; при первом обращении создаётся с делами по умолчанию."""
    h = state.get("house")
    me = (state.get("profile") or {}).get("name") or "Я"
    if not isinstance(h, dict):
        h = {"id": f"house_{uid or 'me'}", "owner": uid,
             "members": [{"uid": uid, "name": me, "role": "owner", "u": 0}] if uid else [],
             "roles": [dict(r, u=0) for r in DEFAULT_ROLES], "items": [], "shop": [],
             # отсчёт с сегодня, чтобы в первый день не вывалилось всё сразу
             "chores": [dict(c, last=quest.today_key(), u=0) for c in DEFAULT_CHORES],
             "projects": [],
             "del": []}
        state["house"] = h
    for k in ("items", "shop", "chores", "projects", "del", "notes"):
        h.setdefault(k, [])
    for x in h["items"]:  # старые «холодильник/кладовка» -> категории
        if x.get("cat") in OLD_CATS or x.get("cat") not in CATS:
            x["cat"] = guess_cat(x.get("name", ""))
    h.setdefault("roles", [dict(r, u=0) for r in DEFAULT_ROLES])
    # старый формат участников (просто id) -> записи с должностью
    h["members"] = [m if isinstance(m, dict) else {"uid": m, "name": me, "role": "owner", "u": 0} for m in h.get("members", [])]
    if uid:
        # дом, созданный в приложении до знакомства с ботом: привязываем к настоящему id
        for m in h["members"]:
            if m.get("uid") == "me":
                m["uid"] = uid
        if not h.get("owner") or h.get("owner") == "me":
            h["owner"] = uid
        if h.get("id") == "house_me":
            h["id"] = f"house_{uid}"
    return h


def _merge_list(a: list, b: list, dead: set) -> list:
    by = {}
    for x in a + b:
        i = x.get("id")
        if not i or i in dead:
            continue
        if i not in by or x.get("u", 0) > by[i].get("u", 0):
            by[i] = x
    return list(by.values())


def merge(server: dict | None, incoming: dict | None) -> dict | None:
    if not server:
        return incoming
    if not incoming:
        return server
    dead = set(server.get("del", [])) | set(incoming.get("del", []))
    out = dict(incoming)
    for k in ("items", "shop", "chores", "notes"):
        out[k] = _merge_list(server.get(k, []), incoming.get(k, []), dead)
    projs = {}
    for p in server.get("projects", []) + incoming.get("projects", []):
        if p.get("id") in dead:
            continue
        cur = projs.get(p["id"])
        if not cur:
            projs[p["id"]] = dict(p)
        else:
            cur["steps"] = _merge_list(cur.get("steps", []), p.get("steps", []), dead)
            if p.get("u", 0) > cur.get("u", 0):
                cur.update({k: v for k, v in p.items() if k != "steps"})
    out["projects"] = list(projs.values())
    out["del"] = sorted(dead)[-300:]
    def _norm(ms):
        return [m if isinstance(m, dict) else {"uid": m, "role": "owner", "u": 0} for m in ms or []]
    out["members"] = _merge_list([dict(m, id=str(m.get("uid"))) for m in _norm(server.get("members"))],
                                 [dict(m, id=str(m.get("uid"))) for m in _norm(incoming.get("members"))], set())
    out["roles"] = _merge_list(server.get("roles", []), incoming.get("roles", []), dead)
    out["owner"] = server.get("owner") or incoming.get("owner")
    return out


# --- вычисления ---

def chore_due(c: dict, today: str) -> bool:
    if not c.get("last"):
        return True
    d = datetime.strptime(c["last"], "%Y-%m-%d") + timedelta(days=int(c.get("every") or 7))
    return d.strftime("%Y-%m-%d") <= today


def due_chores(h: dict, today: str | None = None) -> list[dict]:
    today = today or quest.today_key()
    return [c for c in h.get("chores", []) if chore_due(c, today)]


def expiring(h: dict, today: str | None = None, within: int = 1) -> list[dict]:
    today = today or quest.today_key()
    lim = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=within)).strftime("%Y-%m-%d")
    return [x for x in h.get("items", []) if x.get("cat") != "home" and x.get("level") != "out"
            and x.get("until") and x["until"] <= lim]


def shop_list(h: dict) -> list[str]:
    names = [x["name"] for x in h.get("items", []) if x.get("level") in ("low", "out")]
    names += [s["name"] for s in h.get("shop", []) if not s.get("done")]
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def badge(h: dict, today: str | None = None) -> int:
    return len(due_chores(h, today)) + len(expiring(h, today)) + sum(1 for x in h.get("items", []) if x.get("level") == "out")


def _find(lst: list, name_or_id: str):
    q = str(name_or_id or "").strip().lower()
    for x in lst:
        if x.get("id") == name_or_id or x.get("name", "").lower() == q:
            return x
    for x in lst:
        if q and (q in x.get("name", "").lower() or x.get("name", "").lower() in q):
            return x
    return None


# --- количество: граммы, миллилитры, штуки ---
UNIT = {"г": ("г", 1), "g": ("г", 1), "гр": ("г", 1), "кг": ("г", 1000), "kg": ("г", 1000),
        "мл": ("мл", 1), "ml": ("мл", 1), "л": ("мл", 1000), "l": ("мл", 1000),
        "шт": ("шт", 1), "pcs": ("шт", 1), "": ("шт", 1)}
LOW = {"г": 150, "мл": 200, "шт": 2}  # меньше этого: «мало», в покупки


def norm_amt(amt, unit) -> tuple[float, str] | None:
    try:
        v = float(str(amt).replace(",", "."))
    except (TypeError, ValueError):
        return None
    u, k = UNIT.get(str(unit or "").strip().lower().rstrip("."), ("шт", 1))
    return round(v * k, 1), u


def fmt_amt(x: dict) -> str:
    if x.get("amt") is None:
        return x.get("qty") or ""
    v, u = float(x["amt"]), x.get("unit") or "шт"
    if u in ("г", "мл") and v >= 1000:
        return f"{v / 1000:g} {'кг' if u == 'г' else 'л'}"
    return f"{v:g} {u}"


def _relevel(x: dict) -> None:
    if x.get("amt") is None:
        return
    v, u = float(x["amt"]), x.get("unit") or "шт"
    x["level"] = "out" if v <= 0 else "low" if v < LOW.get(u, 1) else "ok"


def use(h: dict, name: str, amt=None, unit=None, all_: bool = False) -> str:
    """Съел / потратил: списать из дома. Без количества: «доел всё» = кончилось, иначе только отметка."""
    x = _find([y for y in h["items"] if y.get("cat") != "prep"], name)
    if not x:
        return f"«{name}» в доме не числится"
    x["u"] = now_ms()
    if all_:
        x["amt"] = 0 if x.get("amt") is not None else None
        x["level"] = "out"
        return f"{x['name']}: кончилось, в покупках"
    n = norm_amt(amt, unit) if amt is not None else None
    if not n:
        return f"{x['name']}: сколько осталось, не знаю (впиши количество в Доме)"
    v, u = n
    if x.get("amt") is None or (x.get("unit") or "шт") != u:
        return f"{x['name']}: в доме без количества в {u}, списать не могу. Скажи, сколько осталось"
    x["amt"] = max(0, round(float(x["amt"]) - v, 1))
    _relevel(x)
    return f"{x['name']}: осталось {fmt_amt(x)}" + (" (мало, в покупках)" if x["level"] == "low" else " (кончилось, в покупках)" if x["level"] == "out" else "")


def set_amt(h: dict, name: str, amt, unit) -> str:
    x = _find(h["items"], name)
    n = norm_amt(amt, unit)
    if not x or not n:
        return "не нашёл" if not x else "непонятное количество"
    x["amt"], x["unit"], x["u"] = n[0], n[1], now_ms()
    x.pop("qty", None)
    _relevel(x)
    return f"{x['name']}: {fmt_amt(x)}"


# --- действия (бот) ---

def add_items(h: dict, items: list[dict]) -> list[str]:
    """Купил / положил: добавляет или обновляет. Срок годности ставит по продукту и категории."""
    done = []
    for it in items:
        name = str(it.get("name") or "").strip()[:60]
        if not name:
            continue
        cat = it.get("cat") if it.get("cat") in CATS and it.get("cat") not in OLD_CATS else guess_cat(name)
        x = _find([y for y in h["items"] if y.get("cat") == cat], name)
        until = until_for(name, cat, it.get("days"))
        if x:
            x.update({"level": "ok", "u": now_ms()})
            if until:
                x["until"] = until
        else:
            x = {"id": new_id("i"), "name": name, "cat": cat, "level": "ok", "u": now_ms()}
            if until:
                x["until"] = until
            h["items"].append(x)
        n = norm_amt(it.get("amt"), it.get("unit")) if it.get("amt") is not None else None
        if n:
            if x.get("amt") is not None and (x.get("unit") or "шт") == n[1] and x.get("level") != "out":
                x["amt"] = round(float(x["amt"]) + n[0], 1)
            else:
                x["amt"], x["unit"] = n[0], n[1]
            x.pop("qty", None)
            _relevel(x)
        elif it.get("qty"):
            x["qty"] = str(it["qty"])[:20]
        # купленное убираем из ручного списка покупок
        s = _find([y for y in h["shop"] if not y.get("done")], name)
        if s:
            s.update({"done": True, "u": now_ms()})
        done.append(name + (f" (до {until[8:10]}.{until[5:7]})" if until else ""))
    return done


# --- заготовки: блюдо с порциями ---

def prep_add(h: dict, name: str, portions: int, days: int | None = None) -> str:
    name = str(name or "").strip()[:60] or "Заготовка"
    n = max(1, min(int(portions or 1), 30))
    x = _find([y for y in h["items"] if y.get("cat") == "prep" and y.get("level") != "out"], name)
    until = until_for(name, "prep", days or 4)
    if x and x.get("name", "").lower() == name.lower():
        x.update({"n": int(x.get("n") or 0) + n, "until": until, "level": "ok", "u": now_ms()})
    else:
        x = {"id": new_id("i"), "name": name, "cat": "prep", "n": n, "until": until, "level": "ok", "u": now_ms()}
        h["items"].append(x)
    return f"заготовка: {x['name']}, {x['n']} порц., годна до {until[8:10]}.{until[5:7]}"


def prep_eat(h: dict, name: str, k: int = 1) -> str:
    live = [y for y in h["items"] if y.get("cat") == "prep" and y.get("level") != "out" and int(y.get("n") or 0) > 0]
    x = _find(live, name) if name else (sorted(live, key=lambda y: y.get("until") or "9")[0] if live else None)
    if not x:
        return "заготовок нет" if not live else "не нашёл такую заготовку"
    x["n"] = max(0, int(x.get("n") or 0) - max(1, int(k or 1)))
    x["u"] = now_ms()
    if x["n"] == 0:  # съедено: заготовка просто исчезает (в покупки её не кладём)
        h["items"] = [y for y in h["items"] if y is not x]
        h["del"].append(x["id"])
        return f"{x['name']}: съедено всё, заготовок этого больше нет"
    return f"{x['name']}: осталось {x['n']} порц."


def project_new(h: dict, name: str, due: str | None = None) -> str:
    name = str(name or "").strip()[:60]
    if not name:
        return "нужно название"
    p = {"id": new_id("p"), "name": name, "steps": [], "u": now_ms()}
    if due:
        p["due"] = str(due)[:10]
    h["projects"].append(p)
    return f"проект «{name}» заведён" + (f", срок {due}" if due else "")


def set_level(h: dict, name: str, level: str) -> str:
    x = _find(h["items"], name)
    if not x:
        if level == "ok":
            return "добавлено: " + ", ".join(add_items(h, [{"name": name}]))
        x = {"id": new_id("i"), "name": name[:60], "cat": guess_cat(name), "level": level, "u": now_ms()}
        h["items"].append(x)
        return f"{name}: {LEVELS[level]} (новая позиция: {CATS[x['cat']].lower()})"
    if level == "ok" and x.get("cat") != "prep":
        u = until_for(x["name"], x.get("cat"))
        if u:
            x["until"] = u
    x.update({"level": level, "u": now_ms()})
    return f"{x['name']}: {LEVELS[level]}"


def remove_item(h: dict, name: str) -> str:
    x = _find(h["items"], name)
    if not x:
        return "не нашёл"
    h["items"] = [y for y in h["items"] if y is not x]
    h["del"].append(x["id"])
    return f"убрал {x['name']}"


def shop_add(h: dict, names: list[str]) -> str:
    added = []
    for n in names:
        n = str(n).strip()[:60]
        if n and not _find([y for y in h["shop"] if not y.get("done")], n):
            h["shop"].append({"id": new_id("s"), "name": n, "done": False, "u": now_ms()})
            added.append(n)
    return "в список покупок: " + ", ".join(added) if added else "уже в списке"


def chore_done(h: dict, name: str, day: str | None = None) -> dict | None:
    """Сделано: отсчёт заново, дело снова общее (до следующего раза)."""
    c = _find(h["chores"], name)
    if c:
        c.update({"last": day or quest.today_key(), "u": now_ms()})
        c.pop("who", None)
    return c


def is_me(who, uid) -> bool:
    return who is not None and (str(who) == str(uid) or str(who) == "me")


def my_buys(h: dict, uid) -> list[tuple[str, dict]]:
    out = [("item", x) for x in h.get("items", []) if x.get("level") in ("low", "out") and is_me(x.get("who"), uid)]
    out += [("shop", x) for x in h.get("shop", []) if not x.get("done") and is_me(x.get("who"), uid)]
    return out


def mark_bought(h: dict, kind: str, x: dict) -> None:
    if kind == "item":
        x["level"] = "ok"
        u = until_for(x["name"], x.get("cat"))
        if u:
            x["until"] = u
    else:
        x["done"] = True
        add_items(h, [{"name": x["name"]}])
    x.pop("who", None)
    x["u"] = now_ms()


def take(h: dict, what: str, uid, give: bool = False) -> str:
    """Взять на себя (или отдать обратно): домашнее дело, покупку или все покупки."""
    w = str(what or "").strip().lower()
    if w in ("покупки", "все покупки", "всё", "shop", "all"):
        n = 0
        for kind, x in [("item", y) for y in h["items"] if y.get("level") in ("low", "out")] + [("shop", y) for y in h["shop"] if not y.get("done")]:
            if give and is_me(x.get("who"), uid):
                x.pop("who", None); x["u"] = now_ms(); n += 1
            elif not give and x.get("who") is None:
                x["who"] = uid; x["u"] = now_ms(); n += 1
        return (f"отдал {n} покупок" if give else f"взял на себя {n} покупок, в плане появится «Купить: …»") if n else "нечего брать"
    x = _find(h["chores"], what) or _find([y for y in h["items"] if y.get("level") in ("low", "out")], what) \
        or _find([y for y in h["shop"] if not y.get("done")], what)
    if not x:
        for p in h.get("projects", []):
            st = _find([dict(y, name=y["t"]) for y in p.get("steps", []) if not y.get("done")], what)
            if st:
                x = next(y for y in p["steps"] if y["id"] == st["id"])
                x["name"] = x["t"]
                break
    if not x:
        return "не нашёл такое дело, покупку или шаг проекта"
    if give:
        x.pop("who", None)
    else:
        x["who"] = uid
    x["u"] = now_ms()
    nm = x.pop("name") if "t" in x else x["name"]
    return f"{'отдал' if give else 'взял на себя'}: {nm}" + ("" if give else ", дело в плане")


def step_done(h: dict, text: str, done: bool = True) -> str:
    for p in h["projects"]:
        s = _find([{"id": x["id"], "name": x["t"], **x} for x in p.get("steps", [])], text)
        if s:
            real = next(x for x in p["steps"] if x["id"] == s["id"])
            real.update({"done": done, "u": now_ms()})
            real.pop("who", None)
            return f"{p['name']}: «{real['t']}» {'сделано' if done else 'снова открыто'}"
    return "не нашёл такой шаг"


def summary(h: dict) -> str:
    today = quest.today_key()
    lines = []
    for cat, title in CATS.items():
        its = [x for x in h.get("items", []) if x.get("cat") == cat]
        if its:
            lines.append(f"{title}: " + "; ".join(
                x["name"] + (f" {x['n']} порц." if cat == "prep" else "") + (f" {fmt_amt(x)}" if fmt_amt(x) else "") + (f" [{LEVELS.get(x.get('level'), '')}]" if x.get("level") != "ok" else "")
                + (f" до {x['until'][8:10]}.{x['until'][5:7]}" if x.get("until") and x.get("level") != "out" else "") for x in its))
    exp = expiring(h, today)
    if exp:
        lines.append("СКОРО ИСПОРТИТСЯ: " + ", ".join(x["name"] for x in exp))
    sl = shop_list(h)
    if sl:
        lines.append("СПИСОК ПОКУПОК: " + ", ".join(sl))
    dc = due_chores(h, today)
    lines.append("БЫТ (дела с повтором; берут на себя, тогда попадает в план): " + "; ".join(
        f"{c['name']} (раз в {c['every']} дн, {'ПОРА' if c in dc else 'было ' + (c.get('last') or 'никогда')}"
        + (f", взял {c['who']}" if c.get("who") is not None else (", никто не взял" if c in dc else "")) + ")" for c in h.get("chores", [])))
    for p in h.get("projects", []):
        left = [s["t"] + (" [взял " + str(s["who"]) + "]" if s.get("who") is not None else "") for s in p.get("steps", []) if not s.get("done")]
        if left:
            lines.append(f"ПРОЕКТ {p['name']}" + (f" (срок {p['due']})" if p.get("due") else "") + ": осталось " + "; ".join(left))
    if h.get("notes"):
        lines.append("ПРАВИЛА ДОМА (общие для всех участников): " + "; ".join(n["t"] for n in h["notes"]))
    return "\n".join(lines) or "Дом пока пустой."


def chore_queue(state: dict) -> list[dict]:
    """Дела по дому, которые подошли: в план дня как обычные гибкие дела (id ch_<id>)."""
    h = state.get("house")
    if not isinstance(h, dict):
        return []
    today = quest.today_key()
    return [{"id": "ch_" + c["id"], "name": c["name"], "dur": int(c.get("dur") or 20)} for c in due_chores(h, today)]
