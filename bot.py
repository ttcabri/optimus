import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import database
from aiogram.types import MenuButtonWebApp, WebAppInfo

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, WEBAPP_URL
from handlers import access, brain, commands, day, proof, text, voice
from webserver import start_web
from services.scheduler import setup_scheduler


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    await database.init_db()
    # разово: бот записал себе неверный вывод из ночных отметок (12.10), убираем
    try:
        st = await database.get_quest_state(TELEGRAM_USER_ID)
        changed = False
        if any("Проснулся в ночное время" in n for n in st.get("notes") or []):
            st["notes"] = [n for n in st["notes"] if "Проснулся в ночное время" not in n]
            changed = True
        prof = st.get("profile") or {}
        if prof and "reward" not in prof:  # у владельца награда была по умолчанию; для новых людей умолчания больше нет
            st["profile"] = dict(prof, reward="Трава")
            changed = True
        # разово (13.10): штрафы 12.10 и 13.10 бот записал тратами и ещё раз переводом €10 в сейф. Оставляем один перевод.
        from services import money as _money
        mo = (st.get("money") or {}).get("ops") or []
        tr = next((o for o in mo if o.get("id") == "m1a131ef32e1d6b"), None)
        if tr and not tr.get("cat"):
            for oid in ("m1a131ea850e6fc", "m1a131eb2483474"):
                _money.remove(st, oid)
            tr.update(cat="fine", n="Штрафы за 12.10 и 13.10", u=_money._ms())
            st["notes"] = [n for n in st.get("notes") or [] if "заметки к переводам" not in n]
            changed = True
        if changed:
            await database.save_quest_state(TELEGRAM_USER_ID, st)
    except Exception as e:
        logging.error(f"notes cleanup error: {e}")

    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())

    # закрытый бот: проверка на каждом обновлении (сообщения, правки, кнопки)
    dispatcher.update.outer_middleware(access.AccessMiddleware())

    dispatcher.include_router(access.router)
    dispatcher.include_router(commands.router)
    dispatcher.include_router(day.router)
    dispatcher.include_router(brain.router)
    dispatcher.include_router(proof.router)
    dispatcher.include_router(voice.router)
    dispatcher.include_router(text.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()

    import webserver
    webserver.BOT = bot
    await start_web()
    if WEBAPP_URL:
        try:
            # кнопка «Квест» у всех, кому открыт бот (у каждого свой профиль и данные)
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Квест", web_app=WebAppInfo(url=WEBAPP_URL)))
        except Exception as e:
            logging.error(f"Menu button error: {e}")

    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
