"""Утренняя сводка: факты дня и живой текст для голосового. Шлётся после «Я встал» (services/dayflow.py)."""
import logging


import database
from services import house, quest, theory

logger = logging.getLogger(__name__)
DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def notify(state: dict) -> dict:
    """Какие уведомления присылать. Меняются в приложении: Я → Настройки."""
    n = {"morning": True, "voice": True, "pora": True, "rest": True, "evening": True, "week": True, "review": True}
    n.update({k: bool(v) for k, v in (state.get("notify") or {}).items() if k in n})
    return n


def facts(state: dict, day: dict, uid) -> str:
    """Голые факты на сегодня. Из них модель пишет живой текст, а если модель упала, шлём их как есть."""
    now = quest.lnow()
    p = quest.build_plan(day, quest.now_min())
    lines = [f"{DAYS[now.weekday()].capitalize()}, {now.strftime('%d.%m')}."]
    if day.get("woke"):
        lines.append(f"Встал в {day['woke']}, по плану {quest.prof(day)['wake']}.")
        sl = quest.sleep_hours(state, quest.today_key())
        if sl:
            lines.append(f"Спал примерно {sl} ч.")
    P = quest.prof(day)
    pi, pen = quest.pen_item(P), P.get("penalty") or {}
    if day.get("_debt") and pi:
        lines.append("Штрафной день: " + (pi.get("debt") or f"ещё раз «{pi['name']}»") + (f" и €{pen['fine']} в {pen.get('to')}." if pen.get("fine") else "."))
    items = [it for it in p["items"] if not it.get("game")][:6]
    if items:
        lines.append("План: " + "; ".join(f"{quest.hm(it['start'])} {it['name']}" for it in items))
    if p["over"]:
        lines.append("Не влезает: " + ", ".join(o["name"] for o in p["over"]))
    if any(it.get("tutor") == "cbr" for it in quest.items(P)):
        lines.append("Тема теории: " + theory.stage_name(day.get("_theory_i", 0)))
    h = state.get("house")
    if isinstance(h, dict):
        preps = [x for x in h.get("items", []) if x.get("cat") == "prep" and int(x.get("n") or 0) > 0]
        if preps:
            lines.append("Заготовки: " + ", ".join(f"{x['name']} {x['n']} порц." for x in preps))
        exp = house.expiring(h)
        if exp:
            lines.append("Скоро испортится: " + ", ".join(x["name"] for x in exp))
        free = [c["name"] for c in house.due_chores(h) if c.get("who") is None]
        if free:
            lines.append("По дому пора, никто не взял: " + ", ".join(free))
    if now.weekday() == 0:
        lines.append("Понедельник: взвесься до завтрака и напиши «вес 82.5».")
    main = [it["name"] for it in quest.items(P) if it.get("main") and quest.on_day(it, quest.js_weekday())]
    if main:
        lines.append("Главное сегодня: " + ", ".join(main) + f". Закрыл всё: {P.get('reward') or 'награда'}.")
    lines.append(f"Дней подряд всё главное закрыто: {quest.closed_streak(state)}.")
    return "\n".join(lines)


async def write(text_facts: str) -> str:
    from services import claude
    try:
        r = await claude.client.messages.create(
            model="claude-haiku-4-5", max_tokens=400,
            messages=[{"role": "user", "content": (
                "Ты личный помощник. Напиши человеку утреннюю сводку на день по фактам ниже. "
                "Бодро и по-дружески, по-русски, 5–8 коротких строк, без воды. Начни с короткого приветствия. "
                "Главное: что сделать первым, главные дела дня, ключевые дела по времени, что по дому, тема теории если есть. "
                "Одна фраза поддержки в конце, без пафоса. Без длинных тире, без markdown, можно 1–2 эмодзи.\n\n" + text_facts)}])
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        return text or text_facts
    except Exception as e:
        logger.error(f"morning write error: {e}")
        return "☀️ Доброе утро!\n" + text_facts
