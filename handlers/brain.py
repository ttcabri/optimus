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


@router.message(F.document, ~F.document.mime_type.startswith("image/"))
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


# ---------- чек: проверка перед записью ----------
EDIT_MS = 20 * 60 * 1000


@router.callback_query(F.data.startswith("rc:"))
async def receipt_cb(cb: CallbackQuery) -> None:
    import time
    from services import receipt
    _, op, rid = (cb.data.split(":", 2) + ["", ""])[:3]
    uid = cb.from_user.id
    state = await database.get_quest_state(uid)
    r = state.get("rcpt")
    if not r or r.get("id") != rid:
        await cb.answer("Этот чек уже записан или отменён")
        return
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if op == "ok":
        text = receipt.commit(state, uid)
    elif op == "no":
        state.pop("rcpt", None)
        text = "Ок, чек не записываю."
    else:
        r["edit"] = int(time.time() * 1000)
        text = "Напиши или скажи голосом, что поправить. Например: «яиц не было, молоко одно, итог 18,90»."
    await database.save_quest_state(uid, state)
    await cb.message.answer(text)


async def receipt_fix(message: Message, text: str) -> bool:
    """Если ждём правку чека: применить. True, если это была правка."""
    import time
    from services import receipt
    uid = message.from_user.id
    state = await database.get_quest_state(uid)
    r = state.get("rcpt")
    if not r or not r.get("edit") or int(time.time() * 1000) - r["edit"] > EDIT_MS:
        return False
    await message.bot.send_chat_action(message.chat.id, "typing")
    new = await receipt.fix(state, text)
    if not new:
        await message.answer("Не получилось поправить. Напиши ещё раз, по пунктам.")
        return True
    await database.save_quest_state(uid, state)
    await message.answer(receipt.text(new), reply_markup=receipt.kb(new))
    return True


@router.message(F.text & ~F.text.startswith("/"))
async def maybe_receipt_fix(message: Message) -> None:
    if not await receipt_fix(message, message.text):
        raise SkipHandler()


# ---------- таймер шага: кнопки ----------
@router.callback_query(F.data.startswith("st:"))
async def step_cb(cb: CallbackQuery) -> None:
    from services import quest
    _, op, sid = (cb.data.split(":", 2) + ["", ""])[:3]
    uid = cb.from_user.id
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if op == "pf":
        await cb.answer()
        await cb.message.answer("Кидай фото сюда, засчитаю шаг.")
        return
    state = await database.get_quest_state(uid)
    day = state.setdefault("days", {}).setdefault(quest.today_key(), {})
    if sid in (day.get("done") or []):
        await cb.answer("Этот шаг уже готов")
        return
    if op == "ext":
        day["sx"] = dict(day.get("sx") or {})
        day["sx"][sid] = day["sx"].get(sid, 0) + 5
        await database.save_quest_state(uid, state)
        await cb.answer("+5 минут")
        await cb.message.answer("⏱ Ещё 5 минут на шаг. Напомню.")
        return
    await quest.mark_done(uid, [sid])
    await cb.answer("Шаг готов")
    await cb.message.answer("✅ Шаг готов. Следующий уже идёт, таймер в приложении.")


# ---------- деньги и еда: кнопки ----------
@router.callback_query(F.data.startswith("mn:"))
async def money_cb(cb: CallbackQuery) -> None:
    from services import money
    _, op, rid = (cb.data.split(":", 2) + ["", ""])[:3]
    uid = cb.from_user.id
    state = await database.get_quest_state(uid)
    m = money.get(state)
    r = next((x for x in m["reg"] if x.get("id") == rid), None)
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if not r or r.get("paid") == money.ym():
        await cb.message.answer("Уже отмечено.")
        return
    _, warn = money.add(state, "exp", r["amt"], r.get("cat", "other"), r.get("acc"), r["n"], None, "reg:" + r["id"])
    r["paid"] = money.ym()
    r["u"] = brain._now_ms()
    await database.save_quest_state(uid, state)
    await cb.message.answer(f"✅ {html.escape(r['n'])} €{r['amt']:g} в тратах. На счетах €{money.total(m):g}." + (("\n" + warn) if warn else ""))


def meal_kb(state: dict):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from services import house
    h = house.get(state)
    rows = [[InlineKeyboardButton(text=f"🍱 {x['name']} −1 порц.", callback_data=f"fd:prep:{x['id']}")]
            for x in h.get("items", []) if x.get("cat") == "prep" and int(x.get("n") or 0) > 0][:4]
    rows.append([InlineKeyboardButton(text="✍️ Напишу, что ел", callback_data="fd:txt:"), InlineKeyboardButton(text="Не важно", callback_data="fd:skip:")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("fd:"))
async def food_cb(cb: CallbackQuery) -> None:
    from services import house
    _, op, iid = (cb.data.split(":", 2) + ["", ""])[:3]
    uid = cb.from_user.id
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if op == "txt":
        await cb.message.answer("Напиши или скажи голосом: например «2 яйца, 150 г курицы, рис». Спишу из Дома.")
        return
    if op == "prep":
        state = await database.get_quest_state(uid)
        h = house.get(state, uid)
        x = next((y for y in h.get("items", []) if y.get("id") == iid), None)
        res = house.prep_eat(h, x["name"]) if x else "этой заготовки уже нет"
        await database.save_quest_state(uid, state)
        await cb.message.answer("🍱 " + html.escape(res))
