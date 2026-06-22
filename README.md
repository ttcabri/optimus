# Mentor Bot

Персональный Telegram-бот — жёсткий AI-наставник. Держит на одном треке к главной цели, отслеживает ежедневный прогресс и помогает улучшать английский.

## Стек

- Python 3.11+, aiogram 3.x
- Claude API (claude-haiku-4-5) — диалог с памятью
- Whisper API — транскрипция голосовых сообщений
- SQLite (aiosqlite)
- APScheduler — ежедневные уведомления
- Railway — хостинг (polling)

## Установка

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Заполнить `.env`:

```
TELEGRAM_BOT_TOKEN=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
TELEGRAM_USER_ID=
DATABASE_PATH=mentor.db
TIMEZONE=Europe/Amsterdam
```

## Запуск

```bash
python bot.py
```

## Команды

| Команда | Действие |
|---------|----------|
| /start | Онбординг, фиксация главной цели |
| /status | Streak, прогресс недели, главная цель |
| /week | Недельная сводка |
| /ideas | Список припаркованных идей |
| /goal | Показать/изменить главную цель |

## Деплой на Railway

1. Создать проект на railway.app, подключить GitHub репо
2. Добавить переменные окружения из `.env.example`
3. SQLite хранить на persistent volume (`/data/mentor.db`)
4. Запуск как worker (polling), не web-сервис
