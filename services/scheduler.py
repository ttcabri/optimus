import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
from config import TELEGRAM_USER_ID, TIMEZONE


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    scheduler.add_job(send_checkin, "cron", hour=16, minute=30, args=[bot])
    scheduler.add_job(
        send_weekly_review, "cron", day_of_week="sun", hour=19, minute=0, args=[bot]
    )
    scheduler.add_job(
        send_weekly_focus, "cron", day_of_week="mon", hour=8, minute=0, args=[bot]
    )

    return scheduler


async def send_checkin(bot: Bot) -> None:
    await database.ensure_today_log(TELEGRAM_USER_ID)
    await bot.send_message(
        TELEGRAM_USER_ID,
        "Что конкретно сделал сегодня для первого клиента?",
    )


async def send_weekly_review(bot: Bot) -> None:
    week_logs = await database.get_week_logs(TELEGRAM_USER_ID)
    days_with_action = sum(1 for log in week_logs if log["what_done"])
    blocked_days = [log["date"] for log in week_logs if log["what_blocked"]]

    lines = [
        "Воскресный разбор недели:",
        f"Дней с действием по главной цели: {days_with_action}/7",
    ]
    if blocked_days:
        lines.append(f"Дни с отмазками: {', '.join(blocked_days)}")

    await bot.send_message(TELEGRAM_USER_ID, "\n".join(lines))


async def send_weekly_focus(bot: Bot) -> None:
    user = await database.get_user(TELEGRAM_USER_ID)
    main_goal = user["main_goal"] if user else "не задана"
    await bot.send_message(
        TELEGRAM_USER_ID,
        f"Новая неделя. Главная цель: {main_goal}. Что делаешь сегодня для неё?",
    )
