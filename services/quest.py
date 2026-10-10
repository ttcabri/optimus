"""Квест дня: общая логика для Mini App, пруфов и напоминаний.

Состояние: один JSON на пользователя, формат совпадает с webapp/index.html:
{"days": {"YYYY-MM-DD": {"done": [...], "at": {"id": "HH:MM"}, "games": 0, "smokes": 0,
                         "firstSmoke": "HH:MM", "custom": [{"id", "name"}], "del": [...]}},
 "body": [{"d": "YYYY-MM-DD", "kg": 82.5}], "lifts": [{"d", "t", "clean"}]}
"""
import time
from datetime import datetime, timedelta

import pytz

import database
from config import TIMEZONE

BOSS_IDS = ["b1", "b2", "b3", "b4", "b5"]
TOKEN_IDS = ["cbr", "gym", "fight", "eng"]
# Закрываются только пруфом через бота
PROOF_IDS = {"cbr", "gym", "fight", "b5", "vid2"}

QUEST_NAMES = {
    "cbr": "Теория CBR",
    "gym": "Зал",
    "fight": "Единоборства",
    "eng": "Английский",
    "house": "Жильё",
}


def now() -> datetime:
    return datetime.now(pytz.timezone(TIMEZONE))


def today_key() -> str:
    return now().strftime("%Y-%m-%d")


def tomorrow_key() -> str:
    return (now() + timedelta(days=1)).strftime("%Y-%m-%d")


def hhmm() -> str:
    return now().strftime("%H:%M")


def training_id(weekday: int | None = None) -> str | None:
    """Пн, вт, чт, пт = зал. Ср, сб = единоборства. Вс = отдых."""
    w = now().weekday() if weekday is None else weekday
    if w == 6:
        return None
    return "fight" if w in (2, 5) else "gym"


def _day(state: dict, key: str) -> dict:
    day = state["days"].setdefault(key, {})
    day.setdefault("done", [])
    day.setdefault("at", {})
    day.setdefault("custom", [])
    return day


def boss_done(day: dict | None) -> bool:
    return bool(day) and all(b in day.get("done", []) for b in BOSS_IDS)


def tokens(day: dict) -> int:
    """Дота открывается только после видео."""
    if not boss_done(day):
        return 0
    done = day.get("done", [])
    n = 2 + sum(1 for q in TOKEN_IDS if q in done)
    if "vid2" in done:
        n += 2
    return n


def games_used(day: dict) -> int:
    return max(day.get("games", 0), len(day.get("gl", [])))


def clean_state(day: dict | None, weekday: int) -> str:
    """base: тренировка до первой травы (в вс: видео). gold: и видео, и тренировка до травы."""
    if not day:
        return "pending"
    at = day.get("at", {})
    fs = day.get("firstSmoke")
    tr = training_id(weekday)
    t_tr = at.get(tr) if tr else None
    t_v = at.get("b5")

    def before(a, b):
        return bool(a) and (not b or a <= b)

    if before(t_v, fs) and (not tr or before(t_tr, fs)):
        return "gold"
    if (before(t_tr, fs) if tr else before(t_v, fs)):
        return "base"
    return "broken" if fs else "pending"


async def get_day(telegram_id: int) -> dict:
    state = await database.get_quest_state(telegram_id)
    return _day(state, today_key())


async def mark_done(telegram_id: int, ids: list[str]) -> dict:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, today_key())
    t = hhmm()
    for i in ids:
        if i not in day["done"]:
            day["done"].append(i)
            day["at"][i] = t
    await database.save_quest_state(telegram_id, state)
    return day


async def add_smoke(telegram_id: int) -> dict:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, today_key())
    if not day.get("smokes"):
        day["firstSmoke"] = hhmm()
    day["smokes"] = day.get("smokes", 0) + 1
    await database.save_quest_state(telegram_id, state)
    return day


def parse_task(text: str) -> tuple[str, str | None, int]:
    """«отвезти Audi 10:00 1ч» → ("отвезти Audi", "10:00", 60). Без длительности 30 мин."""
    import re
    t = " " + text.strip() + " "
    at = None
    m = re.search(r"(?:\sв)?\s(\d{1,2})[:.](\d{2})(?=\s)", t)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        at = f"{int(m.group(1)):02d}:{m.group(2)}"
        t = t[:m.start()] + " " + t[m.end():]
    dur = 30
    m = re.search(r"\s(\d+(?:[.,]\d+)?)\s*(ч|час|часа|часов|мин|м)(?=[\s.,]|$)", t)
    if m:
        v = float(m.group(1).replace(",", "."))
        dur = int(v * 60) if m.group(2).startswith("ч") else int(v)
        dur = max(5, min(dur, 480))
        t = t[:m.start()] + " " + t[m.end():]
    name = " ".join(t.split())[:80]
    return name, at, dur


async def add_custom(telegram_id: int, names: list[str], tomorrow: bool = False) -> list[str]:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, tomorrow_key() if tomorrow else today_key())
    added = []
    for i, raw in enumerate(names):
        name, at, dur = parse_task(raw)
        if not name:
            continue
        item = {"id": f"t{int(time.time() * 1000):x}{i}", "name": name, "dur": dur}
        if at:
            item["at"] = at
        day["custom"].append(item)
        added.append(name + (f" в {at}" if at else "") + f", {dur} мин")
    await database.save_quest_state(telegram_id, state)
    return added


async def log_weight(telegram_id: int, kg: float) -> list[dict]:
    state = await database.get_quest_state(telegram_id)
    body = [b for b in state.get("body", []) if b.get("d") != today_key()]
    body.append({"d": today_key(), "kg": round(kg, 1)})
    state["body"] = body
    await database.save_quest_state(telegram_id, state)
    return body


def merge_incoming(server: dict, incoming: dict) -> dict:
    """Mini App шлёт полное состояние. Не теряем то, что бот записал, пока приложение было открыто."""
    if not isinstance(incoming, dict) or not isinstance(incoming.get("days"), dict):
        return server
    incoming.setdefault("body", server.get("body", []))
    incoming.setdefault("lifts", server.get("lifts", []))
    s_days = server.get("days", {})

    for key, sday in s_days.items():
        iday = incoming["days"].setdefault(key, {})
        iday.setdefault("done", [])
        iday.setdefault("at", {})
        iday.setdefault("custom", [])
        # пруфы, засчитанные ботом
        for q in sday.get("done", []):
            if q in PROOF_IDS and q not in iday["done"]:
                iday["done"].append(q)
                if q in sday.get("at", {}):
                    iday["at"][q] = sday["at"][q]
        # трава, отмеченная в боте
        if sday.get("smokes", 0) > iday.get("smokes", 0):
            iday["smokes"] = sday["smokes"]
        fs = [t for t in (sday.get("firstSmoke"), iday.get("firstSmoke")) if t]
        if fs:
            iday["firstSmoke"] = min(fs)
        # дела, добавленные через бота
        have = {c.get("id") for c in iday["custom"]}
        deleted = set(iday.get("del", [])) | set(sday.get("del", []))
        for c in sday.get("custom", []):
            if c.get("id") not in have and c.get("id") not in deleted:
                iday["custom"].append(c)
        if deleted:
            iday["del"] = sorted(deleted)

    # вес, записанный в боте
    dates = {b.get("d") for b in incoming["body"]}
    for b in server.get("body", []):
        if b.get("d") not in dates:
            incoming["body"].append(b)
    incoming["body"].sort(key=lambda b: b.get("d", ""))

    # пруф-квесты нельзя отметить из приложения вручную
    for key, iday in incoming["days"].items():
        sdone = s_days.get(key, {}).get("done", [])
        iday["done"] = [q for q in iday.get("done", []) if q not in PROOF_IDS or q in sdone]
        iday["at"] = {q: t for q, t in iday.get("at", {}).items() if q in iday["done"]}
    return incoming


def day_summary(day: dict) -> str:
    done = day.get("done", [])
    w = now().weekday()
    lines = ["Видео: " + ("вышло ✅" if boss_done(day) else f"{sum(b in done for b in BOSS_IDS)} из 5 шагов ❌")]
    if "vid2" in done:
        lines.append("Второе видео: вышло 🔥")
    tr = training_id(w)
    if tr:
        lines.append("Тренировка: " + ("да ✅" if tr in done else "нет ❌"))
    missed = [QUEST_NAMES[q] for q in ("cbr", "eng") if q not in done]
    if missed:
        lines.append("Пропущено: " + ", ".join(missed))
    open_custom = [c["name"] for c in day.get("custom", []) if c["id"] not in done]
    if open_custom:
        lines.append("Не закрыто: " + "; ".join(open_custom))
    c = clean_state(day, w)
    lines.append({
        "gold": "Чистое утро: золотой день 🥇",
        "base": "Чистое утро: засчитано ✅",
        "broken": "Чистое утро: сорвано",
        "pending": "Чистое утро: без травы весь день ✅",
    }[c])
    if boss_done(day):
        left = max(tokens(day) - games_used(day), 0)
        lines.append(f"Каток на Доту не сыграно: {left}" + (f" (+{left * 10} XP)" if left else ""))
    else:
        lines.append("Дота сегодня закрыта: видео не вышло")
    return "\n".join(lines)


# --- Живой план (та же логика, что в webapp/index.html) ---

GAME_MIN = 45
UNTIL = {"wake": "12:00", "bfast": "12:00", "lunch": "16:00"}
DAY_START = "08:00"
DAY_END = "22:45"
# (id, название, минут, дни недели JS: 0 вс .. 6 сб или None, якорь, бытовое)
PLAN_QUESTS = [
    ("wake", "Подъём, вода, улица", 20, None, None, True),
    ("bfast", "Завтрак", 20, None, None, True),
    ("cbr", "Теория CBR", 45, None, None, False),
    ("video", "Видео", 110, None, None, False),
    ("lunch", "Обед", 30, None, None, True),
    ("gym", "Зал", 120, [1, 2, 4, 5], None, False),
    ("fight", "Единоборства", 120, [3, 6], None, False),
    ("walk", "Прогулка", 45, [0], None, False),
    ("_custom", "", 0, None, None, False),
    ("vid2", "Бонус: второе видео", 110, None, None, False),
    ("prep", "Заготовка еды", 80, [0, 3, 5], None, True),
    ("dinner", "Ужин", 30, [1, 2, 4, 6], None, True),
    ("eng", "Английский", 45, None, None, False),
    ("house", "Жильё", 20, [3, 0], None, False),
    ("screen", "Экран выкл, повтор CBR, душ", 45, None, DAY_END, True),
]


def to_min(t: str) -> int:
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


def hm(m: int) -> str:
    m = int(round(m))
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def js_weekday() -> int:
    return (now().weekday() + 1) % 7


def build_plan(day: dict, now_min: int | None = None, wd: int | None = None) -> dict:
    now_m = now_min if now_min is not None else now().hour * 60 + now().minute
    wd = js_weekday() if wd is None else wd
    done = set(day.get("done", []))
    skip = set(day.get("skip", []))
    dur_over = day.get("dur", {})

    def is_done(qid):
        return boss_done(day) if qid == "video" else qid in done

    def dur_of(qid, base):
        if qid in dur_over:
            return dur_over[qid]
        if qid == "video":
            left = sum(1 for b in BOSS_IDS if b not in done)
            return max(20, round(base * left / 5))
        return base

    queue = []
    for qid, name, dur, days, fixed, minor in PLAN_QUESTS:
        if days is not None and wd not in days:
            continue
        if qid == "_custom":
            for c in day.get("custom", []):
                queue.append({"id": c["id"], "name": c["name"], "dur": c.get("dur", 30), "fixed": c.get("at"), "minor": False})
            continue
        if qid == "vid2" and not boss_done(day):
            continue
        if qid in skip:
            continue
        if qid in UNTIL and now_m > to_min(UNTIL[qid]) and qid not in done:
            continue  # утро прошло: завтрак в вечер не тащим
        queue.append({"id": qid, "name": name, "dur": dur, "fixed": fixed, "minor": minor})

    items, over = [], []
    wake = day.get("at", {}).get("wake")
    cur = max(now_m, to_min(DAY_START)) if not wake else now_m
    for g in sorted(to_min(x) for x in day.get("gl", [])):
        if g + GAME_MIN > now_m:
            s = g if g <= now_m else max(g, cur)
            items.append({"game": True, "start": s, "end": s + GAME_MIN})
            cur = max(cur, s + GAME_MIN)

    pending = [q for q in queue if not is_done(q["id"])]
    started = day.get("cur")
    if started:
        idx = next((i for i, q in enumerate(pending) if q["id"] == started.get("id")), -1)
        if idx >= 0:
            q = pending.pop(idx)
            s = to_min(started["at"])
            e = s + dur_of(q["id"], q["dur"])
            items.append({**q, "start": s, "end": max(e, now_m + 5), "started": True, "late": max(0, now_m - e)})
            cur = max(cur, e, now_m + 5)

    anchors = sorted([q for q in pending if q["fixed"]], key=lambda q: to_min(q["fixed"]))
    flex = [q for q in pending if not q["fixed"]]
    end = to_min(DAY_END)

    def place(a):
        nonlocal cur
        s = max(to_min(a["fixed"]), cur)
        items.append({**a, "start": s, "end": s + dur_of(a["id"], a["dur"]), "anchor": True, "late": max(0, cur - to_min(a["fixed"]))})
        cur = s + dur_of(a["id"], a["dur"])

    for q in flex:
        du = dur_of(q["id"], q["dur"])
        while anchors and to_min(anchors[0]["fixed"]) < cur + du and anchors[0]["fixed"] != DAY_END:
            place(anchors.pop(0))
        if cur + du > end:
            over.append({**q, "dur": du})
            continue
        items.append({**q, "start": cur, "end": cur + du})
        cur += du
    while anchors:
        place(anchors.pop(0))
    return {"items": items, "over": over}
