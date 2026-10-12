"""Мозг в боте: пересланные посты, ссылки, документы. Ответ на «О чём это?» раскладывает заново."""
import html
import logging

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import CallbackQuery, Message

import database
from services import brain

router = Router()
logger = logging.getLogger(__name__)

LINK_MSG = r"(?is)^\s*(\S.{0,200}\s)?https?://\S+(\s.{0,300})?$"


def _urls(message: Message) -> list[str]:
    out = []
    for e in (message.entities or []) + (message.caption_entities or []):
        if e.type == "text_link" and e.url:
            out.append(e.url)
    return out


def _file(message: Message) -> dict | None:
    if message.document:
        d = message.document
        return {"id": d.file_id, "kind": "doc", "name": (d.file_name or "файл")[:80], "mime": d.mime_type or ""}
    if message.photo:
        return {"id": message.photo[-1].file_id, "kind": "photo", "name": "фото"}
    if message.video:
        return {"id": message.video.file_id, "kind": "video", "name": (message.video.file_name or "видео")[:80]}
    return None


async def save(message: Message, text: str, file: dict | None = None, note: str = "", folder: str | None = None) -> None:
    uid = message.from_user.id
    await message.bot.send_chat_action(message.chat.id, "typing")
    state = await database.get_quest_state(uid)
    it = await brain.ingest(state, text=text, file=file, src="tg", note=note, folder=folder)
    msg = await message.answer(brain.reply_text(it), reply_markup=brain.kb_for(it))
    if it["f"] == "inbox":
        b = brain.get(state)
        b["ask"] = dict(list((b.get("ask") or {}).items())[-20:], **{str(msg.message_id): it["id"]})
    it.pop("_offer", None)
    await database.save_quest_state(uid, state)


@router.message(F.forward_origin)
async def forwarded(message: Message) -> None:
    text = (message.text or message.caption or "")
    links = _urls(message)
    if links:
        text = (text + "\n" + "\n".join(links)).strip()
    src = message.forward_origin
    who = getattr(getattr(src, "chat", None), "title", None) or getattr(getattr(src, "sender_user", None), "full_name", None) or ""
    await save(message, text, _file(message), note=f"переслано из «{who}»" if who else "")


@router.message(F.document)
async def document(message: Message) -> None:
    await save(message, message.caption or "", _file(message), folder="docs" if not message.caption else None)


@router.message(F.text.regexp(LINK_MSG))
async def link(message: Message) -> None:
    await save(message, message.text)


async def answer_ask(message: Message, text: str) -> bool:
    """Ответ на «О чём это?». True, если это был он."""
    r = message.reply_to_message
    if not r:
        return False
    uid = message.from_user.id
    state = await database.get_quest_state(uid)
    b = brain.get(state)
    iid = (b.get("ask") or {}).get(str(r.message_id))
    it = brain.find(state, iid) if iid else None
    if not it:
        return False
    await message.bot.send_chat_action(message.chat.id, "typing")
    await brain.redo(state, it, text)
    b["ask"] = {k: v for k, v in (b.get("ask") or {}).items() if v != it["id"]}
    out = brain.reply_text(it)
    kb = brain.kb_for(it)
    it.pop("_offer", None)
    await database.save_quest_state(uid, state)
    await message.answer(out, reply_markup=kb)
    return True


@router.message(F.reply_to_message, F.text)
async def reply_text(message: Message) -> None:
    if not await answer_ask(message, message.text):
        raise SkipHandler()


@router.callback_query(F.data.startswith("br:"))
async def brain_cb(cb: CallbackQuery) -> None:
    _, op, iid = (cb.data.split(":", 2) + ["", ""])[:3]
    uid = cb.from_user.id
    state = await database.get_quest_state(uid)
    it = brain.find(state, iid)
    if not it:
        await cb.answer("Этой записи уже нет")
        return
    text = None
    if op == "task":
        dk = brain.to_task(state, it)
        text = f"✅ «{html.escape(it['t'])}» в плане на завтра ({dk[8:10]}.{dk[5:7]})."
    elif op == "del":
        brain.remove(state, iid)
        text = "Удалил из мозга."
    elif op == "later":
        if it.get("pin"):
            it["pin"]["off"] = 1
            brain.touch(it)
        text = "Ок, полежит. Достану, когда будет к месту."
    await database.save_quest_state(uid, state)
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if text:
        await cb.message.answer(text)
