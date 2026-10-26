"""Команды бота. Остался только /start: честное приветствие и кнопка квеста."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    from handlers.proof import quest_keyboard
    await message.answer(
        "Привет. Я веду твой день: план, напоминания, пруфы, дом и цель.\n\n"
        "Открой квест кнопкой ниже. Остальное пиши или говори голосом: «добавь на завтра в 10 отвезти машину», «вешу 83», «что поесть».",
        reply_markup=quest_keyboard(),
    )


# ---------- поиск жилья ----------
from aiogram import F  # noqa: E402
from aiogram.types import CallbackQuery  # noqa: E402


@router.message(Command("flats"))
async def cmd_flats(message: Message) -> None:
    import html
    from services import flats
    uid = message.from_user.id
    s = await flats.get(uid)
    arg = (message.text or "").split(maxsplit=1)[1].strip() if len((message.text or "").split(maxsplit=1)) > 1 else ""
    low = arg.lower()
    if low in ("on", "вкл", "старт"):
        s["on"] = True
        await flats.put(uid, s)
        await message.answer("🏠 Поиск включён. Проверяю сейчас, дальше каждые 5 минут. Первый раз только запоминаю то, что уже висит, присылать буду новое.")
        n = await flats.check(uid, lambda t, k: message.answer(t, reply_markup=k, disable_web_page_preview=True))
        await message.answer(flats.status_text(await flats.get(uid)))
        return
    if low in ("off", "выкл", "стоп"):
        s["on"] = False
        await flats.put(uid, s)
        await message.answer("Поиск жилья выключен.")
        return
    if low in ("test", "тест"):
        await message.answer("Проверяю все сайты…")
        xs, stat = await flats.fetch_all(s)
        s["stat"] = {**stat, "_t": int(__import__("time").time())}
        await flats.put(uid, s)
        await message.answer(flats.status_text(s))
        await flats.translate(xs[:3])
        for x in xs[:3]:
            await message.answer(flats.card(x), reply_markup=flats.kb(x), disable_web_page_preview=True)
            s["items"][flats.key(x)] = x
        await flats.put(uid, s)
        return
    if low.startswith(("about", "о себе")):
        s["about"] = arg.split(maxsplit=1)[1][:600] if len(arg.split(maxsplit=1)) > 1 else ""
        await flats.put(uid, s)
        await message.answer("Запомнил, буду писать хозяевам с этим." if s["about"] else "Очистил «о себе».")
        return
    if arg:
        import re
        nums = [int(x) for x in re.findall(r"\b(\d{3,4})\b", arg)]
        cities = [c.strip().lower() for c in re.sub(r"\b\d{2,4}\b|m2|м2|€", "", arg).split(",") if c.strip()]
        if nums:
            s["max"] = nums[0]
        if cities:
            s["areas"] = cities[:8]
            s["seen"] = {}  # новые города: начинаем запоминать заново, без пачки старого
        await flats.put(uid, s)
        bad = [c for c in s["areas"] if c not in flats.POSTCODES]
        await message.answer(flats.status_text(s) + (f"\n\n⚠️ Для Marktplaats не знаю индекс: {html.escape(', '.join(bad))}. Остальные сайты ищут." if bad else ""))
        return
    await message.answer(flats.status_text(s))


@router.callback_query(F.data.startswith("fl:r:"))
async def flats_reply(cb: CallbackQuery) -> None:
    import html
    from services import flats
    s = await flats.get(cb.from_user.id)
    x = (s.get("items") or {}).get(cb.data[5:])
    if not x:
        await cb.answer("Объявление устарело")
        return
    await cb.answer("Пишу письмо…")
    await cb.bot.send_chat_action(cb.message.chat.id, "typing")
    t = await flats.letter(x, s.get("about") or "")
    nl, ru = (t.split("RU:", 1) + [""])[:2]
    nl = nl.replace("NL:", "").strip()
    await cb.message.answer(f"<code>{html.escape(nl)}</code>", disable_web_page_preview=True)
    await cb.message.answer("🇷🇺 " + html.escape(ru.strip()) + "\n\nТапни по тексту выше, он скопируется. Отправь хозяину на сайте."
                            + ("" if s.get("about") else "\nСовет: /flats about … и напиши о себе (работа, доход, сбережения), письма станут сильнее."))
