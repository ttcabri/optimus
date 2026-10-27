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
    # подъём, холодильник и взвешивание: в утреннем сообщении после «Я встал» (services/dayflow.py)
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
        names = ", ".join(o["name"] + (f" (главное, до {quest.hm(o['late'])})" if o.get("late") else "") for o in p["over"])
        once("over:" + names, f"⚠️ До отбоя не влезает: {names}. Открой квест и реши: сократить, убрать или на завтра.")
    return out


async def send_quest_tick(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Каждые 5 минут: сверка с живым планом."""
    try:
        from services import dayflow, morning
        day = await quest.get_day(uid)
        if not quest.woke_at(day):
            return  # пока не нажал «Я встал», бот молчит
        await dayflow.check_drift(bot, uid)
        day["_notify"] = morning.notify(await database.get_quest_state(uid))
        msgs = quest_messages(day, quest.now_min(), quest.today_key(), quest.js_weekday())
        if msgs:
            from handlers.proof import quest_keyboard
            for m in msgs:
                await bot.send_message(uid, m, reply_markup=quest_keyboard())
    except Exception as e:
        logger.error(f"Quest tick error: {e}")


async def send_reminders(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Каждую минуту: напоминания, которые поставил помощник."""
    try:
        state = await database.get_quest_state(uid)
        rem = state.get("reminders") or []
        today, now_m = quest.today_key(), quest.now_min()
        due = [r for r in rem if not r.get("sent") and (r.get("d", "") < today or (r.get("d") == today and quest._ev_min(r.get("at") or "04:59") <= now_m))]
        if not due:
            return
        for r in due:
            r["sent"] = True
        fresh = await database.get_quest_state(uid)
        fresh["reminders"] = rem
        await database.save_quest_state(uid, fresh)
        from handlers.proof import quest_keyboard
        for r in due:
            await bot.send_message(uid, f"⏰ <b>Напоминание</b>\n{html.escape(r.get('text', ''))}", reply_markup=quest_keyboard())
    except Exception as e:
        logger.error(f"Reminders error: {e}")


def step_kb(st: dict):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    done = (InlineKeyboardButton(text="📸 Кину пруф", callback_data=f"st:pf:{st['id']}") if st.get("proof")
            else InlineKeyboardButton(text="✅ Шаг готов", callback_data=f"st:done:{st['id']}"))
    return InlineKeyboardMarkup(inline_keyboard=[[done, InlineKeyboardButton(text="+5 минут", callback_data=f"st:ext:{st['id']}")]])


async def send_step_timer(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Каждую минуту: у идущего шага кончилось время → один раз написать (после «+5 минут» снова)."""
    try:
        state = await database.get_quest_state(uid)
        k = quest.today_key()
        day = (state.get("days") or {}).get(k)
        if not day or not day.get("cur"):
            return
        sc = quest.step_clock(day, quest.full_profile(state))
        if not sc or sc["left"] > 0:
            return
        st = sc["step"]
        mark = f"{st['id']}:{(day.get('sx') or {}).get(st['id'], 0)}"
        if day.get("sw") == mark:
            return
        day["sw"] = mark
        await database.save_quest_state(uid, state)
        await bot.send_message(uid, f"⏰ <b>{html.escape(st.get('name') or 'Шаг')}</b>: время шага вышло ({st['min']} мин).\n"
                                    f"«{html.escape(sc['item'].get('name') or '')}». Готово или ещё 5 минут?", reply_markup=step_kb(st))
    except Exception as e:
        logger.error(f"step timer: {e}")


async def send_flats(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Каждые 5 минут: новые объявления жилья (если поиск включён)."""
    from services import flats
    try:
        await flats.check(uid, lambda t, k: bot.send_message(uid, t, reply_markup=k, disable_web_page_preview=True))
    except Exception as e:
        logger.error(f"flats: {e}")


async def send_flats_day(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """20:00 — сводка по жилью за сутки (если поиск включён и было что-то новое)."""
    from services import flats
    try:
        s = await flats.get(uid)
        t = flats.day_summary(s) if s.get("on") else None
        if t:
            await bot.send_message(uid, t, reply_markup=flats.app_kb("🗂 Открыть список"))
    except Exception as e:
        logger.error(f"flats day: {e}")


async def send_quest_night(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """22:30 — итог дня и экран выкл."""
    try:
        from services import morning
        if not morning.notify(await database.get_quest_state(uid))["evening"]:
            return
        day = await quest.get_day(uid)
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
        await bot.send_message(uid, text)
    except Exception as e:
        logger.error(f"Quest night error: {e}")


async def send_quest_week(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Воскресенье 21:00 — итог недели по квесту."""
    try:
        from services import morning
        if not morning.notify(await database.get_quest_state(uid))["week"]:
            return
        state = await database.get_quest_state(uid)
        P = quest.full_profile(state)
        today = quest.lnow().date()
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
        await bot.send_message(uid, text)
    except Exception as e:
        logger.error(f"Quest week error: {e}")


async def send_review(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Воскресенье 20:30: разбор недели, бот записывает, что понял."""
    try:
        from services import review
        await review.send(bot, uid)
    except Exception as e:
        logger.error(f"Review error: {e}")


async def send_brain(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Мозг: раз в неделю или при новом этапе (поменялись цели) перечитать всё и поднять 0–2 вещи к месту."""
    try:
        import html
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        from services import brain
        state = await database.get_quest_state(uid)
        why = brain.due(state)
        if not why:
            return
        pins = await brain.review(state, changed=why == "stage")
        await database.save_quest_state(uid, state)
        if not pins:
            return
        head = "🧠 У тебя новый этап, перечитал мозг. Вот что теперь к месту:" if why == "stage" else "🧠 Перечитал мозг под то, что у тебя сейчас:"
        for i, x in enumerate(pins):
            text = (head + "\n\n" if i == 0 else "") + f"<b>{html.escape(x['t'])}</b>\n{html.escape(x['pin'].get('why') or x.get('s') or '')}"
            row = [InlineKeyboardButton(text="Сделать делом на завтра", callback_data=f"br:task:{x['id']}")] if x["pin"].get("act") in ("task", "habit") else []
            row.append(InlineKeyboardButton(text="Не сейчас", callback_data=f"br:later:{x['id']}"))
            await bot.send_message(uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[row]))
    except Exception as e:
        logger.error(f"Brain review error: {e}")


async def send_money(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Регулярные платежи: накануне напомнить, в день списать самим. Долги: напомнить в срок."""
    try:
        from services import money
        state = await database.get_quest_state(uid)
        m = money.get(state)
        t = quest.lnow()
        for r, o, warn in money.auto_pay(state):  # в свой день платёж списывается сам
            acc = money.acct(m, o["acc"])
            await bot.send_message(uid, f"💳 Списал регулярный платёж: {html.escape(r['n'])} €{r['amt']:g} ({acc['n'].lower()}). "
                                   f"На жизнь осталось €{money.free(state):g}." + (f"\n{warn}" if warn else ""))
        tm = (t + timedelta(days=1)).strftime("%Y-%m-%d")
        for r in m["reg"]:  # накануне: напомнить, чтобы на счёте хватило
            if r.get("paid") == tm[:7] or money.reg_due(r, tm[:7]) != int(tm[8:10]) or r.get("rem") == tm:
                continue
            r["rem"] = tm
            await bot.send_message(uid, f"💳 Завтра спишется {html.escape(r['n'])} €{r['amt']:g} ({money.acct(m, r.get('acc'))['n'].lower()}).")
        for dbt in money.debts_open(m):  # долги: напомнить в день «вернуть до»
            if dbt.get("due") == quest.today_key() and dbt.get("rem") != dbt["due"]:
                dbt["rem"] = dbt["due"]
                await bot.send_message(uid, f"🤝 Сегодня срок долга: {html.escape(dbt['who'])} " + ("должен тебе" if dbt.get("dir") == "owe_me" else "— ты должен") + f" €{dbt['amt']:g}. Вернули — напиши «{html.escape(dbt['who'])} вернул».")
        await database.save_quest_state(uid, state)
    except Exception as e:
        logger.error(f"Money reminder error: {e}")


async def money_snapshot(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Каждый вечер: остатки счетов в историю (график на странице счёта)."""
    try:
        from services import money
        state = await database.get_quest_state(uid)
        if state.get("money"):
            money.snapshot(state)
            await database.save_quest_state(uid, state)
    except Exception as e:
        logger.error(f"Money snapshot error: {e}")


async def money_month(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """1-го числа: итог прошлого месяца с одним советом."""
    try:
        from services import claude, money
        state = await database.get_quest_state(uid)
        m = money.get(state)
        prev = (quest.lnow().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        if not money.month_ops(m, prev):
            return
        facts = money.month_report(state, prev)
        try:
            r = await claude.client.messages.create(model="claude-haiku-4-5", max_tokens=400, messages=[{"role": "user", "content": (
                "Напиши человеку итог месяца по деньгам по фактам ниже. По-русски, по-дружески, 4–6 коротких строк: главное число, "
                "на что ушло больше всего, что лучше или хуже прошлого месяца, и ОДИН конкретный совет на новый месяц. Без длинных тире, без markdown, 1 эмодзи.\n\n" + facts)}])
            text = "".join(b.text for b in r.content if b.type == "text").strip() or facts
        except Exception:
            text = facts
        await bot.send_message(uid, "💰 <b>Итог месяца</b>\n" + html.escape(text))
    except Exception as e:
        logger.error(f"Money month error: {e}")


async def money_savings_ask(bot: Bot, uid: int = TELEGRAM_USER_ID) -> None:
    """Воскресенье: сверить сбережения (акции и крипта меняются сами), чтобы был график роста."""
    try:
        from services import money
        state = await database.get_quest_state(uid)
        m = money.get(state)
        accs = [a for a in money.visible(m) if a.get("t") in ("stocks", "crypto") and money.balance(m, a["id"])]
        if not accs:
            return
        now = ", ".join(f"{money.TYPES[a['t']][1].lower()} €{money.balance(m, a['id']):g}" for a in accs)
        await bot.send_message(uid, f"📈 Сверка сбережений. Сейчас записано: {now}.\nСколько там на самом деле? Напиши, например: «акции 900, крипта 23500».")
    except Exception as e:
        logger.error(f"Savings ask error: {e}")


async def send_backup(bot: Bot) -> None:
    """Каждую ночь копия базы владельцу в Телеграм: если диск на сервере умрёт, всё восстановится из чата."""
    import os
    from aiogram.types import FSInputFile
    try:
        path = await database.backup_file()
        try:
            await bot.send_document(TELEGRAM_USER_ID, FSInputFile(path, filename=f"backup-{quest.today_key()}.db.gz"),
                                    caption="💾 Копия базы за сутки. Ничего делать не надо, просто пусть лежит.", disable_notification=True)
        finally:
            for p in (path, path[:-3]):
                if os.path.exists(p):
                    os.remove(p)
    except Exception as e:
        logger.error(f"Backup error: {e}")


async def users() -> list[int]:
    """Все, кому открыт бот: владелец и одобренные им. У каждого свой профиль и свои данные."""
    ids = [TELEGRAM_USER_ID]
    try:
        ids += [int(uid) for uid, _n, st in await database.access_list() if st == "ok" and int(uid) != TELEGRAM_USER_ID]
    except Exception as e:
        logger.error(f"users list error: {e}")
    return ids


def each(fn):
    """Задача по расписанию для каждого человека отдельно: ошибка у одного не мешает остальным."""
    async def run(bot: Bot) -> None:
        from services import journal
        journal.SRC.set("бот (авто)")
        for uid in await users():
            try:
                await fn(bot, uid)
            except Exception as e:
                logger.error(f"{fn.__name__} for {uid}: {e}")
    run.__name__ = fn.__name__
    return run


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)

    # Квест дня: живой план, проверка каждые 5 минут
    scheduler.add_job(each(send_quest_tick), "cron", minute="*/5", kwargs={"bot": bot})
    scheduler.add_job(each(send_reminders), "cron", minute="*", kwargs={"bot": bot})
    scheduler.add_job(each(send_flats), "cron", minute="*/5", second=40, kwargs={"bot": bot})
    scheduler.add_job(each(send_flats_day), "cron", hour=20, minute=0, second=50, kwargs={"bot": bot})
    scheduler.add_job(each(send_step_timer), "cron", minute="*", second=20, kwargs={"bot": bot})
    scheduler.add_job(each(send_quest_night), "cron", hour=22, minute=30, kwargs={"bot": bot})
    scheduler.add_job(each(send_quest_week), "cron", day_of_week="sun", hour=21, minute=0, kwargs={"bot": bot})
    scheduler.add_job(each(send_review), "cron", day_of_week="sun", hour=20, minute=30, kwargs={"bot": bot})
    scheduler.add_job(each(send_brain), "cron", hour=12, minute=40, kwargs={"bot": bot})
    scheduler.add_job(send_backup, "cron", hour=4, minute=30, kwargs={"bot": bot})
    scheduler.add_job(each(send_money), "cron", hour=9, minute=5, kwargs={"bot": bot})
    scheduler.add_job(each(money_snapshot), "cron", hour=23, minute=55, kwargs={"bot": bot})
    scheduler.add_job(each(money_month), "cron", day=1, hour=10, minute=20, kwargs={"bot": bot})
    scheduler.add_job(each(money_savings_ask), "cron", day_of_week="sun", hour=19, minute=0, kwargs={"bot": bot})

    return scheduler
