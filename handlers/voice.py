import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.types import Message

from services import claude, whisper

router = Router()


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    file = await bot.get_file(message.voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        local_path = tmp.name

    try:
        await bot.download_file(file.file_path, destination=local_path)
        text = await whisper.transcribe(local_path)
    finally:
        os.remove(local_path)

    if text is None:
        await message.answer("Недоступен, напиши позже.")
        return

    reply = await claude.get_response(message.from_user.id, text)
    await message.answer(reply)
