import logging
from datetime import date

import pytz
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
from config import TELEGRAM_USER_ID, TIMEZONE
from services import quest
from services.claude import get_checkin_opener, get_evening_reminder, get_morning_push

logger = logging.getLogger(__name__)


async def send_morning_push(bot: Bot) -> None:
    """08:00 — утренний пуш с задачей дня."""
    try:
        text = await get_morning_push(TELEGRAM_USER_ID)
        await bot.send_message(TELEGRAM_USER_ID, f"☀️ {text}")
    except Exception as e:
        logger.error(f"Morning push error: {e}")


async def send_checkin(bot: Bot) -> None:
    """16:30 — основной ежедневный check-in."""
    try:
        text = await get_checkin_opener(TELEGRAM_USER_ID)
        await bot.send_message(TELEGRAM_USER_ID, f"🎯 {text}")
    except Exception as e:
        logger.error(f"Check-in error: {e}")


async def send_evening_reminder(bot: Bot) -> None:
    """21:00 — напоминание если check-in не сделан."""
    try:
        today_log = await database.get_today_log(TELEGRAM_USER_ID)
        if today_log is None or not today_log["checkin_done"]:
            text = await get_evening_reminder(TELEGRAM_USER_ID)
            await bot.send_message(TELEGRAM_USER_ID, f"⚠️ {text}")
    except Exception as e:
        logger.error(f"Evening reminder error: {e}")


async def send_weekly_review(bot: Bot) -> None:
    """Воскресенье 19:00 — недельный разбор."""
    try:
        logs = await database.get_week_logs(TELEGRAM_USER_ID)
        total = len(logs)
        done_days = sum(1 for l in logs if l["what_done"])
        missed_days = total - done_days

        actions = [l["tomorrow_action"] for l in logs if l["tomorrow_action"]]
        actions_str = "\n".join(f"• {a}" for a in actions) if actions else "не было обещаний"

        text = (
            f"📊 <b>Итог недели</b>\n\n"
            f"Дней с действием: {done_days}/7\n"
            f"Дней без действия: {missed_days}\n\n"
            f"Обещания за неделю:\n{actions_str}\n\n"
            f"{'Хорошая неделя. Продолжай.' if done_days >= 5 else 'Слабая неделя. Что происходит?' if done_days < 3 else 'Средне. Можешь лучше.'}"
        )
        await bot.send_message(TELEGRAM_USER_ID, text)
    except Exception as e:
        logger.error(f"Weekly review error: {e}")


async def send_monday_focus(bot: Bot) -> None:
    """Понедельник 08:30 — фокус на неделю."""
    try:
        user = await database.get_user(TELEGRAM_USER_ID)
        goal = user["main_goal"] if user else "первый клиент онлайн"
        text = f"🗓 Новая неделя.\nГлавная цель: <b>{goal}</b>\n\nОдно действие сегодня — что это будет?"
        await bot.send_message(TELEGRAM_USER_ID, text)
    except Exception as e:
        logger.error(f"Monday focus error: {e}")


# --- Квест дня: напоминания приходят только если квест ещё не закрыт ---

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


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)

    # Утренний пуш — каждый день 08:00
    scheduler.add_job(
        send_morning_push, "cron",
        hour=8, minute=0,
        kwargs={"bot": bot}
    )

    # Понедельничный фокус — 08:30
    scheduler.add_job(
        send_monday_focus, "cron",
        day_of_week="mon", hour=8, minute=30,
        kwargs={"bot": bot}
    )

    # Основной check-in — каждый день 16:30
    scheduler.add_job(
        send_checkin, "cron",
        hour=16, minute=30,
        kwargs={"bot": bot}
    )

    # Вечернее напоминание — каждый день 21:00 (только если не было check-in)
    scheduler.add_job(
        send_evening_reminder, "cron",
        hour=21, minute=0,
        kwargs={"bot": bot}
    )

    # Воскресный разбор — 19:00
    scheduler.add_job(
        send_weekly_review, "cron",
        day_of_week="sun", hour=19, minute=0,
        kwargs={"bot": bot}
    )

    # Квест дня: живой план, проверка каждые 5 минут
    scheduler.add_job(send_quest_tick, "cron", minute="*/5", kwargs={"bot": bot})
    scheduler.add_job(send_quest_night, "cron", hour=22, minute=30, kwargs={"bot": bot})
    scheduler.add_job(send_weigh_in, "cron", day_of_week="mon", hour=8, minute=10, kwargs={"bot": bot})

    return scheduler
