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

## Квест дня (Telegram Mini App)

Игровой распорядок дня внутри бота: видео как главный квест с фокус-таймером, CBR, тренировки, английский, жетоны на Доту, чистое утро, вес и рабочие веса.

### Запуск на Railway
1. Settings → Networking → **Generate Domain**. Получишь адрес вида `https://optimus-production.up.railway.app`.
2. Variables → добавить `WEBAPP_URL` = этот адрес со слешем в конце.
3. Чтобы база не стиралась при деплое: добавить Volume с mount path `/data` и поставить `DATABASE_PATH=/data/mentor.db`.
4. Деплой. В боте появится кнопка меню «Квест» и команда `/quest`.

### Что понимает бот
- фото с тренировки, скрин CBR, скрин загрузки видео: проверка через Claude и отметка квеста
- ссылка на YouTube: видео засчитано (вторая ссылка за день: бонусное второе видео)
- `покурил`: отметка травы, считается «чистое утро»
- `вес 82.5`: взвешивание
- `добавь: дело1; дело2`: свои дела на сегодня
- `завтра: дело1; дело2`: план на завтра

Напоминания: 08:50 CBR, 09:30 / 11:30 / 14:00 видео, 15:30 тренировка, 18:45 английский, 22:30 итог дня, пн 08:10 взвешивание.
