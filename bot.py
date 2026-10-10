import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject

import database
from aiogram.types import MenuButtonWebApp, WebAppInfo

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, WEBAPP_URL
from handlers import commands, proof, text, voice
from webserver import start_web
from services.scheduler import setup_scheduler


class AllowedUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.id != TELEGRAM_USER_ID:
            return None
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    await database.init_db()

    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())

    dispatcher.message.middleware(AllowedUserMiddleware())

    dispatcher.include_router(commands.router)
    dispatcher.include_router(proof.router)
    dispatcher.include_router(voice.router)
    dispatcher.include_router(text.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()

    await start_web()
    if WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(
                chat_id=TELEGRAM_USER_ID,
                menu_button=MenuButtonWebApp(text="Квест", web_app=WebAppInfo(url=WEBAPP_URL)),
            )
        except Exception as e:
            logging.error(f"Menu button error: {e}")

    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
