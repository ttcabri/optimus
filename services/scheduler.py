import logging
from datetime import date

import pytz
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
from config import TELEGRAM_USER_ID, TIMEZONE
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

    return scheduler
