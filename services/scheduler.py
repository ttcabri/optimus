import html
import logging
from datetime import date, timedelta

import pytz
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
from config import TELEGRAM_USER_ID, TIMEZONE
from services import quest

logger = logging.getLogger(__name__)


# --- Квест дня: напоминания по живому плану ---

HINTS = {
    "cbr": "TheorieExamen.nl. Скрин результата сюда.",
    "video": "Открой квест и жми «Начать фокус». Телефон экраном вниз.",
    "gym": "До зала без травы. Селфи из зала сюда.",
    "fight": "До тренировки без травы. Фото сюда.",
    "eng": "Duolingo, урок или два. Скрин сюда.",
    "vid2": "Ссылку на второе видео сюда.",
    "fine": "Переведи €5 в safemoney и кинь скрин Trading 212.",
}
_sent: set[str] = set()


def quest_messages(day: dict, now_min: int, today: str, wd: int) -> list[str]:
    """Что сказать сейчас по живому плану. Каждое сообщение один раз за день."""
    out = []
    P = quest.prof(day)
    if now_min < quest.to_min(P["wake"]) or now_min > quest.to_min(quest.day_end(P)):
        return out

    def once(tag, text):
        k = f"{today}:{tag}"
        if k not in _sent:
            _sent.add(k)
            out.append(text)

    if "wake" not in day.get("done", []) and quest.to_min(P["wake"]) + 15 <= now_min < quest.to_min("12:00"):
        once("wake", "☀️ Подъём. Отметь в квесте, и день пересчитается от сейчас.")
    # отдых закончился: сразу говорим, что дальше
    for start, mins in quest.rests_of(day):
        end = start + mins
        if end <= now_min < end + 6:
            nxt = next((it for it in quest.build_plan(day, now_min, wd)["items"] if not it.get("game") and not it.get("gap")), None)
            once(f"rest:{start}", "⏰ Отдых всё." + (f" Пора: <b>{nxt['name']}</b>, до {quest.hm(nxt['end'])}." if nxt else ""))
            if nxt:
                _sent.add(f"{today}:start:{nxt['id']}")
    rest_total = sum(min(m, max(0, now_min - s)) for s, m in quest.rests_of(day))
    if rest_total >= 150:
        once("rest-total", f"🛑 Отдыха сегодня уже {rest_total // 60} ч {rest_total % 60} мин. Хватит, возвращайся к плану.")
    p = quest.build_plan(day, now_min, wd)
    first = next((it for it in p["items"] if not it.get("game")), None)
    if first and first.get("started") and first.get("late", 0) >= 10:
        once(f"late:{first['id']}", f"⏱ «{first['name']}» идёт на {first['late']} мин дольше плана. Закрывай или режь лишнее, иначе вечер уедет.")
    elif first and not first.get("started") and not first.get("minor") and first["start"] <= now_min + 2:
        hint = HINTS.get(first["id"], "")
        if first["id"] == "cbr":
            from services import theory
            hint = f"Сегодня: {theory.stage_name(day.get('_theory_i', 0))}. Напиши «теория», расскажу что учить."
        once(f"start:{first['id']}", f"▶️ Пора: <b>{first['name']}</b>, до {quest.hm(first['end'])}. {hint}".strip())
    if p["over"]:
        names = ", ".join(o["name"] for o in p["over"])
        once("over:" + names, f"⚠️ До отбоя не влезает: {names}. Открой квест и реши: сократить, убрать или на завтра.")
    return out


async def send_quest_tick(bot: Bot) -> None:
    """Каждые 5 минут: сверка с живым планом."""
    try:
        day = await quest.get_day(TELEGRAM_USER_ID)
        n = quest.now()
        msgs = quest_messages(day, n.hour * 60 + n.minute, quest.today_key(), quest.js_weekday())
        if msgs:
            from handlers.proof import quest_keyboard
            for m in msgs:
                await bot.send_message(TELEGRAM_USER_ID, m, reply_markup=quest_keyboard())
    except Exception as e:
        logger.error(f"Quest tick error: {e}")


async def send_reminders(bot: Bot) -> None:
    """Каждую минуту: напоминания, которые поставил помощник."""
    try:
        state = await database.get_quest_state(TELEGRAM_USER_ID)
        rem = state.get("reminders") or []
        today, now_t = quest.today_key(), quest.hhmm()
        due = [r for r in rem if not r.get("sent") and (r.get("d", "") < today or (r.get("d") == today and r.get("at", "99") <= now_t))]
        if not due:
            return
        for r in due:
            r["sent"] = True
        fresh = await database.get_quest_state(TELEGRAM_USER_ID)
        fresh["reminders"] = rem
        await database.save_quest_state(TELEGRAM_USER_ID, fresh)
        from handlers.proof import quest_keyboard
        for r in due:
            await bot.send_message(TELEGRAM_USER_ID, f"⏰ <b>Напоминание</b>\n{html.escape(r.get('text', ''))}", reply_markup=quest_keyboard())
    except Exception as e:
        logger.error(f"Reminders error: {e}")


async def send_quest_night(bot: Bot) -> None:
    """22:30 — итог дня и экран выкл."""
    try:
        day = await quest.get_day(TELEGRAM_USER_ID)
        text = (
            "🌙 <b>22:30. Итог дня</b>\n\n" + quest.day_summary(day)
            + ("" if quest.boss_done(day) else "\n\n⚠️ Видео сегодня не вышло. Завтра штрафной день: два видео и €5 в safemoney. Серия обнулится.")
            + "\n\nЧто завтра, кроме основы? Напиши так:\n<code>завтра: отвезти Audi; письмо в UWV</code>"
            + "\n\nЭкран выкл, 15 минут повтор теории и спать в 23:30."
        )
        await bot.send_message(TELEGRAM_USER_ID, text)
    except Exception as e:
        logger.error(f"Quest night error: {e}")


async def send_weigh_in(bot: Bot) -> None:
    """Понедельник 08:10 — взвешивание."""
    try:
        await bot.send_message(TELEGRAM_USER_ID, "⚖️ Понедельник. Встань на весы до завтрака и напиши: <code>вес 82.5</code>")
    except Exception as e:
        logger.error(f"Weigh-in error: {e}")


async def send_quest_week(bot: Bot) -> None:
    """Воскресенье 21:00 — итог недели по квесту."""
    try:
        state = await database.get_quest_state(TELEGRAM_USER_ID)
        all_days = state.get("days", {})
        today = quest.now().date()
        keys = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        days = [all_days.get(k) or {} for k in keys]
        vids = sum(1 for d in days if quest.boss_done(d))
        gym = sum(1 for d in days if "gym" in d.get("done", []))
        cbr = sum(1 for d in days if "cbr" in d.get("done", []))
        eng = sum(1 for d in days if "eng" in d.get("done", []))
        streak, i = 0, (0 if quest.boss_done(days[0]) else 1)
        while quest.boss_done(all_days.get((today - timedelta(days=i)).strftime("%Y-%m-%d"))):
            streak += 1
            i += 1
        verdict = ("Сильная неделя. Так держать." if vids >= 6 else
                   "Нормально, но можно жёстче." if vids >= 4 else
                   "Слабая неделя. Видео — главное, начни завтра с него.")
        text = (f"📊 <b>Неделя в квесте</b>\n\nВидео: {vids}/7\nЗал: {gym}\nТеория: {cbr}/7\nDuolingo: {eng}/7\n"
                f"Серия сейчас: {streak} дн.\n\n{verdict}")
        await bot.send_message(TELEGRAM_USER_ID, text)
    except Exception as e:
        logger.error(f"Quest week error: {e}")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)

    # Квест дня: живой план, проверка каждые 5 минут
    scheduler.add_job(send_quest_tick, "cron", minute="*/5", kwargs={"bot": bot})
    scheduler.add_job(send_reminders, "cron", minute="*", kwargs={"bot": bot})
    scheduler.add_job(send_quest_night, "cron", hour=22, minute=30, kwargs={"bot": bot})
    scheduler.add_job(send_quest_week, "cron", day_of_week="sun", hour=21, minute=0, kwargs={"bot": bot})
    scheduler.add_job(send_weigh_in, "cron", day_of_week="mon", hour=8, minute=10, kwargs={"bot": bot})

    return scheduler
