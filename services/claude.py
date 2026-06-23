from datetime import date, datetime, timedelta

import pytz
from anthropic import AsyncAnthropic

import database
from config import ANTHROPIC_API_KEY, TIMEZONE
from prompts.system import SYSTEM_PROMPT

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


async def build_context_block(telegram_id: int) -> str:
    """Строит динамический контекст о пользователе для системного промпта."""
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    now_str = now.strftime("%H:%M, %A %d %B %Y")

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    yesterday_log = await database.get_log_by_date(telegram_id, yesterday)
    today_log = await database.get_today_log(telegram_id)
    streak = await database.get_streak(telegram_id)
    user = await database.get_user(telegram_id)

    main_goal = user["main_goal"] if user else "не задана"

    # Вчерашние данные
    if yesterday_log:
        y_done = yesterday_log["what_done"] or "ничего не записано"
        y_blocked = yesterday_log["what_blocked"] or "—"
        y_tomorrow = yesterday_log["tomorrow_action"] or "ничего не обещал"
    else:
        y_done = "данных нет"
        y_blocked = "—"
        y_tomorrow = "ничего не обещал"

    # Сегодняшние данные
    if today_log:
        t_done = today_log["what_done"] or "ещё не записано"
        t_checkin = "да" if today_log["checkin_done"] else "нет"
        t_trained = "да" if today_log["trained"] else "нет"
        t_action = today_log["tomorrow_action"] or "не задано"
    else:
        t_done = "ещё не записано"
        t_checkin = "нет"
        t_trained = "нет"
        t_action = "не задано"

    return f"""
=== КОНТЕКСТ СЕССИИ ===
Сейчас: {now_str}
Часовой пояс: {TIMEZONE}

ГЛАВНАЯ ЦЕЛЬ АНДРЕЯ: {main_goal}
Streak (дней подряд с действием): {streak}

ВЧЕРА ({yesterday}):
  - Что сделал: {y_done}
  - Что мешало: {y_blocked}
  - Обещал на сегодня: {y_tomorrow}

СЕГОДНЯ:
  - Check-in сделан: {t_checkin}
  - Что сделал: {t_done}
  - Тренировка: {t_trained}
  - Задача на завтра: {t_action}

ВАЖНО: Если вчера было обещание — спроси про него ПЕРВЫМ делом.
Если check-in сегодня ещё не сделан — веди разговор к нему.
======================
"""


async def get_ai_response(telegram_id: int, user_message: str) -> str:
    """Получает ответ от Claude с полным контекстом и памятью."""

    # Строим динамический контекст
    context_block = await build_context_block(telegram_id)
    full_system = context_block + "\n\n" + SYSTEM_PROMPT

    # Загружаем историю разговора
    history = await database.get_recent_messages(telegram_id, limit=20)
    messages = [{"role": row["role"], "content": row["content"]} for row in history]

    # Добавляем новое сообщение пользователя
    messages.append({"role": "user", "content": user_message})

    # Запрос к Claude
    response = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1000,
        system=full_system,
        messages=messages,
    )

    assistant_text = response.content[0].text

    # Сохраняем в память
    await database.add_message(telegram_id, "user", user_message)
    await database.add_message(telegram_id, "assistant", assistant_text)

    return assistant_text


async def get_checkin_opener(telegram_id: int) -> str:
    """Генерирует открывающее сообщение для ежедневного check-in в 16:30."""
    context_block = await build_context_block(telegram_id)
    full_system = context_block + "\n\n" + SYSTEM_PROMPT

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    yesterday_log = await database.get_log_by_date(telegram_id, yesterday)
    y_tomorrow = (
        yesterday_log["tomorrow_action"]
        if yesterday_log and yesterday_log["tomorrow_action"]
        else None
    )

    if y_tomorrow:
        prompt = f'Время дневного check-in. Вчера пользователь обещал: "{y_tomorrow}". Напиши короткое открывающее сообщение — спроси напрямую выполнил ли он это обещание. Без лишних слов.'
    else:
        prompt = "Время дневного check-in. Напиши короткое открывающее сообщение — спроси что конкретно сделал сегодня для главной цели. Без лишних слов."

    response = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        system=full_system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


async def get_morning_push(telegram_id: int) -> str:
    """Генерирует утреннее сообщение с задачей дня."""
    context_block = await build_context_block(telegram_id)
    full_system = context_block + "\n\n" + SYSTEM_PROMPT

    response = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        system=full_system,
        messages=[
            {
                "role": "user",
                "content": "Напиши короткое утреннее сообщение. Напомни главную цель и одно конкретное действие на сегодня (основанное на том что было вчера). Максимум 3 строки.",
            }
        ],
    )
    return response.content[0].text


async def get_evening_reminder(telegram_id: int) -> str:
    """Генерирует вечернее напоминание если check-in не был сделан."""
    context_block = await build_context_block(telegram_id)
    full_system = context_block + "\n\n" + SYSTEM_PROMPT

    response = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=150,
        system=full_system,
        messages=[
            {
                "role": "user",
                "content": "Пользователь не сделал check-in сегодня. Напиши жёсткое короткое напоминание — не нотацию, а прямой вопрос что происходит. Максимум 2 строки.",
            }
        ],
    )
    return response.content[0].text
