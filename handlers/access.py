"""Закрытый бот: владелец всегда, остальные только после одобрения владельцем.

Проверка стоит на каждом обновлении (сообщения, правки, кнопки), а не только на сообщениях.
"""
import html
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject

import database
from config import TELEGRAM_USER_ID

router = Router()
_told: dict[int, float] = {}  # кому уже сказали «жди», чтобы не спамить


def _who(user) -> str:
    return (user.full_name or "без имени") + (f" @{user.username}" if user.username else "")


async def allowed(uid: int | None) -> bool:
    return uid is not None and (uid == TELEGRAM_USER_ID or await database.access_status(uid) == "ok")


def _kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Пустить", callback_data=f"acc:ok:{uid}"),
        InlineKeyboardButton(text="⛔ Отказать", callback_data=f"acc:no:{uid}")]])


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return None
        if await allowed(user.id):
            return await handler(event, data)
        # пришёл по ссылке-приглашению: пускаем сразу, без запроса владельцу
        text = getattr(event, "text", None) or ""
        if text.startswith("/start inv_") and await use_invite(data["bot"], user, text.split("inv_", 1)[1].strip()):
            return await handler(event, data)
        # чужой: в первый раз просим доступ у владельца, дальше молчим (раз в сутки напоминаем ему, что ждёт)
        st = await database.access_status(user.id)
        if st == "no":
            return None
        bot = data["bot"]
        if st is None:
            await database.set_access(user.id, "wait", _who(user))
            await bot.send_message(TELEGRAM_USER_ID, f"🔐 Просится в бота: <b>{html.escape(_who(user))}</b>\nid {user.id}", reply_markup=_kb(user.id))
        if time.time() - _told.get(user.id, 0) > 86400:
            _told[user.id] = time.time()
            if data.get("event_chat") is not None:
                await bot.send_message(user.id, "🔐 Бот закрытый. Запрос на доступ отправлен владельцу, напишу, когда пустит.")
        return None


async def use_invite(bot, user, code: str) -> bool:
    got = await database.invite_use(code, user.id)
    if not got:
        return False
    owner, house_id = got
    await database.set_access(user.id, "ok", _who(user))
    if house_id:
        await database.join_house(user.id, house_id)
        st = await database.get_quest_state(user.id)
        h = st.get("house")
        if isinstance(h, dict) and not any(str(m.get("uid")) == str(user.id) for m in h.get("members", [])):
            h.setdefault("members", []).append({"uid": user.id, "name": user.first_name or "Жилец", "role": "adult", "u": int(time.time() * 1000)})
            await database.save_quest_state(user.id, st)
    for to in {owner, TELEGRAM_USER_ID}:
        try:
            await bot.send_message(to, f"🔑 По приглашению зашёл: <b>{html.escape(_who(user))}</b>" + (" (живёт с тобой в доме)" if house_id and to == owner else ""))
        except Exception:
            pass
    return True


@router.callback_query(F.data.startswith("acc:"))
async def decide(cb: CallbackQuery) -> None:
    if cb.from_user.id != TELEGRAM_USER_ID:
        await cb.answer()
        return
    _, st, uid = cb.data.split(":")
    await database.set_access(int(uid), st)
    await cb.message.edit_text(cb.message.html_text + ("\n\n✅ Пустил" if st == "ok" else "\n\n⛔ Отказано"), reply_markup=None)
    if st == "ok":
        try:
            await cb.bot.send_message(int(uid), "✅ Доступ открыт. Пиши, или жми /start.")
        except Exception:
            pass
    await cb.answer("Готово")


@router.message(Command("access"))
async def list_access(message: Message) -> None:
    if message.from_user.id != TELEGRAM_USER_ID:
        return
    rows = await database.access_list()
    if not rows:
        await message.answer("Кроме тебя в боте никого. Чужие сообщения не обрабатываются, они приходят тебе запросом.")
        return
    names = {"ok": "✅ есть доступ", "wait": "⏳ ждёт", "no": "⛔ отказано"}
    for uid, name, st in rows:
        btn = InlineKeyboardButton(text="Убрать доступ" if st == "ok" else "Пустить", callback_data=f"acc:{'no' if st == 'ok' else 'ok'}:{uid}")
        await message.answer(f"{html.escape(name or str(uid))} · id {uid}\n{names.get(st, st)}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]))
