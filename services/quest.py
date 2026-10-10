"""Квест дня: общая логика для Mini App, пруфов и напоминаний.

Состояние хранится одним JSON на пользователя: {"days": {"YYYY-MM-DD": {"done": [...], "games": 0, "smokes": 0}}}
Формат совпадает с webapp/index.html.
"""
from datetime import datetime

import pytz

import database
from config import TIMEZONE

BOSS_IDS = ["b1", "b2", "b3", "b4", "b5"]
TOKEN_IDS = ["cbr", "gym", "fight", "eng"]
# Квесты, которые закрываются только пруфом через бота
PROOF_IDS = {"cbr", "gym", "fight", "b5"}

QUEST_NAMES = {
    "wake": "Подъём в 8:00",
    "sleep": "Отбой до 23:30",
    "bfast": "Завтрак",
    "cbr": "Теория CBR",
    "lunch": "Обед",
    "gym": "Зал",
    "fight": "Единоборства",
    "walk": "Прогулка",
    "dinner": "Ужин",
    "eng": "Английский",
    "house": "Жильё",
    "screen": "Экран выкл",
}


def now() -> datetime:
    return datetime.now(pytz.timezone(TIMEZONE))


def today_key() -> str:
    return now().strftime("%Y-%m-%d")


def training_id() -> str:
    """Пн, вт, чт, пт = зал. Ср, сб = единоборства. Вс = зал (если всё же пошёл)."""
    return "fight" if now().weekday() in (2, 5) else "gym"


def boss_done(day: dict | None) -> bool:
    return bool(day) and all(b in day.get("done", []) for b in BOSS_IDS)


def tokens(day: dict) -> int:
    n = sum(1 for q in TOKEN_IDS if q in day.get("done", []))
    if boss_done(day):
        return n + 2
    return min(n, 1)


async def get_day(telegram_id: int) -> dict:
    state = await database.get_quest_state(telegram_id)
    return state["days"].get(today_key(), {"done": []})


async def mark_done(telegram_id: int, ids: list[str]) -> dict:
    """Отмечает квесты сегодняшнего дня. Возвращает обновлённый день."""
    state = await database.get_quest_state(telegram_id)
    day = state["days"].setdefault(today_key(), {"done": []})
    day.setdefault("done", [])
    for i in ids:
        if i not in day["done"]:
            day["done"].append(i)
    await database.save_quest_state(telegram_id, state)
    return day


def merge_incoming(server: dict, incoming: dict) -> dict:
    """Mini App шлёт полное состояние. Пруф-квесты, которые засчитал бот, не теряем,
    даже если в приложении была старая версия."""
    if not isinstance(incoming, dict) or not isinstance(incoming.get("days"), dict):
        return server
    for key, sday in server.get("days", {}).items():
        iday = incoming["days"].setdefault(key, {"done": []})
        iday.setdefault("done", [])
        for q in sday.get("done", []):
            if q in PROOF_IDS and q not in iday["done"]:
                iday["done"].append(q)
    # Пруф-квесты нельзя отметить из приложения вручную
    for key, iday in incoming["days"].items():
        sdone = server.get("days", {}).get(key, {}).get("done", [])
        iday["done"] = [q for q in iday.get("done", []) if q not in PROOF_IDS or q in sdone]
    return incoming


def day_summary(day: dict) -> str:
    done = day.get("done", [])
    lines = []
    lines.append("Видео: " + ("вышло ✅" if boss_done(day) else "не вышло ❌"))
    missed = [QUEST_NAMES[q] for q in ("cbr", "eng") if q not in done]
    trained = "gym" in done or "fight" in done
    if now().weekday() != 6:
        lines.append("Тренировка: " + ("да ✅" if trained else "нет ❌"))
    if missed:
        lines.append("Пропущено: " + ", ".join(missed))
    left = tokens(day) - day.get("games", 0)
    lines.append(f"Жетоны на Доту: {max(left, 0)}")
    if day.get("smokes"):
        first = day.get("firstSmoke")
        lines.append(f"Трава: {day['smokes']}" + (f", первый раз в {first}" if first else ""))
    return "\n".join(lines)
