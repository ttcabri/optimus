"""Начало дня и сверка с планом.

«Я встал» (кнопка в приложении или «встал» боту): бот присылает утреннее сообщение с планом от этого времени,
кнопки «Принять» (что не влезло, уходит на завтра) и «Поправить».
Выпал из плана: час без отметок после подъёма, бот спрашивает, что было, пишет ответ в историю
и присылает остаток дня на подтверждение. Штрафы прежние, без поблажек.
"""
import html
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import database
from services import brain, house, quest, theory

logger = logging.getLogger(__name__)
DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
DRIFT_MIN = 60  # столько минут без отметок, и бот спрашивает


def plan_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data="plan:ok"),
        InlineKeyboardButton(text="✏️ Поправить", callback_data="plan:edit")]])


def _late(day: dict) -> str:
    P = quest.prof(day)
    w = quest.woke_at(day) or quest.hhmm()
    d = quest.to_min(w) - quest.to_min(P["wake"])
    if abs(d) < 15:
        return f"Встал в {w}, вовремя."
    h, m = divmod(abs(d), 60)
    span = (f"{h} ч" if h else "") + (f" {m} мин" if m else "")
    return f"Встал в {w}, на {span.strip()} {'позже' if d > 0 else 'раньше'} плана."


def plan_lines(day: dict) -> tuple[list[str], list[str], dict | None]:
    p = quest.build_plan(day, quest.now_min())
    items = [it for it in p["items"] if not it.get("game") and not it.get("gap")]
    lines = [f"{quest.hm(it['start'])}  {html.escape(it['name'])}" for it in items]
    return lines, [html.escape(o["name"]) for o in p["over"]], (items[0] if items else None)


def morning_text(state: dict, day: dict) -> str:
    n = quest.lnow()
    P = quest.prof(day)
    out = [f"☀️ <b>Доброе утро, {DAYS[n.weekday()]} {n.strftime('%d.%m')}</b>", _late(day)]
    sl = quest.sleep_hours(state, quest.today_key())
    if sl:
        out.append(f"Спал ~{str(sl).replace('.', ',')} ч" + (". Мало: сегодня главное сделай с утра, вечером не засиживайся." if sl < 6 else "."))
    yk = (n.date() - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
    yv = quest.day_view(state, yk)
    r = quest.reqs(yv, (n.weekday() - 1) % 7)
    if r and (state.get("days") or {}).get(yk):
        miss = [x["name"] for x in r if not x["t"]]
        y = f"Главное {len(r) - len(miss)} из {len(r)}" + (f", не сделано: {html.escape(', '.join(miss))}." if miss else ", всё закрыто.")
        out += ["", "<b>Вчера</b>", y]
    pi, pen = quest.pen_item(P), P.get("penalty") or {}
    if day.get("_debt") and pi:
        out.append("⚠️ Сегодня штрафной день: " + html.escape(pi.get("debt") or f"ещё раз «{pi['name']}»")
                   + (f" и €{pen['fine']} в {html.escape(str(pen.get('to') or 'копилку'))}." if pen.get("fine") else "."))
    elif r and (state.get("days") or {}).get(yk):
        out.append("Штрафа нет.")
    lines, over, first = plan_lines(day)
    if lines:
        out += ["", "<b>План</b>"] + lines
    if over:
        out += ["", "<b>Не влезает</b>", ", ".join(over) + ". Нажмёшь «Принять», перенесу на завтра."]
    extra = []
    if any(it.get("tutor") == "cbr" for it in quest.items(P)):
        extra.append("Теория: " + html.escape(theory.stage_name(day.get("_theory_i", 0))))
    h = state.get("house")
    if isinstance(h, dict):
        preps = [x for x in h.get("items", []) if x.get("cat") == "prep" and int(x.get("n") or 0) > 0]
        if preps:
            extra.append("Заготовки: " + html.escape(", ".join(f"{x['name']} {x['n']} порц." for x in preps)))
        exp = house.expiring(h)
        if exp:
            extra.append("Скоро испортится: " + html.escape(", ".join(x["name"] for x in exp)))
    bl = brain.morning_line(state)
    if bl:
        extra.append(bl)
    if n.weekday() == 0:
        extra.append("Понедельник: взвесься до завтрака и напиши «вес 82.5».")
    if extra:
        out += [""] + extra
    if first:
        out += ["", f"<b>Первым делом:</b> {html.escape(first['name'])}."]
    return "\n".join(out)


async def send_morning(bot: Bot, uid: int) -> None:
    from services import morning
    state = await database.get_quest_state(uid)
    day = await quest.get_day(uid)
    text = morning_text(state, day)
    if "🧠" in text:  # закреп из мозга показан утром: запомнить
        await database.save_quest_state(uid, state)
    n = morning.notify(state)
    if n["voice"]:
        try:
            from services import tts
            audio = await tts.speak(await morning.write(morning.facts(state, day, uid)))
            if audio:
                await bot.send_voice(uid, BufferedInputFile(audio, "morning.ogg"))
        except Exception as e:
            logger.error(f"morning voice error: {e}")
    await bot.send_message(uid, text, reply_markup=plan_kb())


async def wake(uid: int) -> bool:
    """Отметить подъём. False, если сегодня уже отмечен."""
    state = await database.get_quest_state(uid)
    day = quest._day(state, quest.today_key())
    if day.get("woke"):
        return False
    day["woke"] = quest.hhmm()
    quest.log_ev(day, "woke")
    day["mSent"] = True
    await database.save_quest_state(uid, state)
    return True


async def accept(uid: int) -> str:
    """План принят: что не влезает, переезжает на завтра."""
    state = await database.get_quest_state(uid)
    day = await quest.get_day(uid)
    over = quest.build_plan(day, quest.now_min())["over"]
    moved = []
    for o in over:
        if quest.move_task(state, o["id"], quest.tomorrow_key()).startswith("перен"):
            moved.append(o["name"])
    d = quest._day(state, quest.today_key())
    d["ok"] = quest.hhmm()
    quest.log_ev(d, "plan")
    await database.save_quest_state(uid, state)
    return "Принято." + (f" На завтра: {', '.join(moved)}." if moved else "")


# --- выпал из плана ---

def last_activity(day: dict) -> int | None:
    """Последняя отметка дня в минутах: подъём, любое событие, конец отдыха."""
    w = quest.woke_at(day)
    if not w:
        return None
    ts = [quest.to_min(w)] + [quest._ev_min(e[0]) for e in day.get("ev") or [] if len(e) == 3]
    ts += [quest._ev_min(t) for t in (day.get("at") or {}).values() if t]
    ts += [s + m for s, m in quest.rests_of(day)]
    return max(ts)


def drift_due(day: dict, now_m: int) -> int | None:
    """Время последней отметки, если с неё прошёл час и ничего не идёт. Иначе None."""
    P = quest.prof(day)
    if now_m > quest.to_min(quest.day_end(P)):
        return None
    last = last_activity(day)
    if last is None or now_m - last < DRIFT_MIN:
        return None
    if day.get("cur") or any(s <= now_m < s + m for s, m in quest.rests_of(day)):
        return None  # дело идёт или отдых: не дёргаем
    if (day.get("drift") or "") and quest._ev_min(day["drift"]) >= last:
        return None  # про эту паузу уже спрашивали
    return last


def drift_kb(qid: str | None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🎮 Залип", callback_data="off:game")],
            [InlineKeyboardButton(text="📌 Было другое дело", callback_data="off:busy")]]
    if qid:
        rows.append([InlineKeyboardButton(text="▶️ Начинаю сейчас", callback_data=f"off:go:{qid}"[:64])])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def check_drift(bot: Bot, uid: int) -> None:
    state = await database.get_quest_state(uid)
    day = await quest.get_day(uid)
    now_m = quest.now_min()
    last = drift_due(day, now_m)
    if last is None:
        return
    lines, _, first = plan_lines(day)
    d = quest._day(state, quest.today_key())
    d["drift"] = quest.hhmm()
    await database.save_quest_state(uid, state)
    gap = now_m - last
    h, m = divmod(gap, 60)
    txt = f"С {quest.hm(last)} ни одной отметки, это {h} ч{f' {m} мин' if m else ''}. Что было?"
    if first:
        txt += f"\nПо плану сейчас: <b>{html.escape(first['name'])}</b>."
    await bot.send_message(uid, txt, reply_markup=drift_kb(first["id"] if first else None))


async def answer_drift(uid: int, kind: str, qid: str = "") -> str:
    """Ответ на «что было»: пишем в историю и возвращаем остаток дня."""
    state = await database.get_quest_state(uid)
    day = await quest.get_day(uid)
    last = last_activity(day)
    d = quest._day(state, quest.today_key())
    gap = max(0, quest.now_min() - last) if last is not None else 0
    if kind in ("game", "busy"):
        d["ev"] = (d.get("ev") or []) + [[quest.hm(last if last is not None else quest.now_min()), "off" if kind == "game" else "busy", str(gap)]]
        await database.save_quest_state(uid, state)
        day = await quest.get_day(uid)
        lines, over, _ = plan_lines(day)
        head = f"Записал: залип, {gap} мин." if kind == "game" else f"Записал: другое дело, {gap} мин. Если хочешь, напиши, что именно."
        body = "\n".join(lines) if lines else "На сегодня по плану всё."
        tail = f"\n\nНе влезает: {', '.join(over)}. «Принять» перенесёт на завтра." if over else ""
        return f"{head}\n\n<b>Остаток дня</b>\n{body}{tail}"
    if kind == "go" and qid:
        d["cur"] = {"id": qid, "at": quest.hhmm()}
        quest.log_ev(d, "start", qid)
        await database.save_quest_state(uid, state)
        nm = quest.names(quest.full_profile(state)).get(qid) or next((c["name"] for c in d.get("custom") or [] if c.get("id") == qid), "дело")
        return f"Погнали: <b>{html.escape(nm)}</b>. Закончишь, отметь в квесте или скажи боту."
    return ""
