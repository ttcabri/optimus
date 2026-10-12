"""Начало дня и «выпал из плана»: «встал» боту, кнопки «Принять / Поправить», ответ «что было»."""
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from services import dayflow

router = Router()

WOKE_PATTERN = r"(?i)^\s*(я\s+)?(встал|проснулся|подъём|подъем)\s*[.!]?\s*$"


@router.message(F.text.regexp(WOKE_PATTERN))
async def woke(message: Message) -> None:
    if not await dayflow.wake(message.from_user.id):
        await message.answer("Подъём сегодня уже отмечен. План в квесте.")
        return
    await dayflow.send_morning(message.bot, message.from_user.id)


@router.callback_query(F.data.startswith("plan:"))
async def plan_cb(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    if cb.data == "plan:ok":
        text = await dayflow.accept(uid)
        await cb.answer("Принято")
    else:
        text = "Напиши или скажи голосом, что поменять: перенести, убрать, добавить, поменять время."
        await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.message.answer(text)


@router.callback_query(F.data.startswith("off:"))
async def drift_cb(cb: CallbackQuery) -> None:
    parts = cb.data.split(":", 2)
    kind, qid = parts[1], (parts[2] if len(parts) > 2 else "")
    text = await dayflow.answer_drift(cb.from_user.id, kind, qid)
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if text:
        await cb.message.answer(text, reply_markup=dayflow.plan_kb() if kind != "go" else None)
