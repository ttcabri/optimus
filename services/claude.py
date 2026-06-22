import datetime

from anthropic import AsyncAnthropic

import database
from config import ANTHROPIC_API_KEY
from prompts.system import SYSTEM_PROMPT

MODEL = "claude-haiku-4-5"

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


async def build_context(telegram_id: int) -> str:
    user = await database.get_user(telegram_id)
    today = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    yesterday_log = await database.get_log_by_date(telegram_id, yesterday)
    yesterday_commitment = (
        yesterday_log["tomorrow_action"] if yesterday_log and yesterday_log["tomorrow_action"] else "не указано"
    )

    streak = await database.get_streak(telegram_id)
    parked_ideas_count = await database.count_ideas(telegram_id)

    main_goal = user["main_goal"] if user else "не задана"

    return f"""
ТЕКУЩИЙ КОНТЕКСТ:
- Главная цель: {main_goal}
- Сегодня: {today}
- Вчера обещал: {yesterday_commitment}
- Streak дней с действием: {streak}
- Идеи в паркинге: {parked_ideas_count}
"""


async def get_response(telegram_id: int, user_message: str) -> str:
    await database.add_message(telegram_id, "user", user_message)

    history = await database.get_recent_messages(telegram_id, limit=20)
    messages = [{"role": row["role"], "content": row["content"]} for row in history]

    context = await build_context(telegram_id)

    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT + "\n\n" + context,
            messages=messages,
        )
        reply = response.content[0].text
    except Exception:
        reply = "Недоступен, напиши позже."
        return reply

    await database.add_message(telegram_id, "assistant", reply)
    return reply
