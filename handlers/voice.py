import html
import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.types import BufferedInputFile, Message

import database
from services import claude, tts, whisper

router = Router()
logger = logging.getLogger(__name__)


async def voice_replies_on(telegram_id: int) -> bool:
    state = await database.get_quest_state(telegram_id)
    return (state.get("settings") or {}).get("voice", True)


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

    if not text:
        await message.answer("Не разобрал голосовое. Скажи ещё раз или напиши текстом.")
        return

    await bot.send_chat_action(message.chat.id, "record_voice")
    try:
        reply = await claude.get_ai_response(message.from_user.id, text, voice=True)
    except Exception as e:
        logger.error(f"Voice chat error: {e}")
        await message.answer("Не получилось ответить, попробуй ещё раз через минуту.")
        return
    audio = await tts.speak(reply) if await voice_replies_on(message.from_user.id) else None
    if audio:
        await message.answer_voice(BufferedInputFile(audio, "reply.ogg"))
    await message.answer(f"<i>🎙 {html.escape(text[:300])}</i>\n\n{html.escape(reply)}")
