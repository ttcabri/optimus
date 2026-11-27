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
        # разово (27.10): голландский в распорядок, 15 мин каждый день, без давления (не главное)
        import os as _os
        from config import DATABASE_PATH as _DBP
        _flag = _os.path.join(_os.path.dirname(_os.path.abspath(_DBP)), "mig_nl")
        if st.get("profile") and not _os.path.exists(_flag):
            from services import quest as _q
            if not any("голланд" in (x.get("name") or "").lower() for x in _q.full_profile(st).get("routine") or []):
                _q.routine_edit(st, "add", {"name": "Голландский", "dur": 15,
                                            "sub": "15 минут, без зубрёжки",
                                            "hint": "Duolingo (с английского) или урок курса. Пара новых слов в день уже засчитывается."})
                changed = True
            open(_flag, "w").write("1")
        # разово (5.11): напоминания про нулевую btw-декларацию после закрытия ZZP (письмо Belastingdienst от 05-11-2026)
        _flag2 = _os.path.join(_os.path.dirname(_os.path.abspath(_DBP)), "mig_btw")
        if not _os.path.exists(_flag2):
            _txt = ("🔴‼️ ПОДАТЬ НУЛЕВУЮ BTW-ДЕКЛАРАЦИЮ ‼️🔴\n"
                    "Фирма закрыта, налоговая ждёт последнюю декларацию. Без неё штраф.\n"
                    "belastingdienst.nl → Mijn Belastingdienst Zakelijk → вход DigiD → btw-aangifte 4-й квартал 2026 → везде 0 → Verzenden.\n"
                    "Срок: до 31.01.2027. Подал? Скажи мне «btw подал», и я уберу остальные напоминания.")
            rem = st.get("reminders") or []
            for i, d in enumerate(["2027-01-02", "2027-01-09", "2027-01-16", "2027-01-23", "2027-01-28"]):
                rem.append({"id": f"rbtw{i}", "d": d, "at": "10:00", "text": _txt, "sent": False})
            st["reminders"] = rem
            changed = True
            open(_flag2, "w").write("1")
        _flag3 = _os.path.join(_os.path.dirname(_os.path.abspath(_DBP)), "mig_btw2")
        if not _os.path.exists(_flag3):  # + конец декабря (декларация появляется в кабинете в конце декабря)
            _t2 = ("🔴‼️ BTW-ДЕКЛАРАЦИЯ УЖЕ ДОЛЖНА ПОЯВИТЬСЯ ‼️🔴\n"
                   "Зайди: belastingdienst.nl → Mijn Belastingdienst Zakelijk → DigiD. Если btw-aangifte за 4-й квартал 2026 уже есть, "
                   "подай сразу: везде 0 → Verzenden. Срок до 31.01.2027.\nПодал? Скажи мне «btw подал».")
            rem = st.get("reminders") or []
            for i, d in enumerate(["2026-12-25", "2026-12-28", "2026-12-31"]):
                rem.append({"id": f"rbtwd{i}", "d": d, "at": "11:00", "text": _t2, "sent": False})
            st["reminders"] = rem
            changed = True
            open(_flag3, "w").write("1")
        _flag4 = _os.path.join(_os.path.dirname(_os.path.abspath(_DBP)), "mig_ww")
        if not _os.path.exists(_flag4):  # 10.11: подать на WW до 14.11 (первый день без работы 7.11)
            _t3 = ("🔴‼️ ПОДАТЬ НА WW СЕГОДНЯ ‼️🔴\n"
                   "werk.nl → Inloggen (DigiD) → «WW aanvragen». Первый день без работы: 07-11-2026. "
                   "Последний работодатель: Kloek Pallets B.V.\n"
                   "Крайний срок 14.11, иначе можно потерять деньги. Подал? Скажи мне «WW подал».")
            rem = st.get("reminders") or []
            for i, (d, t) in enumerate([("2026-11-10", "11:00"), ("2026-11-10", "15:00"), ("2026-11-11", "10:00"), ("2026-11-12", "10:00"), ("2026-11-13", "10:00")]):
                rem.append({"id": f"rww{i}", "d": d, "at": t, "text": _t3, "sent": False})
            st["reminders"] = rem
            changed = True
            open(_flag4, "w").write("1")
        _flag5 = _os.path.join(_os.path.dirname(_os.path.abspath(_DBP)), "mig_ww2")
        if not _os.path.exists(_flag5):  # 12.11: WW подан → убрать напоминания «подать», поставить обязанности по WW
            rem = [r for r in st.get("reminders") or [] if not str(r.get("id", "")).startswith("rww")]
            add = [
                ("2026-11-13", "12:00", "📬 WW: подключи Berichtenbox на mijn.overheid.nl (DigiD), чтобы не пропустить письма UWV. "
                                        "И на werk.nl → «Mijn werkmap» заполни CV и профиль."),
                ("2026-11-20", "12:00", "✈️ WW: отметь отпуск на werk.nl (Doorgeven → Vakantie) на 26.11–04.12. Обязательно ДО вылета."),
                ("2026-11-24", "12:00", "🔴‼️ Последний шанс: отпуск в werk.nl на 26.11–04.12 отмечен? Без этого могут снять WW за эти дни."),
            ]
            d0 = __import__("datetime").date(2026, 11, 16)
            for w in range(13):  # каждый понедельник до конца WW (~9 февраля)
                d = d0 + __import__("datetime").timedelta(days=7 * w)
                add.append((d.isoformat(), "11:00", "💼 WW: на этой неделе откликнись минимум на 1 вакансию и сохрани доказательство "
                                                    "(скрин, письмо). Нужно минимум 4 отклика за каждые 4 недели. Внеси их в werk.nl."))
            for i, (d, t, txt) in enumerate(add):
                rem.append({"id": f"rwwo{i}", "d": d, "at": t, "text": txt, "sent": False})
            st["reminders"] = rem
            changed = True
            open(_flag5, "w").write("1")
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

    async def _sweep0():  # после запуска: тихая чистка «Аренды» (результат в логах)
        await asyncio.sleep(30)
        try:
            from services import flats as _fl
            lost = await _fl.sweep(TELEGRAM_USER_ID)
            logging.warning(f"FLSWEEP lost={lost} {(await _fl.get(TELEGRAM_USER_ID)).get('sweep')}")
        except Exception as e:
            logging.warning(f"FLSWEEP fail {e}")
    asyncio.get_event_loop().create_task(_sweep0())

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
