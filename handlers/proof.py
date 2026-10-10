"""Квест дня: /quest, пруфы (фото, ссылка на YouTube), трава, вес, свои дела, план на завтра."""
import io
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from config import WEBAPP_URL
from services import claude, quest

router = Router()

YT_PATTERN = r"(youtube\.com/(watch|shorts|live)|youtu\.be/|studio\.youtube\.com)"
SMOKE_PATTERN = r"(?i)^\s*(покурил|курнул|дунул)\s*[.!]?\s*$"
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


@router.message(F.text.regexp(YT_PATTERN))
async def proof_youtube(message: Message) -> None:
    day = await quest.get_day(message.from_user.id)
    if not quest.boss_done(day):
        day = await quest.mark_done(message.from_user.id, quest.BOSS_IDS)
        await message.answer(
            f"🎬 Видео засчитано. Босс убит.\n\n{_reward(day)}",
            reply_markup=quest_keyboard(),
        )
    elif "vid2" not in day["done"]:
        day = await quest.mark_done(message.from_user.id, ["vid2"])
        await message.answer(
            f"🔥 Второе видео засчитано. Дубль. +100 XP",
            reply_markup=quest_keyboard(),
        )
    else:
        await message.answer("Два видео сегодня уже есть. Остальное в запас на завтра.")


@router.message(F.text.regexp(SMOKE_PATTERN))
async def log_smoke(message: Message) -> None:
    day = await quest.add_smoke(message.from_user.id)
    state = quest.reward_state(day)
    if day.get("smokes") == 1:
        note = {
            "gold": "Все 4 дела закрыты раньше. Награда по-честному 🌿",
            "base": "Раньше времени, но тренировка была чистой. +30 XP.",
            "broken": "Раньше тренировки. Награда сегодня сгорела. Завтра заново.",
        }.get(state, "Записал.")
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


@router.message(F.photo)
async def proof_photo(message: Message, bot: Bot) -> None:
    photo = message.photo[-1]
    buf = io.BytesIO()
    await bot.download(photo, destination=buf)

    await bot.send_chat_action(message.chat.id, "typing")
    training = quest.training_id() or "gym"
    result = await claude.check_proof(buf.getvalue(), message.caption or "", training)

    kind = result.get("quest")
    comment = result.get("comment", "").strip()
    if not result.get("ok") or kind not in ("cbr", "eng", "fine", "training", "video"):
        await message.answer("❌ Не засчитано.\n" + (comment or "Не понял, что это за пруф. Подпиши фото."))
        return

    day = await quest.get_day(message.from_user.id)
    if kind == "training":
        ids, title = [training], "Тренировка"
    elif kind == "cbr":
        ids, title = ["cbr"], "Теория на права"
    elif kind == "eng":
        ids, title = ["eng"], "Duolingo"
    elif kind == "fine":
        if not day.get("_debt"):
            await message.answer("Штрафа сегодня нет. Но перевод в safemoney засчитываю как красавчик 💪")
            return
        ids, title = ["fine"], "Штраф €5"
    elif not quest.boss_done(day):
        ids, title = quest.BOSS_IDS, "Видео"
    else:
        ids, title = ["vid2"], "Второе видео"

    day = await quest.mark_done(message.from_user.id, ids)
    extra = ""
    if quest.reward_state(day) == "open":
        extra = "\n\n🌿 Все 4 дела закрыты. Награда открыта."
    await message.answer(
        f"✅ {title} засчитано.\n{comment}{extra}\n\n{_reward(day)}",
        reply_markup=quest_keyboard(),
    )
