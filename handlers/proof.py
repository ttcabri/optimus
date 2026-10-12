"""Квест дня: /quest, пруфы (фото, ссылка на YouTube), трава, вес, свои дела, план на завтра."""
import html
import io
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

import database
from config import WEBAPP_URL
from services import claude, quest, theory

router = Router()

YT_PATTERN = r"(youtube\.com/(watch|shorts|live)|youtu\.be/|studio\.youtube\.com)"
SMOKE_PATTERN = r"(?i)^\s*(покурил|курнул|дунул|взял награду|награда)\s*[.!]?\s*$"
WEIGHT_PATTERN = r"(?i)^\s*вес\s*:?\s*(\d{2,3}(?:[.,]\d)?)\s*(кг)?\s*$"
ADD_PATTERN = r"(?is)^\s*(добавь|сегодня)\s*:\s*(.+)$"
PLAN_PATTERN = r"(?is)^\s*завтра\s*:\s*(.+)$"


def quest_keyboard() -> InlineKeyboardMarkup | None:
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎮 Открыть квест", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )


def _reward(day: dict) -> str:
    return quest.reward_line(day)


def _split(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;\n]", text) if p.strip()][:5]


@router.message(Command("quest"))
async def cmd_quest(message: Message) -> None:
    day = await quest.get_day(message.from_user.id)
    text = "<b>Квест дня</b>\n\n" + quest.day_summary(day)
    kb = quest_keyboard()
    if kb is None:
        text += "\n\n⚠️ WEBAPP_URL не задан в Railway, кнопка не работает."
    await message.answer(text, reply_markup=kb)


def _proof_ids(day: dict, it: dict) -> tuple[list[str], str] | None:
    """Что закрыть пруфом по делу распорядка: само дело, все его шаги, или повтор (долг/бонус)."""
    if not quest.item_done(day, it):
        return (quest.step_ids(it) or [it["id"]]), it["name"]
    if (day.get("_debt") or it.get("bonus")) and quest.pen_item(quest.prof(day)) is not None \
            and quest.pen_item(quest.prof(day))["id"] == it["id"] and "vid2" not in day["done"]:
        return ["vid2"], (it.get("debt") if day.get("_debt") else it.get("bonus")) or "Повтор"
    return None


@router.message(F.text.regexp(YT_PATTERN))
async def proof_youtube(message: Message) -> None:
    day = await quest.get_day(message.from_user.id)
    it = next((x for x in quest.items(quest.prof(day)) if x.get("link") == "youtube"), None)
    got = _proof_ids(day, it) if it else None
    if not got:
        await message.answer("Ссылку вижу, но засчитывать нечего: по распорядку на сегодня всё уже закрыто.")
        return
    day = await quest.mark_done(message.from_user.id, got[0])
    await message.answer(f"🎬 {html.escape(got[1])}: засчитано.\n\n{_reward(day)}", reply_markup=quest_keyboard())


@router.message(F.text.regexp(SMOKE_PATTERN))
async def log_smoke(message: Message) -> None:
    day = await quest.add_smoke(message.from_user.id)
    if day.get("smokes") == 1:
        note = ("Все главные дела закрыты. Кайфуй, заслужил 🌿" if quest.day_closed(day)
                else "Записал. Главные дела ещё не закрыты, доделай их сегодня.")
        note += "\n\n" + quest.reward_line(day)
    else:
        note = f"Записал. Сегодня уже {day['smokes']}."
    await message.answer(note)


@router.message(F.text.regexp(WEIGHT_PATTERN))
async def log_weight(message: Message) -> None:
    m = re.match(WEIGHT_PATTERN, message.text)
    kg = float(m.group(1).replace(",", "."))
    if not 30 < kg < 250:
        await message.answer("Не похоже на вес. Напиши так: вес 82.5")
        return
    body = await quest.log_weight(message.from_user.id, kg)
    if len(body) > 1:
        diff = round(body[-1]["kg"] - body[0]["kg"], 1)
        sign = "+" if diff > 0 else ""
        await message.answer(f"⚖️ {kg} кг записал. С первого взвешивания: {sign}{diff} кг.")
    else:
        await message.answer(f"⚖️ {kg} кг записал. Первое взвешивание, дальше будет видна динамика.")


SUBS_PATTERN = r"(?i)^\s*подписчик\w*\s*:?\s*(\d[\d\s]*)\s*$"


@router.message(F.text.regexp(SUBS_PATTERN))
async def log_subs(message: Message) -> None:
    n = int(re.sub(r"\D", "", re.match(SUBS_PATTERN, message.text).group(1)))
    state = await database.get_quest_state(message.from_user.id)
    goals = dict(state.get("goals") or {})
    goals["subs"] = n
    state["goals"] = goals
    today = quest.today_key()
    log = [x for x in (state.get("chanLog") or []) if x.get("d") != today]
    state["chanLog"] = (log + [{"d": today, "subs": n, "views": goals.get("maxViews", 0)}])[-60:]
    await database.save_quest_state(message.from_user.id, state)
    left = next((t - n for t in (100, 1000) if t > n), None)
    await message.answer(f"📈 Записал: {n} подписчиков." + (f" До следующей цели {left}." if left else " Обе цели по подписчикам взяты."))


THEORY_PATTERN = r"(?i)^\s*(теория|что учить|/theory)\s*[?.!]?\s*$"


@router.message(F.text.regexp(THEORY_PATTERN))
async def theory_today(message: Message) -> None:
    state = await database.get_quest_state(message.from_user.id)
    text = theory.today_task(state)
    await database.save_quest_state(message.from_user.id, state)
    await message.answer(text + "\n\nВопрос по правилу или скрин вопроса кидай сюда, объясню.")


@router.message(F.text.regexp(ADD_PATTERN))
async def add_today(message: Message) -> None:
    m = re.match(ADD_PATTERN, message.text, re.S)
    added = await quest.add_custom(message.from_user.id, _split(m.group(2)))
    if added:
        await message.answer("➕ В очередь на сегодня:\n" + "\n".join(f"• {a}" for a in added), reply_markup=quest_keyboard())


@router.message(F.text.regexp(PLAN_PATTERN))
async def add_tomorrow(message: Message) -> None:
    m = re.match(PLAN_PATTERN, message.text, re.S)
    added = await quest.add_custom(message.from_user.id, _split(m.group(1)), tomorrow=True)
    if added:
        await message.answer("🗓 На завтра, кроме основы:\n" + "\n".join(f"• {a}" for a in added) + "\n\nУтром будут в очереди.")


async def _save_loc(message: Message) -> dict:
    import time as _t
    loc = message.location
    state = await database.get_quest_state(message.from_user.id)
    state["loc"] = {"lat": loc.latitude, "lon": loc.longitude, "t": int(_t.time()), "live": bool(loc.live_period)}
    await database.save_quest_state(message.from_user.id, state)
    return state


@router.message(F.location)
async def got_location(message: Message) -> None:
    state = await _save_loc(message)
    text = "📍 Вижу, где ты. Дорогу теперь считаю отсюда."
    if message.location.live_period:
        text = "📍 Вижу тебя в реальном времени, пока идёт трансляция. Дорогу считаю от того места, где ты сейчас."
    if not (state.get("home") or {}).get("lat"):
        text += "\nЕсли ты сейчас дома, напиши «я дома, запомни», и я сохраню это как дом."
    await message.answer(text)


@router.edited_message(F.location)
async def live_location(message: Message) -> None:
    await _save_loc(message)  # обновления трансляции: молча


@router.message(F.photo)
async def proof_photo(message: Message, bot: Bot) -> None:
    photo = message.photo[-1]
    buf = io.BytesIO()
    await bot.download(photo, destination=buf)

    await bot.send_chat_action(message.chat.id, "typing")
    day = await quest.get_day(message.from_user.id)
    P = quest.prof(day)
    result = await claude.check_proof(buf.getvalue(), message.caption or "", P, bool(day.get("_debt")))

    kind = result.get("quest")
    comment = result.get("comment", "").strip()
    if kind in ("receipt", "fridge") and result.get("items"):
        from services import house
        state = await database.get_quest_state(message.from_user.id)
        h = house.get(state, message.from_user.id)
        got = house.add_items(h, result["items"])
        await database.save_quest_state(message.from_user.id, state)
        head = "🧾 Чек разобрал, записал домой:" if kind == "receipt" else "🧊 Посмотрел холодильник, записал:"
        await message.answer(head + "\n" + "\n".join(f"• {html.escape(g)}" for g in got)
                             + "\n\nЕсли что-то не так, скажи: «яиц нет», «убери колу».", reply_markup=quest_keyboard())
        return
    if kind == "brain":
        from handlers.brain import save
        await save(message, (message.caption or ""), {"id": photo.file_id, "kind": "photo", "name": "фото"}, note=comment)
        return
    if kind == "question":
        await message.answer("📚 " + html.escape(comment or "Не разобрал вопрос. Скинь скрин почётче."))
        return
    it = quest.item(P, kind or "")
    if not result.get("ok") or not (it or kind == "fine"):
        await message.answer("Это не пруф, ничего не отметил.\n" + html.escape(comment or "")
                             + "\n\nЕсли хотел что-то спросить, напиши текстом или голосом.")
        return

    if kind == "fine":
        if not day.get("_debt"):
            await message.answer("Штрафа сегодня нет. Но перевод засчитываю как красавчик 💪")
            return
        ids, title = ["fine"], f"Штраф €{(P.get('penalty') or {}).get('fine') or 0}"
    else:
        got = _proof_ids(day, it)
        if not got:
            await message.answer(f"«{html.escape(it['name'])}» сегодня уже засчитано 👍")
            return
        ids, title = got

    day = await quest.mark_done(message.from_user.id, ids)
    extra = ""
    if it and it.get("tutor") == "cbr" and result.get("score") is not None:
        st = await database.get_quest_state(message.from_user.id)
        line = theory.record(st, result.get("score"), result.get("total"), result.get("weak", ""), quest.today_key())
        if line:
            await database.save_quest_state(message.from_user.id, st)
            extra += "\n\n" + line
    if quest.reward_state(day) == "open":
        extra += "\n\n🌿 Все главные дела закрыты. Награда открыта."
    await message.answer(
        f"✅ {html.escape(title)}: засчитано.\n{html.escape(comment)}{extra}\n\n{_reward(day)}",
        reply_markup=quest_keyboard(),
    )
