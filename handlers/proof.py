"""Квест дня: команда /quest, пруфы фото и ссылкой на YouTube."""
import io

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from config import WEBAPP_URL
from services import claude, quest

router = Router()

YT_PATTERN = r"(youtube\.com/(watch|shorts|live)|youtu\.be/|studio\.youtube\.com)"


def quest_keyboard() -> InlineKeyboardMarkup | None:
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎮 Открыть квест", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )


@router.message(Command("quest"))
async def cmd_quest(message: Message) -> None:
    day = await quest.get_day(message.from_user.id)
    kb = quest_keyboard()
    text = "<b>Квест дня</b>\n\n" + quest.day_summary(day)
    if kb is None:
        text += "\n\n⚠️ WEBAPP_URL не задан в Railway, кнопка не работает."
    await message.answer(text, reply_markup=kb)


@router.message(F.text.regexp(YT_PATTERN))
async def proof_youtube(message: Message) -> None:
    day = await quest.get_day(message.from_user.id)
    if quest.boss_done(day):
        await message.answer("Видео сегодня уже засчитано. Можешь пилить завтрашнее.")
        return
    day = await quest.mark_done(message.from_user.id, quest.BOSS_IDS)
    left = quest.tokens(day) - day.get("games", 0)
    await message.answer(
        f"🎬 Видео засчитано. Босс убит.\nЖетоны на Доту: <b>{left}</b>",
        reply_markup=quest_keyboard(),
    )


@router.message(F.photo)
async def proof_photo(message: Message, bot: Bot) -> None:
    photo = message.photo[-1]
    buf = io.BytesIO()
    await bot.download(photo, destination=buf)

    await message.bot.send_chat_action(message.chat.id, "typing")
    result = await claude.check_proof(buf.getvalue(), message.caption or "", quest.training_id())

    kind = result.get("quest")
    comment = result.get("comment", "").strip()
    if not result.get("ok") or kind not in ("cbr", "training", "video"):
        await message.answer("❌ Не засчитано.\n" + (comment or "Не понял, что это за пруф. Подпиши фото."))
        return

    if kind == "training":
        ids = [quest.training_id()]
        title = "Тренировка"
    elif kind == "cbr":
        ids = ["cbr"]
        title = "Теория CBR"
    else:
        ids = quest.BOSS_IDS
        title = "Видео"

    day = await quest.mark_done(message.from_user.id, ids)
    left = quest.tokens(day) - day.get("games", 0)
    await message.answer(
        f"✅ {title} засчитано.\n{comment}\n\nЖетоны на Доту: <b>{left}</b>",
        reply_markup=quest_keyboard(),
    )
