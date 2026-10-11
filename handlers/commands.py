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
