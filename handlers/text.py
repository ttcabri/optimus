from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from services import claude

router = Router()


@router.message(F.text)
async def handle_text(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        return

    reply = await claude.get_response(message.from_user.id, message.text)
    await message.answer(reply)
