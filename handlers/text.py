import time
import html
import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import database
from services import claude

router = Router()
logger = logging.getLogger(__name__)

VOICE_TOGGLE = r"(?i)^\s*голосом\s+(вкл|выкл|on|off)\s*$"


@router.message(F.text.regexp(VOICE_TOGGLE))
async def toggle_voice(message: Message) -> None:
    on = re.match(VOICE_TOGGLE, message.text).group(1).lower() in ("вкл", "on")
    state = await database.get_quest_state(message.from_user.id)
    state["settings"] = dict(state.get("settings") or {}, reply="both" if on else "text", u=int(time.time() * 1000))
    await database.save_quest_state(message.from_user.id, state)
    await message.answer("На голосовые отвечаю голосом и текстом." if on else "На голосовые отвечаю только текстом.")


@router.message(F.text)
async def handle_text(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        return
    await message.bot.send_chat_action(message.chat.id, "typing")
    try:
        reply = html.escape(await claude.get_ai_response(message.from_user.id, message.text))
    except Exception as e:
        logger.error(f"Chat error: {e}")
        reply = "Не получилось ответить, попробуй ещё раз через минуту."
    await message.answer(reply)
