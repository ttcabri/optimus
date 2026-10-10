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
    done = day.get("done", [])
    n = sum(1 for q in TOKEN_IDS if q in done)
    n = n + 2 if boss_done(day) else min(n, 1)
    if "vid2" in done:
        n += 2
    return n


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


async def add_custom(telegram_id: int, names: list[str], tomorrow: bool = False) -> list[str]:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, tomorrow_key() if tomorrow else today_key())
    added = []
    for i, name in enumerate(names):
        name = name.strip()[:80]
        if not name:
            continue
        day["custom"].append({"id": f"t{int(time.time() * 1000):x}{i}", "name": name})
        added.append(name)
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
    left = tokens(day) - day.get("games", 0)
    lines.append(f"Каток на Доту осталось: {max(left, 0)}")
    return "\n".join(lines)
