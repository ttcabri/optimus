import html
import logging
from datetime import date, timedelta

import pytz
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
from config import TELEGRAM_USER_ID, TIMEZONE
from services import quest

logger = logging.getLogger(__name__)


# --- Квест дня: напоминания по живому плану ---

_sent: set[str] = set()


def _has_reminder(day: dict, start_min: int) -> bool:
    """На это время уже стоит напоминание от помощника: «Пора» не дублируем."""
    today = quest.today_key()
    return any(r.get("d") == today and abs(quest.to_min(r.get("at", "00:00")) - start_min) <= 20
               for r in day.get("_reminders") or [])


def quest_messages(day: dict, now_min: int, today: str, wd: int) -> list[str]:
    """Что сказать сейчас по живому плану. Каждое сообщение один раз за день."""
    out = []
    P = quest.prof(day)
    if now_min < quest.to_min(P["wake"]) or now_min > quest.to_min(quest.day_end(P)):
        return out

    def once(tag, text):
        k = f"{today}:{tag}"
        if k not in _sent:
            _sent.add(k)
            out.append(text)

    n = day.get("_notify") or {}
    pora, rest = n.get("pora", True), n.get("rest", True)
    # подъём, холодильник и взвешивание теперь в утренней сводке (services/morning.py)
    # отдых закончился: сразу говорим, что дальше
    for start, mins in (quest.rests_of(day) if rest else []):
        end = start + mins
        if end <= now_min < end + 6:
            nxt = next((it for it in quest.build_plan(day, now_min, wd)["items"] if not it.get("game") and not it.get("gap")), None)
            once(f"rest:{start}", "⏰ Отдых всё." + (f" Пора: <b>{nxt['name']}</b>, до {quest.hm(nxt['end'])}." if nxt else ""))
            if nxt:
                _sent.add(f"{today}:start:{nxt['id']}")
    rest_total = sum(min(m, max(0, now_min - s)) for s, m in quest.rests_of(day))
    if rest and rest_total >= 150:
        once("rest-total", f"🛑 Отдыха сегодня уже {rest_total // 60} ч {rest_total % 60} мин. Хватит, возвращайся к плану.")
    p = quest.build_plan(day, now_min, wd)
    first = next((it for it in p["items"] if not it.get("game")), None)
    if not pora:
        return out
    if first and first.get("started") and first.get("late", 0) >= 10:
        once(f"late:{first['id']}", f"⏱ «{first['name']}» идёт на {first['late']} мин дольше плана. Закрывай или режь лишнее, иначе вечер уедет.")
    elif first and not first.get("started") and (not first.get("minor") or str(first["id"]).startswith("ch_")) and first["start"] <= now_min + 2 \
            and not _has_reminder(day, first["start"]):
        it = quest.item(P, first["id"]) or {}
        hint = it.get("hint") or ("Дом, дело с повтором. Сделал, отметь в квесте или скажи боту." if str(first["id"]).startswith("ch_") else "")
        if first["id"] == "fine":
            hint = f"Переведи €{(P.get('penalty') or {}).get('fine')} в {(P.get('penalty') or {}).get('to')} и кинь скрин."
        if it.get("tutor") == "cbr":
            from services import theory
            hint = f"Сегодня: {theory.stage_name(day.get('_theory_i', 0))}. Напиши «теория», расскажу что учить."
        once(f"start:{first['id']}", f"▶️ Пора: <b>{first['name']}</b>, до {quest.hm(first['end'])}. {hint}".strip())
    if p["over"]:
        names = ", ".join(o["name"] for o in p["over"])
        once("over:" + names, f"⚠️ До отбоя не влезает: {names}. Открой квест и реши: сократить, убрать или на завтра.")
    return out


async def send_quest_tick(bot: Bot) -> None:
    """Каждые 5 минут: сверка с живым планом."""
    try:
        from services import morning
        await morning.maybe_send(bot, TELEGRAM_USER_ID)
        day = await quest.get_day(TELEGRAM_USER_ID)
        day["_notify"] = morning.notify(await database.get_quest_state(TELEGRAM_USER_ID))
        n = quest.now()
        msgs = quest_messages(day, n.hour * 60 + n.minute, quest.today_key(), quest.js_weekday())
        if msgs:
            from handlers.proof import quest_keyboard
            for m in msgs:
                await bot.send_message(TELEGRAM_USER_ID, m, reply_markup=quest_keyboard())
    except Exception as e:
        logger.error(f"Quest tick error: {e}")


async def send_reminders(bot: Bot) -> None:
    """Каждую минуту: напоминания, которые поставил помощник."""
    try:
        state = await database.get_quest_state(TELEGRAM_USER_ID)
        rem = state.get("reminders") or []
        today, now_t = quest.today_key(), quest.hhmm()
        due = [r for r in rem if not r.get("sent") and (r.get("d", "") < today or (r.get("d") == today and r.get("at", "99") <= now_t))]
        if not due:
            return
        for r in due:
            r["sent"] = True
        fresh = await database.get_quest_state(TELEGRAM_USER_ID)
        fresh["reminders"] = rem
        await database.save_quest_state(TELEGRAM_USER_ID, fresh)
        from handlers.proof import quest_keyboard
        for r in due:
            await bot.send_message(TELEGRAM_USER_ID, f"⏰ <b>Напоминание</b>\n{html.escape(r.get('text', ''))}", reply_markup=quest_keyboard())
    except Exception as e:
        logger.error(f"Reminders error: {e}")


async def send_quest_night(bot: Bot) -> None:
    """22:30 — итог дня и экран выкл."""
    try:
        from services import morning
        if not morning.notify(await database.get_quest_state(TELEGRAM_USER_ID))["evening"]:
            return
        day = await quest.get_day(TELEGRAM_USER_ID)
        P = quest.prof(day)
        pi = quest.pen_item(P)
        pen = P.get("penalty") or {}
        miss = pi and quest.on_day(pi, quest.js_weekday()) and not quest.item_done(day, pi)
        text = (
            "🌙 <b>Итог дня</b>\n\n" + quest.day_summary(day)
            + (f"\n\n⚠️ «{pi['name']}» сегодня не сделано. Завтра штрафной день: повторить" + (f" и €{pen['fine']} в {pen.get('to')}." if pen.get("fine") else ".") if miss else "")
            + "\n\nЧто завтра, кроме распорядка? Напиши так:\n<code>завтра: отвезти Audi; письмо в UWV</code>"
            + f"\n\nЭкран выкл и спать в {P['bed']}."
        )
        await bot.send_message(TELEGRAM_USER_ID, text)
    except Exception as e:
        logger.error(f"Quest night error: {e}")


async def send_quest_week(bot: Bot) -> None:
    """Воскресенье 21:00 — итог недели по квесту."""
    try:
        from services import morning
        if not morning.notify(await database.get_quest_state(TELEGRAM_USER_ID))["week"]:
            return
        state = await database.get_quest_state(TELEGRAM_USER_ID)
        P = quest.full_profile(state)
        today = quest.now().date()
        ks = [today - timedelta(days=i) for i in range(7)]
        views = [quest.day_view(state, k.strftime("%Y-%m-%d")) for k in ks]
        closed = sum(1 for v, k in zip(views, ks) if quest.day_closed(v, k.weekday()))
        rows = []
        for it in quest.items(P):
            if not it.get("main"):
                continue
            need = sum(1 for k in ks if quest.on_day(it, (k.weekday() + 1) % 7))
            got = sum(1 for v, k in zip(views, ks) if quest.on_day(it, (k.weekday() + 1) % 7) and quest.item_done(v, it))
            rows.append(f"{it['name']}: {got}/{need}")
        verdict = ("Сильная неделя. Так держать." if closed >= 6 else
                   "Нормально, но можно жёстче." if closed >= 4 else
                   "Слабая неделя. Начни завтра с главного дела.")
        text = (f"📊 <b>Неделя в квесте</b>\n\nДней закрыто полностью: {closed}/7\n" + "\n".join(rows)
                + f"\nПодряд сейчас: {quest.closed_streak(state)} дн.\n\n{verdict}")
        await bot.send_message(TELEGRAM_USER_ID, text)
    except Exception as e:
        logger.error(f"Quest week error: {e}")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)

    # Квест дня: живой план, проверка каждые 5 минут
    scheduler.add_job(send_quest_tick, "cron", minute="*/5", kwargs={"bot": bot})
    scheduler.add_job(send_reminders, "cron", minute="*", kwargs={"bot": bot})
    scheduler.add_job(send_quest_night, "cron", hour=22, minute=30, kwargs={"bot": bot})
    scheduler.add_job(send_quest_week, "cron", day_of_week="sun", hour=21, minute=0, kwargs={"bot": bot})

    return scheduler
