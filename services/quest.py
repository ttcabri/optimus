"""Квест дня: общая логика для Mini App, пруфов и напоминаний.

Состояние: один JSON на пользователя, формат совпадает с webapp/index.html:
{"days": {"YYYY-MM-DD": {"done": [...], "at": {"id": "HH:MM"}, "games": 0, "smokes": 0,
                         "firstSmoke": "HH:MM", "custom": [{"id", "name"}], "del": [...]}},
 "body": [{"d": "YYYY-MM-DD", "kg": 82.5}], "lifts": [{"d", "t", "clean"}]}
"""
import json
import re
import time
from datetime import datetime, timedelta

import pytz
from pathlib import Path

import database
from config import TIMEZONE

TEMPLATES = json.loads((Path(__file__).with_name("routine.json")).read_text(encoding="utf-8"))
ANDREY_STEPS = {"b1", "b2", "b3", "b4", "b5"}  # по ним узнаём старые данные Андрея без распорядка


def now() -> datetime:
    return datetime.now(pytz.timezone(TIMEZONE))


def today_key() -> str:
    return now().strftime("%Y-%m-%d")


def tomorrow_key() -> str:
    return (now() + timedelta(days=1)).strftime("%Y-%m-%d")


def hhmm() -> str:
    return now().strftime("%H:%M")


DEF_PROFILE = {"reward": "Трава", "name": "", "wake": "08:00", "bed": "23:30"}


def full_profile(state: dict | None) -> dict:
    """Анкета человека вместе с распорядком. Нет своего распорядка: шаблон.
    Старые данные Андрея (шаги видео b1–b5) получают его шаблон, все остальные: стартовый."""
    state = state or {}
    src = state.get("profile") or {}
    p = dict(DEF_PROFILE)
    p.update({k: v for k, v in src.items() if v is not None})
    if not isinstance(src.get("routine"), list):
        old = any(ANDREY_STEPS & set(d.get("done") or []) for d in (state.get("days") or {}).values() if isinstance(d, dict))
        tpl = TEMPLATES["andrey" if old else "starter"]
        r = json.loads(json.dumps(tpl["routine"]))
        for it in r:  # старые поля анкеты, пока не было распорядка
            if it["id"] == "video" and src.get("videoMin"):
                it["dur"] = int(src["videoMin"])
            if it["id"] == "gym" and src.get("gymDays"):
                it["days"] = list(src["gymDays"])
            if it["id"] == "walk" and src.get("gymDays"):
                it["days"] = [d for d in range(7) if d not in src["gymDays"]]
            if it["id"] in ("cbr", "eng") and (src.get("req") or {}).get(it["id"]) == 0:
                it.pop("main", None)
        p["routine"] = r
        if "penalty" not in src:
            p["penalty"] = tpl["penalty"]
        if "goal" not in src:
            p["goal"] = tpl["goal"]
    p.setdefault("penalty", None)
    p.setdefault("goal", None)
    return p


def prof(day_or_state: dict | None) -> dict:
    """Анкета: из day["_profile"] (get_day кладёт уже полную) или из state."""
    x = day_or_state or {}
    if isinstance(x.get("_profile"), dict):
        return x["_profile"] if isinstance(x["_profile"].get("routine"), list) else full_profile({"profile": x["_profile"]})
    return full_profile(x)


# --- Распорядок: дела из анкеты ---

def items(P: dict) -> list[dict]:
    return [it for it in P.get("routine") or [] if it.get("id") and it["id"] != "_custom"]


def item(P: dict, qid: str) -> dict | None:
    return next((it for it in items(P) if it["id"] == qid), None)


def on_day(it: dict, wd: int) -> bool:
    """wd как в JS: 0 вс .. 6 сб."""
    return not it.get("days") or wd in it["days"]


def step_ids(it: dict | None) -> list[str]:
    return [s["id"] for s in (it or {}).get("steps") or []]


def parent_of(P: dict, sid: str) -> dict | None:
    return next((it for it in items(P) if sid in step_ids(it)), None)


def item_done(day: dict | None, it: dict) -> bool:
    done = (day or {}).get("done") or []
    st = step_ids(it)
    return all(s in done for s in st) if st else it["id"] in done


def item_t(day: dict, it: dict):
    st = step_ids(it)
    return (day.get("at") or {}).get(st[-1] if st else it["id"])


def pen_item(P: dict) -> dict | None:
    return item(P, (P.get("penalty") or {}).get("task") or "")


def proof_ids(P: dict) -> set[str]:
    """Закрываются только пруфом через бота."""
    out = {"fine"}
    for it in items(P):
        if it.get("proof"):
            out.add(it["id"])
        for s in it.get("steps") or []:
            if s.get("proof"):
                out.add(s["id"])
                out.add("vid2")
        if it.get("proof") and (it.get("bonus") or pen_item(P) is it):
            out.add("vid2")
    return out


def names(P: dict) -> dict:
    n = {}
    for it in items(P):
        n[it["id"]] = it["name"]
        for s in it.get("steps") or []:
            n[s["id"]] = f"{it['name']}: {s['name']}"
    pi = pen_item(P)
    n["vid2"] = (pi or {}).get("bonus") or "Повтор"
    n["fine"] = f"Штраф €{(P.get('penalty') or {}).get('fine') or 0}"
    return n


def bed_min(p: dict) -> int:
    m = to_min(p["bed"])
    return m + 1440 if m < 300 else m


def day_end(p: dict) -> str:
    return hm(min(bed_min(p) - 45, 1439))


def _day(state: dict, key: str) -> dict:
    day = state["days"].setdefault(key, {})
    day.setdefault("done", [])
    day.setdefault("at", {})
    day.setdefault("custom", [])
    return day


# --- Провал и наказания: провал = день без штрафного дела (у Андрея видео). Долг не копится: один повтор и штраф.

def failed_day(state: dict, key: str) -> bool:
    if key >= today_key():
        return False
    P = full_profile(state)
    it = pen_item(P)
    if not it or not on_day(it, (datetime.strptime(key, "%Y-%m-%d").weekday() + 1) % 7):
        return False
    if item_done(state.get("days", {}).get(key), it):
        return False
    return any(k <= key and (d.get("done") or d.get("firstSmoke")) for k, d in state.get("days", {}).items())


def has_debt(state: dict, key: str | None = None) -> bool:
    key = key or today_key()
    prev = (datetime.strptime(key, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return failed_day(state, prev)


def reqs(day: dict, weekday: int | None = None) -> list[dict]:
    """Главные дела дня (отмечены в распорядке): закрыл все, награда открыта. weekday как в Python: пн=0."""
    w = now().weekday() if weekday is None else weekday
    P = prof(day)
    skip = set(day.get("skip", []))
    pi = pen_item(P)
    r = [{"id": it["id"], "name": it["name"],
          "t": item_t(day, it) or ("skip" if it["id"] in skip and it is not pi else None)}
         for it in items(P) if it.get("main") and on_day(it, (w + 1) % 7)]
    if day.get("_debt") and pi:
        at = day.get("at", {})
        r.append({"id": "debt", "name": pi.get("debt") or f"Долг: ещё раз «{pi['name']}»", "t": at.get("vid2")})
        if (P.get("penalty") or {}).get("fine"):
            r.append({"id": "fine", "name": f"Штраф €{P['penalty']['fine']}", "t": at.get("fine")})
    return r


def day_closed(day: dict | None, weekday: int | None = None) -> bool:
    """День закрыт: все главные дела сделаны (или убраны на сегодня)."""
    r = reqs(day, weekday) if day else []
    return bool(r) and all(x["t"] for x in r)


def reward_state(day: dict | None, weekday: int | None = None) -> str:
    """pending: копим · open: всё закрыто, награда твоя · taken: награду взял."""
    if not day:
        return "pending"
    if day.get("firstSmoke"):
        return "taken"
    return "open" if day_closed(day, weekday) else "pending"


def reward_line(day: dict, weekday: int | None = None) -> str:
    r = reqs(day, weekday)
    n = sum(1 for x in r if x["t"])
    st = reward_state(day, weekday)
    marks = " · ".join(("✅ " if x["t"] else "▫️ ") + x["name"] for x in r)
    head = {
        "pending": f"🌿 {'Штрафной день' if day.get('_debt') else 'Награда дня'}: {n} из {len(r)}",
        "open": "🌿 Награда открыта. Заслужил.",
        "taken": "🌿 Награда взята.",
    }[st]
    return head + "\n" + marks


# --- Замеры времени (та же логика, что в webapp/index.html) ---

def log_ev(day: dict, e: str, qid: str = "") -> None:
    day["ev"] = (day.get("ev") or []) + [[hhmm(), e, qid]]
    day["ev"] = day["ev"][-200:]


def _rest_overlap(day: dict, a: int, b: int) -> int:
    return sum(max(0, min(b, s + m) - max(a, s)) for s, m in rests_of(day))


def record_span(day: dict, qid: str, P: dict | None = None) -> None:
    """Сколько заняло дело: от «начал» (или прошлого закрытого дела) до сейчас, минус отдых."""
    n = now()
    now_m = n.hour * 60 + n.minute
    s, g = None, 1
    cur = day.get("cur") or {}
    par = parent_of(P or prof(day), qid)
    if cur.get("id") == qid or (par and cur.get("id") == par["id"]):
        s, g = to_min(cur["at"]), 0
    marks = [to_min(t) for k, t in (day.get("at") or {}).items() if k != qid]
    marks += [st + m for st, m in rests_of(day)]
    marks = [t for t in marks if t <= now_m]
    if marks and (s is None or max(marks) > s):
        s = max(marks)
    if s is None or s > now_m:
        return
    m = now_m - s - _rest_overlap(day, s, now_m)
    if m < 1:
        return
    day["span"] = dict(day.get("span") or {}, **{qid: {"s": hm(s), "e": hm(now_m), "m": m, "g": g}})


def learned(state: dict) -> dict:
    """Медиана последних 10 замеров по каждому делу, если набралось 3 нормальных."""
    P = full_profile(state)
    base = {it["id"]: it.get("dur", 30) for it in items(P)}
    multi = [it for it in items(P) if it.get("steps")]
    vals: dict[str, list[int]] = {}
    for k in sorted(state.get("days", {}))[-40:]:
        sp = state["days"][k].get("span") or {}
        for qid, v in sp.items():
            if qid not in base or any(qid == it["id"] for it in multi):
                continue
            if v.get("g") and v.get("m", 0) > base[qid] * 2.5:
                continue
            vals.setdefault(qid, []).append(v["m"])
        for it in multi:
            st = step_ids(it)
            if all(b in sp for b in st):
                vals.setdefault(it["id"], []).append(sum(sp[b]["m"] for b in st))
            for b in st:
                if b in sp:
                    vals.setdefault(b, []).append(sp[b]["m"])
    out = {}
    for qid, arr in vals.items():
        last = arr[-10:]
        if len(last) < 3:
            continue
        m0 = _median(last)
        ok = [x for x in last if m0 * 0.3 <= x <= m0 * 2.5]
        if len(ok) < 3:
            continue
        out[qid] = {"min": max(5, int(round(_median(ok) / 5) * 5)), "n": len(ok)}
    return out


def _median(a: list) -> float:
    b = sorted(a)
    n = len(b)
    return b[(n - 1) // 2] if n % 2 else (b[n // 2 - 1] + b[n // 2]) / 2


async def get_day(telegram_id: int) -> dict:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, today_key())
    day["_debt"] = has_debt(state)
    day["_profile"] = full_profile(state)
    day["_theory_i"] = (state.get("theory") or {}).get("i", 0)
    day["_learn"] = learned(state)
    day["_reminders"] = state.get("reminders") or []
    day["_chores"] = chores_for_plan(state, telegram_id)
    from services import house as _house
    day["_expiring"] = [x["name"] for x in _house.expiring(state["house"])] if isinstance(state.get("house"), dict) else []
    return day


def chores_for_plan(state: dict, uid=None) -> list[dict]:
    """Что из Дома человек взял на себя: дела по быту и поход за покупками."""
    from services import house
    h = state.get("house")
    if not isinstance(h, dict):
        return []
    uid = uid if uid is not None else h.get("owner")
    solo = len(h.get("members") or []) <= 1
    mine = (lambda w: house.is_me(w, uid) or (solo and str(w) == "me"))
    today = today_key()
    done = set((state.get("days", {}).get(today) or {}).get("done", []))
    # взял на себя = дело в плане, даже если срок ещё не подошёл
    out = [{"id": "ch_" + c["id"], "name": c["name"], "dur": int(c.get("dur") or 20)}
           for c in h.get("chores", []) if mine(c.get("who")) or (c.get("last") == today and "ch_" + c["id"] in done)]
    buys = [x for _, x in house.my_buys(h, uid)]
    if buys or "buy" in done:
        out.append({"id": "buy", "name": "Купить: " + ", ".join(x["name"] for x in buys) if buys else "Покупки",
                    "dur": min(60, 20 + 5 * len(buys))})
    return out


async def mark_done(telegram_id: int, ids: list[str]) -> dict:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, today_key())
    P = full_profile(state)
    t = hhmm()
    for i in ids:
        if i not in day["done"]:
            record_span(day, i, P)
            day["done"].append(i)
            day["at"][i] = t
            log_ev(day, "done", i)
    drop_cur(day, P, ids)
    await database.save_quest_state(telegram_id, state)
    day["_debt"] = has_debt(state)
    day["_profile"] = P
    return day


def drop_cur(day: dict, P: dict, ids: list[str]) -> None:
    """Закрыл начатое дело (или последний шаг многошагового): «идёт сейчас» снимаем."""
    cur = (day.get("cur") or {}).get("id")
    it = item(P, cur or "")
    if cur in ids or (it and it.get("steps") and item_done(day, it)):
        day.pop("cur", None)


async def add_smoke(telegram_id: int) -> dict:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, today_key())
    if not day.get("smokes"):
        day["firstSmoke"] = hhmm()
    day["smokes"] = day.get("smokes", 0) + 1
    await database.save_quest_state(telegram_id, state)
    day["_debt"] = has_debt(state)
    day["_profile"] = full_profile(state)
    return day


def parse_task(text: str) -> tuple[str, str | None, int]:
    """«отвезти Audi 10:00 1ч» → ("отвезти Audi", "10:00", 60). Без длительности 30 мин."""
    import re
    t = " " + text.strip() + " "
    at = None
    m = re.search(r"(?:\sв)?\s(\d{1,2})[:.](\d{2})(?=\s)", t)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        at = f"{int(m.group(1)):02d}:{m.group(2)}"
        t = t[:m.start()] + " " + t[m.end():]
    dur = 30
    m = re.search(r"\s(\d+(?:[.,]\d+)?)\s*(ч|час|часа|часов|мин|м)(?=[\s.,]|$)", t)
    if m:
        v = float(m.group(1).replace(",", "."))
        dur = int(v * 60) if m.group(2).startswith("ч") else int(v)
        dur = max(5, min(dur, 480))
        t = t[:m.start()] + " " + t[m.end():]
    name = " ".join(t.split())[:80]
    return name, at, dur


async def add_custom(telegram_id: int, names: list[str], tomorrow: bool = False) -> list[str]:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, tomorrow_key() if tomorrow else today_key())
    added = []
    for i, raw in enumerate(names):
        name, at, dur = parse_task(raw)
        if not name:
            continue
        item = {"id": f"t{int(time.time() * 1000):x}{i}", "name": name, "dur": dur}
        if at:
            item["at"] = at
        day["custom"].append(item)
        added.append(name + (f" в {at}" if at else "") + f", {dur} мин")
    await database.save_quest_state(telegram_id, state)
    return added


async def log_weight(telegram_id: int, kg: float) -> list[dict]:
    state = await database.get_quest_state(telegram_id)
    body = [b for b in state.get("body", []) if b.get("d") != today_key()]
    body.append({"d": today_key(), "kg": round(kg, 1)})
    state["body"] = body
    await database.save_quest_state(telegram_id, state)
    return body


def merge_incoming(server: dict, incoming: dict) -> dict:
    """Mini App шлёт полное состояние. Не теряем то, что бот записал, пока приложение было открыто."""
    if not isinstance(incoming, dict) or not isinstance(incoming.get("days"), dict):
        return server
    incoming.setdefault("body", server.get("body", []))
    incoming.setdefault("lifts", server.get("lifts", []))
    have_l = {(x.get("d"), x.get("ex"), x.get("kg"), x.get("reps")) for x in incoming["lifts"]}
    incoming["lifts"] += [x for x in server.get("lifts", []) if (x.get("d"), x.get("ex"), x.get("kg"), x.get("reps")) not in have_l]
    incoming.pop("_me", None)
    # дом: слияние по записям (кто позже менял, тот и прав)
    if server.get("house") or incoming.get("house"):
        from services import house as _house
        incoming["house"] = _house.merge(server.get("house"), incoming.get("house"))
    # то, что ведёт только бот: приложение это не меняет, берём с сервера
    for k in ("notes", "temp", "theory", "home", "loc", "insights"):
        if server.get(k) is not None:
            incoming[k] = server[k]
    ss, si = server.get("settings") or {}, incoming.get("settings") or {}
    if ss and not si.get("u", 0) > ss.get("u", 0):  # режим голоса: приложение меняет только поверх более старого
        incoming["settings"] = ss
    if server.get("reminders") or incoming.get("reminders"):
        byid = {r.get("id"): r for r in incoming.get("reminders") or []}
        for r in server.get("reminders") or []:
            if r.get("id") not in byid or r.get("sent"):
                byid[r.get("id")] = r
        incoming["reminders"] = list(byid.values())
    for k in ("prefs", "gifts", "claims", "goals", "notify", "tgoals"):
        if not incoming.get(k) and server.get(k):
            incoming[k] = server[k]
    sp, ip = server.get("profile") or {}, incoming.get("profile") or {}
    if sp and (not ip or sp.get("u", 0) > ip.get("u", 0)):  # распорядок мог поменять бот
        incoming["profile"] = sp
    s_days = server.get("days", {})
    PROOF = proof_ids(full_profile(server)) | proof_ids(full_profile(incoming))

    for key, sday in s_days.items():
        iday = incoming["days"].setdefault(key, {})
        iday.setdefault("done", [])
        iday.setdefault("at", {})
        iday.setdefault("custom", [])
        # пруфы, засчитанные ботом
        for q in sday.get("done", []):
            if q in PROOF and q not in iday["done"]:
                iday["done"].append(q)
                if q in sday.get("at", {}):
                    iday["at"][q] = sday["at"][q]
        # журнал и замеры, записанные ботом
        if sday.get("ev"):
            seen = {tuple(e) for e in iday.get("ev") or []}
            iday["ev"] = sorted((iday.get("ev") or []) + [e for e in sday["ev"] if tuple(e) not in seen], key=lambda e: e[0])[-200:]
        if sday.get("span"):
            iday["span"] = {**sday["span"], **(iday.get("span") or {})}
        if sday.get("mSent"):  # утренняя сводка уже ушла: не слать повторно из-за старой копии в приложении
            iday["mSent"] = True
        # трава, отмеченная в боте
        if sday.get("smokes", 0) > iday.get("smokes", 0):
            iday["smokes"] = sday["smokes"]
        fs = [t for t in (sday.get("firstSmoke"), iday.get("firstSmoke")) if t]
        if fs:
            iday["firstSmoke"] = min(fs)
        # дела, добавленные через бота
        have = {c.get("id") for c in iday["custom"]}
        deleted = set(iday.get("del", [])) | set(sday.get("del", []))
        for c in sday.get("custom", []):
            if c.get("id") not in have and c.get("id") not in deleted:
                iday["custom"].append(c)
        if deleted:
            iday["del"] = sorted(deleted)

    # вес, записанный в боте
    dates = {b.get("d") for b in incoming["body"]}
    for b in server.get("body", []):
        if b.get("d") not in dates:
            incoming["body"].append(b)
    incoming["body"].sort(key=lambda b: b.get("d", ""))

    # цифры канала: история из бота и из приложения, по дате
    if server.get("chanLog") or incoming.get("chanLog"):
        byd = {x.get("d"): x for x in server.get("chanLog") or []}
        byd.update({x.get("d"): x for x in incoming.get("chanLog") or []})
        incoming["chanLog"] = [byd[k] for k in sorted(byd) if k][-60:]
        if incoming["chanLog"]:
            goals = dict(incoming.get("goals") or {})
            goals["subs"] = incoming["chanLog"][-1].get("subs", goals.get("subs", 0))
            incoming["goals"] = goals

    # пруф-квесты нельзя отметить из приложения вручную
    for key, iday in incoming["days"].items():
        sdone = s_days.get(key, {}).get("done", [])
        iday["done"] = [q for q in iday.get("done", []) if q not in PROOF or q in sdone]
        iday["at"] = {q: t for q, t in iday.get("at", {}).items() if q in iday["done"]}
    return incoming


def day_summary(day: dict) -> str:
    P = prof(day)
    w = now().weekday()
    lines = [("✅ " if x["t"] and x["t"] != "skip" else "➖ " if x["t"] else "❌ ") + x["name"] for x in reqs(day, w)]
    pi = pen_item(P)
    if pi and "vid2" in day.get("done", []) and not day.get("_debt"):
        lines.append(f"{pi.get('bonus') or 'Бонус'}: есть 🔥")
    open_custom = [c["name"] for c in day.get("custom", []) if c["id"] not in day.get("done", [])]
    if open_custom:
        lines.append("Не закрыто: " + "; ".join(open_custom))
    lines.append("")
    lines.append(reward_line(day, w))
    if day.get("smokes"):
        lines.append(f"{P.get('reward') or 'Награда'}: {day['smokes']}, первый раз в {day.get('firstSmoke')}")
    return "\n".join(lines)


# --- Живой план (та же логика, что в webapp/index.html) ---

GAME_MIN = 45


def to_min(t: str) -> int:
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


def hm(m: int) -> str:
    m = int(round(m))
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def js_weekday() -> int:
    return (now().weekday() + 1) % 7


def rests_of(day: dict) -> list[tuple[int, int]]:
    """Отдых: [(начало в минутах, длительность)]. Старый формат "HH:MM" = 45 минут."""
    out = []
    for r in day.get("gl", []):
        if isinstance(r, str):
            out.append((to_min(r), GAME_MIN))
        elif isinstance(r, dict) and r.get("at"):
            out.append((to_min(r["at"]), int(r.get("min") or GAME_MIN)))
    return out


def build_plan(day: dict, now_min: int | None = None, wd: int | None = None) -> dict:
    now_m = now_min if now_min is not None else now().hour * 60 + now().minute
    wd = js_weekday() if wd is None else wd
    done = set(day.get("done", []))
    skip = set(day.get("skip", []))
    dur_over = day.get("dur", {})
    learn = day.get("_learn") or {}

    P = prof(day)
    multi = {it["id"]: it for it in items(P) if it.get("steps")}

    def is_done(qid):
        return item_done(day, multi[qid]) if qid in multi else qid in done

    def dur_of(qid, base):
        if qid in dur_over:
            return dur_over[qid]
        if qid in learn:
            base = learn[qid]["min"]
        if qid in multi:
            st = step_ids(multi[qid])
            return max(20, round(base * sum(1 for b in st if b not in done) / len(st)))
        return base

    debt = day.get("_debt")
    pi = pen_item(P)
    queue, has_custom = [], False

    def extra():  # долг или бонус за штрафное дело
        if pi and (debt or (pi.get("bonus") and item_done(day, pi) and "vid2" not in skip)):
            queue.append({"id": "vid2", "name": (pi.get("debt") or f"Долг: ещё раз «{pi['name']}»") if debt else pi["bonus"],
                          "dur": pi.get("dur", 30), "fixed": None, "minor": False})

    def customs():
        for c in day.get("custom", []):
            queue.append({"id": c["id"], "name": c["name"], "dur": c.get("dur", 30), "fixed": c.get("at"), "minor": False})
        for c in day.get("_chores") or []:
            if c["id"] not in skip:
                queue.append({"id": c["id"], "name": c["name"], "dur": c["dur"], "fixed": None, "minor": True})
        extra()

    for it in P.get("routine") or []:
        qid = it.get("id")
        if qid == "_custom":
            has_custom = True
            customs()
            continue
        if not qid or not on_day(it, wd) or (qid in skip and it is not pi):
            continue
        if it.get("main") and debt and (P.get("penalty") or {}).get("fine") and not any(q["id"] == "fine" for q in queue):
            queue.append({"id": "fine", "name": f"Штраф: €{P['penalty']['fine']} в {P['penalty'].get('to') or 'копилку'}", "dur": 5, "fixed": None, "minor": False})
        if it.get("until") and now_m > to_min(it["until"]) and not is_done(qid):
            continue  # утро прошло: завтрак в вечер не тащим
        fixed = day_end(P) if it.get("at") == "end" else it.get("at")
        queue.append({"id": qid, "name": it["name"], "dur": it.get("dur", 30), "fixed": fixed, "minor": bool(it.get("minor"))})
    if not has_custom:
        customs()

    out, over = [], []
    wake = day.get("at", {}).get("wake")
    cur = max(now_m, to_min(P["wake"])) if not wake else now_m
    for g, mins in sorted(rests_of(day)):
        if g + mins > now_m:
            s = g if g <= now_m else max(g, cur)
            out.append({"game": True, "start": s, "end": s + mins})
            cur = max(cur, s + mins)

    pending = [q for q in queue if not is_done(q["id"])]
    started = day.get("cur")
    if started:
        idx = next((i for i, q in enumerate(pending) if q["id"] == started.get("id")), -1)
        if idx >= 0:
            q = pending.pop(idx)
            s = to_min(started["at"])
            e = s + dur_of(q["id"], q["dur"])
            out.append({**q, "start": s, "end": max(e, now_m + 5), "started": True, "late": max(0, now_m - e)})
            cur = max(cur, e, now_m + 5)

    anchors = sorted([q for q in pending if q["fixed"]], key=lambda q: to_min(q["fixed"]))
    flex = [q for q in pending if not q["fixed"]]
    end = to_min(day_end(P))

    def place(a):
        nonlocal cur
        s = max(to_min(a["fixed"]), cur)
        out.append({**a, "start": s, "end": s + dur_of(a["id"], a["dur"]), "anchor": True, "late": max(0, cur - to_min(a["fixed"]))})
        cur = s + dur_of(a["id"], a["dur"])

    for q in flex:
        du = dur_of(q["id"], q["dur"])
        while anchors and to_min(anchors[0]["fixed"]) < cur + du and anchors[0]["fixed"] != day_end(P):
            place(anchors.pop(0))
        if cur + du > end:
            optional = q["id"] == "vid2" and not debt
            if not optional:  # бонусы молча не влезают, без тревоги
                over.append({**q, "dur": du})
            continue
        out.append({**q, "start": cur, "end": cur + du})
        cur += du
    while anchors:
        place(anchors.pop(0))
    return {"items": out, "over": over}


def day_view(state: dict, key: str) -> dict:
    """День из истории с анкетой и долгом: для reqs/day_closed по прошлым дням."""
    return dict(state.get("days", {}).get(key) or {}, _profile=full_profile(state), _debt=has_debt(state, key))


def closed_streak(state: dict) -> int:
    """Сколько дней подряд закрыто всё главное (сегодня считается, если уже закрыт)."""
    d0 = now().date()
    n, i = 0, 0 if day_closed(day_view(state, d0.strftime("%Y-%m-%d")), d0.weekday()) else 1
    while i < 400:
        k = d0 - timedelta(days=i)
        if not day_closed(day_view(state, k.strftime("%Y-%m-%d")), k.weekday()):
            break
        n, i = n + 1, i + 1
    return n


# --- Правка распорядка (бот и приложение) ---
ROUTINE_FIELDS = {"name": str, "sub": str, "dur": int, "days": list, "at": str, "until": str,
                  "minor": int, "main": int, "proof": int, "hint": str, "check": str, "goal": str}  # goal: body = привычка для тела


def routine_edit(state: dict, op: str, a: dict) -> str:
    """op: add, update, remove. Пишет полный распорядок в анкету, с отметкой u для слияния."""
    P = full_profile(state)
    r = [dict(x) for x in P["routine"]]
    nm = str(a.get("name") or "").strip()[:60]
    it = next((x for x in r if x.get("id") == a.get("id")), None) or \
        next((x for x in r if nm and x.get("name", "").lower() == nm.lower()), None)
    if op == "remove":
        if not it:
            return "не нашёл такое дело в распорядке"
        r = [x for x in r if x is not it]
        if (P.get("penalty") or {}).get("task") == it["id"]:
            P["penalty"] = None
        msg = f"убрано из распорядка: {it['name']}"
    else:
        if op == "add" or not it:
            if not nm:
                return "нужно название"
            it = {"id": "r" + format(int(time.time() * 1000), "x"), "name": nm, "dur": 30}
            pos = next((i for i, x in enumerate(r) if x.get("at") == "end"), len(r))
            r.insert(pos, it)
        for k, typ in ROUTINE_FIELDS.items():
            if k not in a or a[k] is None or (k == "name" and str(a[k]).strip().lower() == it.get("name", "").lower()):
                continue
            v = a[k]
            if k == "days":
                v = sorted({int(d) for d in v if 0 <= int(d) <= 6}) or None
            elif k == "dur":
                v = max(5, min(int(v), 480))
            elif k in ("minor", "main", "proof"):
                v = 1 if v else 0
            elif k in ("at", "until") and v not in ("", "end") and not re.match(r"^\d{2}:\d{2}$", str(v)):
                continue
            else:
                v = typ(v)[:300]
            if v in (None, "", 0) and k != "dur":
                it.pop(k, None)
            else:
                it[k] = v
        msg = f"в распорядке: {it['name']}, {it.get('dur', 30)} мин" + (", главное" if it.get("main") else "") + \
              (", пруф" if it.get("proof") else "") + (f", дни {it['days']}" if it.get("days") else ", каждый день") + \
              (f", в {it['at']}" if it.get("at") and it["at"] != "end" else "")
    prof_ = dict(state.get("profile") or {})
    prof_.update({"routine": r, "penalty": P.get("penalty"), "goal": P.get("goal"), "u": int(time.time() * 1000)})
    state["profile"] = prof_
    return msg


def habit_streak(state: dict, qid: str) -> int:
    """Серия привычки: дни подряд, когда дело сделано. Дни без него по распорядку не рвут серию, сегодня ещё можно успеть."""
    P = full_profile(state)
    it = item(P, qid)
    if not it:
        return 0
    d0, n = now().date(), 0
    for i in range(400):
        k = d0 - timedelta(days=i)
        if not on_day(it, (k.weekday() + 1) % 7):
            continue
        if item_done(state.get("days", {}).get(k.strftime("%Y-%m-%d")), it):
            n += 1
        elif i:
            break
    return n
