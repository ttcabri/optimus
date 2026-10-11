"""Дом: холодильник, припасы, бытовое, покупки, дела по дому, проекты (переезд).

Хранится как отдельное «хозяйство» с участниками (members), чтобы потом добавить семью.
Каждая запись имеет id и u (время изменения, мс). Удалённые id лежат в del.
Слияние приложение/бот: по id побеждает больший u, удалённое не воскресает.
Та же логика есть в webapp/index.html.
"""
import time
from datetime import datetime, timedelta

from services import quest

CATS = {"fridge": "Холодильник", "pantry": "Припасы", "home": "Бытовое"}
LEVELS = {"ok": "есть", "low": "мало", "out": "нет"}

# Сколько дней живёт в холодильнике после покупки (по умолчанию 5)
SHELF = [("фарш", 1), ("рыба", 1), ("курин", 2), ("курица", 2), ("мясо", 3), ("говяд", 3), ("свинин", 3),
         ("заготов", 3), ("мил-преп", 3), ("молоко", 5), ("кефир", 5), ("творог", 5), ("сметан", 7),
         ("йогурт", 7), ("ветчин", 5), ("колбас", 7), ("сыр", 14), ("яйц", 21), ("масло слив", 21),
         ("зелень", 4), ("салат", 3), ("овощ", 5), ("помидор", 5), ("огур", 5), ("фрукт", 6), ("банан", 4)]

DEFAULT_CHORES = [
    {"id": "c_wash", "name": "Стирка", "every": 7, "dur": 20},
    {"id": "c_clean", "name": "Уборка комнаты", "every": 7, "dur": 40},
    {"id": "c_trash", "name": "Вынести мусор", "every": 3, "dur": 5},
    {"id": "c_bed", "name": "Сменить постельное", "every": 14, "dur": 15},
]
MOVE_STEPS = ["Найти студию или квартиру с отдельной кухней", "Подписать договор аренды",
              "Договориться с хозяином о сдаче комнаты", "Сдать комнату и забрать залог €500",
              "Переписать адрес в BRP (gemeente)", "Новый адрес: банк, страховка, UWV, RDW (Audi)"]


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
    if any(k in n for k, _ in SHELF):
        return "fridge"
    return "pantry"


def shelf_days(name: str) -> int:
    n = name.lower()
    for k, d in SHELF:
        if k in n:
            return d
    return 5


def get(state: dict, uid: int | None = None) -> dict:
    """Дом из состояния; при первом обращении создаётся с делами по умолчанию и проектом переезда."""
    h = state.get("house")
    if not isinstance(h, dict):
        h = {"id": f"house_{uid or 'me'}", "owner": uid,
             "members": [{"uid": uid, "name": "Андрей", "role": "owner", "u": 0}] if uid else [],
             "roles": [dict(r, u=0) for r in DEFAULT_ROLES], "items": [], "shop": [],
             # отсчёт с сегодня, чтобы в первый день не вывалилось всё сразу
             "chores": [dict(c, last=quest.today_key(), u=0) for c in DEFAULT_CHORES],
             "projects": [{"id": "p_move", "name": "Переезд до 31.12", "due": "2026-12-31", "u": 0,
                           "steps": [{"id": f"s{i + 1}", "t": t, "done": False, "u": 0} for i, t in enumerate(MOVE_STEPS)]}],
             "del": []}
        state["house"] = h
    for k in ("items", "shop", "chores", "projects", "del", "notes"):
        h.setdefault(k, [])
    h.setdefault("roles", [dict(r, u=0) for r in DEFAULT_ROLES])
    # старый формат участников (просто id) -> записи с должностью
    h["members"] = [m if isinstance(m, dict) else {"uid": m, "name": "Андрей", "role": "owner", "u": 0} for m in h.get("members", [])]
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
    return [x for x in h.get("items", []) if x.get("cat") == "fridge" and x.get("level") != "out"
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


# --- действия (бот) ---

def add_items(h: dict, items: list[dict]) -> list[str]:
    """Купил / положил: добавляет или обновляет. Для холодильника ставит срок."""
    today = quest.today_key()
    done = []
    for it in items:
        name = str(it.get("name") or "").strip()[:60]
        if not name:
            continue
        cat = it.get("cat") if it.get("cat") in CATS else guess_cat(name)
        x = _find([y for y in h["items"] if y.get("cat") == cat], name)
        days = it.get("days")
        until = None
        if cat == "fridge":
            days = int(days) if days else shelf_days(name)
            until = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
        if x:
            x.update({"level": "ok", "u": now_ms()})
            if until:
                x["until"] = until
            if it.get("qty"):
                x["qty"] = str(it["qty"])[:20]
        else:
            x = {"id": new_id("i"), "name": name, "cat": cat, "level": "ok", "u": now_ms()}
            if until:
                x["until"] = until
            if it.get("qty"):
                x["qty"] = str(it["qty"])[:20]
            h["items"].append(x)
        # купленное убираем из ручного списка покупок
        s = _find([y for y in h["shop"] if not y.get("done")], name)
        if s:
            s.update({"done": True, "u": now_ms()})
        done.append(name + (f" (до {until[8:10]}.{until[5:7]})" if until else ""))
    return done


def set_level(h: dict, name: str, level: str) -> str:
    x = _find(h["items"], name)
    if not x:
        if level == "ok":
            return "добавлено: " + ", ".join(add_items(h, [{"name": name}]))
        x = {"id": new_id("i"), "name": name[:60], "cat": guess_cat(name), "level": level, "u": now_ms()}
        h["items"].append(x)
        return f"{name}: {LEVELS[level]} (новая позиция: {CATS[x['cat']].lower()})"
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
    c = _find(h["chores"], name)
    if c:
        c.update({"last": day or quest.today_key(), "u": now_ms()})
    return c


def step_done(h: dict, text: str, done: bool = True) -> str:
    for p in h["projects"]:
        s = _find([{"id": x["id"], "name": x["t"], **x} for x in p.get("steps", [])], text)
        if s:
            real = next(x for x in p["steps"] if x["id"] == s["id"])
            real.update({"done": done, "u": now_ms()})
            return f"{p['name']}: «{real['t']}» {'сделано' if done else 'снова открыто'}"
    return "не нашёл такой шаг"


def summary(h: dict) -> str:
    today = quest.today_key()
    lines = []
    for cat, title in CATS.items():
        its = [x for x in h.get("items", []) if x.get("cat") == cat]
        if its:
            lines.append(f"{title}: " + "; ".join(
                x["name"] + (f" {x['qty']}" if x.get("qty") else "") + (f" [{LEVELS.get(x.get('level'), '')}]" if x.get("level") != "ok" else "")
                + (f" до {x['until'][8:10]}.{x['until'][5:7]}" if x.get("until") and x.get("level") != "out" else "") for x in its))
    exp = expiring(h, today)
    if exp:
        lines.append("СКОРО ИСПОРТИТСЯ: " + ", ".join(x["name"] for x in exp))
    sl = shop_list(h)
    if sl:
        lines.append("СПИСОК ПОКУПОК: " + ", ".join(sl))
    dc = due_chores(h, today)
    lines.append("ДЕЛА ПО ДОМУ: " + "; ".join(
        f"{c['name']} (раз в {c['every']} дн, {'пора' if c in dc else 'было ' + (c.get('last') or 'никогда')})" for c in h.get("chores", [])))
    for p in h.get("projects", []):
        left = [s["t"] for s in p.get("steps", []) if not s.get("done")]
        if left:
            lines.append(f"ПРОЕКТ {p['name']}: осталось " + "; ".join(left))
    if h.get("notes"):
        lines.append("ПАМЯТЬ ДОМА (общая для всех участников): " + "; ".join(n["t"] for n in h["notes"]))
    return "\n".join(lines) or "Дом пока пустой."


def chore_queue(state: dict) -> list[dict]:
    """Дела по дому, которые подошли: в план дня как обычные гибкие дела (id ch_<id>)."""
    h = state.get("house")
    if not isinstance(h, dict):
        return []
    today = quest.today_key()
    return [{"id": "ch_" + c["id"], "name": c["name"], "dur": int(c.get("dur") or 20)} for c in due_chores(h, today)]
