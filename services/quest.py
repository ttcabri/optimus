"""Квест дня: общая логика для Mini App, пруфов и напоминаний.

Состояние: один JSON на пользователя, формат совпадает с webapp/index.html:
{"days": {"YYYY-MM-DD": {"done": [...], "at": {"id": "HH:MM"}, "games": 0, "smokes": 0,
                         "firstSmoke": "HH:MM", "custom": [{"id", "name"}], "del": [...]}},
 "body": [{"d": "YYYY-MM-DD", "kg": 82.5}], "lifts": [{"d", "t", "clean"}]}
"""
import time
from datetime import datetime, timedelta

import pytz

import database
from config import TIMEZONE

BOSS_IDS = ["b1", "b2", "b3", "b4", "b5"]
# Закрываются только пруфом через бота
PROOF_IDS = {"cbr", "gym", "fight", "eng", "b5", "vid2", "fine"}

QUEST_NAMES = {
    "cbr": "TheorieExamen",
    "gym": "Зал",
    "fight": "Единоборства",
    "eng": "Duolingo",
    "house": "Жильё",
}


def now() -> datetime:
    return datetime.now(pytz.timezone(TIMEZONE))


def today_key() -> str:
    return now().strftime("%Y-%m-%d")


def tomorrow_key() -> str:
    return (now() + timedelta(days=1)).strftime("%Y-%m-%d")


def hhmm() -> str:
    return now().strftime("%H:%M")


DEF_PROFILE = {"reward": "Трава", "name": "", "wake": "08:00", "bed": "23:30", "gymDays": [1, 2, 3, 4, 5, 6],
               "req": {"train": 1, "cbr": 1, "eng": 1}, "videoMin": 110}


def prof(day_or_state: dict | None) -> dict:
    """Анкета: из day["_profile"] или state["profile"], поверх значений по умолчанию."""
    p = dict(DEF_PROFILE)
    src = (day_or_state or {}).get("_profile") or (day_or_state or {}).get("profile") or {}
    p.update({k: v for k, v in src.items() if v is not None})
    return p


def bed_min(p: dict) -> int:
    m = to_min(p["bed"])
    return m + 1440 if m < 300 else m


def day_end(p: dict) -> str:
    return hm(min(bed_min(p) - 45, 1439))


def training_id(weekday: int | None = None, profile: dict | None = None) -> str | None:
    """Зал в дни из анкеты (по умолчанию пн–сб). weekday как в Python: пн=0."""
    w = now().weekday() if weekday is None else weekday
    js = (w + 1) % 7
    return "gym" if js in (profile or DEF_PROFILE)["gymDays"] else None


def _day(state: dict, key: str) -> dict:
    day = state["days"].setdefault(key, {})
    day.setdefault("done", [])
    day.setdefault("at", {})
    day.setdefault("custom", [])
    return day


def boss_done(day: dict | None) -> bool:
    return bool(day) and all(b in day.get("done", []) for b in BOSS_IDS)


REQ_NAMES = {"video": "Видео", "train": "Тренировка", "cbr": "Теория", "eng": "Duolingo", "debt": "Долг: 2-е видео", "fine": "Штраф €5"}


# --- Провал и наказания: провал = день без видео. Долг не копится: максимум одно лишнее видео и €5.

def failed_day(state: dict, key: str) -> bool:
    if key >= today_key():
        return False
    if boss_done(state.get("days", {}).get(key)):
        return False
    return any(k <= key and (d.get("done") or d.get("firstSmoke")) for k, d in state.get("days", {}).items())


def has_debt(state: dict, key: str | None = None) -> bool:
    key = key or today_key()
    prev = (datetime.strptime(key, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return failed_day(state, prev)


def reqs(day: dict, weekday: int | None = None) -> list[dict]:
    """4 главных дела для награды дня. weekday как в Python: пн=0, вс=6."""
    at = day.get("at", {})
    skip = set(day.get("skip", []))
    p = prof(day)
    tr = training_id(weekday, p)
    r = [{"id": "video", "t": at.get("b5")}]
    if tr and p["req"].get("train"):
        r.append({"id": "train", "tr": tr, "t": at.get(tr) or ("skip" if tr in skip else None)})
    if p["req"].get("cbr"):
        r.append({"id": "cbr", "t": at.get("cbr") or ("skip" if "cbr" in skip else None)})
    if p["req"].get("eng"):
        r.append({"id": "eng", "t": at.get("eng") or ("skip" if "eng" in skip else None)})
    if day.get("_debt"):
        r.append({"id": "debt", "t": at.get("vid2")})
        r.append({"id": "fine", "t": at.get("fine")})
    return r


def reward_state(day: dict | None, weekday: int | None = None) -> str:
    """pending: копим · open: награда открыта · gold: взял после всех дел ·
    base: раньше, но тренировка была чистой · broken: раньше тренировки."""
    if not day:
        return "pending"
    r = reqs(day, weekday)
    fs = day.get("firstSmoke")
    done_all = all(x["t"] for x in r)
    if not fs:
        return "open" if done_all else "pending"
    if done_all and all(x["t"] == "skip" or x["t"] <= fs for x in r):
        return "gold"
    tr = next((x for x in r if x["id"] == "train"), None)
    first = tr if tr else r[0]
    if first["t"] and first["t"] != "skip" and first["t"] <= fs:
        return "base"
    return "broken"


def reward_line(day: dict, weekday: int | None = None) -> str:
    r = reqs(day, weekday)
    n = sum(1 for x in r if x["t"])
    st = reward_state(day, weekday)
    marks = " · ".join(("✅ " if x["t"] else "▫️ ") + (("Бой" if x.get("tr") == "fight" else "Зал") if x["id"] == "train" else REQ_NAMES[x["id"]]) for x in r)
    head = {
        "pending": f"🌿 {'Штрафной день' if day.get('_debt') else 'Награда дня'}: {n} из {len(r)}",
        "open": "🌿 Награда открыта. Заслужил.",
        "gold": "🌿 Награда взята по-честному.",
        "base": "🌿 Раньше времени, но тренировка была чистой.",
        "broken": "🌿 Награда сегодня сгорела.",
    }[st]
    return head + "\n" + marks


# --- Замеры времени (та же логика, что в webapp/index.html) ---

def log_ev(day: dict, e: str, qid: str = "") -> None:
    day["ev"] = (day.get("ev") or []) + [[hhmm(), e, qid]]
    day["ev"] = day["ev"][-200:]


def _rest_overlap(day: dict, a: int, b: int) -> int:
    return sum(max(0, min(b, s + m) - max(a, s)) for s, m in rests_of(day))


def record_span(day: dict, qid: str) -> None:
    """Сколько заняло дело: от «начал» (или прошлого закрытого дела) до сейчас, минус отдых."""
    n = now()
    now_m = n.hour * 60 + n.minute
    s, g = None, 1
    cur = day.get("cur") or {}
    if cur.get("id") == qid or (qid in BOSS_IDS and cur.get("id") == "video"):
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
    base = {q[0]: q[2] for q in PLAN_QUESTS}
    vals: dict[str, list[int]] = {}
    for k in sorted(state.get("days", {}))[-40:]:
        sp = state["days"][k].get("span") or {}
        for qid, v in sp.items():
            if qid not in base or qid == "video":
                continue
            if v.get("g") and v.get("m", 0) > base[qid] * 2.5:
                continue
            vals.setdefault(qid, []).append(v["m"])
        if all(b in sp for b in BOSS_IDS):
            vals.setdefault("video", []).append(sum(sp[b]["m"] for b in BOSS_IDS))
        for b in BOSS_IDS:
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
    day["_profile"] = state.get("profile")
    day["_theory_i"] = (state.get("theory") or {}).get("i", 0)
    day["_learn"] = learned(state)
    return day


async def mark_done(telegram_id: int, ids: list[str]) -> dict:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, today_key())
    t = hhmm()
    for i in ids:
        if i not in day["done"]:
            record_span(day, i)
            day["done"].append(i)
            day["at"][i] = t
            log_ev(day, "done", i)
    cur = day.get("cur") or {}
    if cur.get("id") in ids or (cur.get("id") == "video" and boss_done(day)):
        day.pop("cur", None)
    await database.save_quest_state(telegram_id, state)
    day["_debt"] = has_debt(state)
    day["_profile"] = state.get("profile")
    return day


async def add_smoke(telegram_id: int) -> dict:
    state = await database.get_quest_state(telegram_id)
    day = _day(state, today_key())
    if not day.get("smokes"):
        day["firstSmoke"] = hhmm()
    day["smokes"] = day.get("smokes", 0) + 1
    await database.save_quest_state(telegram_id, state)
    day["_debt"] = has_debt(state)
    day["_profile"] = state.get("profile")
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
    # то, что ведёт только бот: приложение это не меняет, берём с сервера
    for k in ("notes", "temp", "theory", "settings", "home", "loc"):
        if server.get(k) is not None:
            incoming[k] = server[k]
    if server.get("reminders") or incoming.get("reminders"):
        byid = {r.get("id"): r for r in incoming.get("reminders") or []}
        for r in server.get("reminders") or []:
            if r.get("id") not in byid or r.get("sent"):
                byid[r.get("id")] = r
        incoming["reminders"] = list(byid.values())
    for k in ("prefs", "gifts", "claims", "goals"):
        if not incoming.get(k) and server.get(k):
            incoming[k] = server[k]
    if not incoming.get("profile") and server.get("profile"):
        incoming["profile"] = server["profile"]
    s_days = server.get("days", {})

    for key, sday in s_days.items():
        iday = incoming["days"].setdefault(key, {})
        iday.setdefault("done", [])
        iday.setdefault("at", {})
        iday.setdefault("custom", [])
        # пруфы, засчитанные ботом
        for q in sday.get("done", []):
            if q in PROOF_IDS and q not in iday["done"]:
                iday["done"].append(q)
                if q in sday.get("at", {}):
                    iday["at"][q] = sday["at"][q]
        # журнал и замеры, записанные ботом
        if sday.get("ev"):
            seen = {tuple(e) for e in iday.get("ev") or []}
            iday["ev"] = sorted((iday.get("ev") or []) + [e for e in sday["ev"] if tuple(e) not in seen], key=lambda e: e[0])[-200:]
        if sday.get("span"):
            iday["span"] = {**sday["span"], **(iday.get("span") or {})}
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
        iday["done"] = [q for q in iday.get("done", []) if q not in PROOF_IDS or q in sdone]
        iday["at"] = {q: t for q, t in iday.get("at", {}).items() if q in iday["done"]}
    return incoming


def day_summary(day: dict) -> str:
    done = day.get("done", [])
    w = now().weekday()
    lines = ["Видео: " + ("вышло ✅" if boss_done(day) else f"{sum(b in done for b in BOSS_IDS)} из 5 шагов ❌")]
    if "vid2" in done:
        lines.append("Второе видео: вышло 🔥" + (" Долг закрыт." if day.get("_debt") else ""))
    elif day.get("_debt"):
        lines.append("Долг за вчера: второе видео не вышло ❌")
    if day.get("_debt"):
        lines.append("Штраф €5: " + ("переведён ✅" if "fine" in done else "не переведён ❌"))
    tr = training_id(w, prof(day))
    if tr:
        lines.append("Тренировка: " + ("да ✅" if tr in done else "нет ❌"))
    missed = [QUEST_NAMES[q] for q in ("cbr", "eng") if q not in done]
    if missed:
        lines.append("Пропущено: " + ", ".join(missed))
    open_custom = [c["name"] for c in day.get("custom", []) if c["id"] not in done]
    if open_custom:
        lines.append("Не закрыто: " + "; ".join(open_custom))
    lines.append("")
    lines.append(reward_line(day, w))
    if day.get("smokes"):
        lines.append(f"Трава: {day['smokes']}, первый раз в {day.get('firstSmoke')}")
    return "\n".join(lines)


# --- Живой план (та же логика, что в webapp/index.html) ---

GAME_MIN = 45
UNTIL = {"wake": "12:00", "bfast": "12:00", "lunch": "16:00"}
DAY_START = "08:00"
DAY_END = "22:45"
# (id, название, минут, дни недели JS: 0 вс .. 6 сб или None, якорь, бытовое)
PLAN_QUESTS = [
    ("wake", "Подъём, вода, улица", 20, None, None, True),
    ("bfast", "Завтрак", 20, None, None, True),
    ("fine", "Штраф: €5 в safemoney", 5, None, None, False),
    ("cbr", "Теория на права", 45, None, None, False),
    ("video", "Видео", 110, None, None, False),
    ("lunch", "Обед", 30, None, None, True),
    ("gym", "Зал", 120, [1, 2, 3, 4, 5, 6], None, False),
    ("walk", "Прогулка", 45, [0], None, False),
    ("_custom", "", 0, None, None, False),
    ("vid2", "Бонус: второе видео", 110, None, None, False),
    ("prep", "Заготовка еды", 80, [0, 3, 5], None, True),
    ("dinner", "Ужин", 30, [1, 2, 4, 6], None, True),
    ("eng", "Duolingo", 20, None, None, False),
    ("house", "Жильё", 20, [3, 0], None, False),
    ("screen", "Экран выкл, повтор теории, душ", 45, None, DAY_END, True),
]


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

    def is_done(qid):
        return boss_done(day) if qid == "video" else qid in done

    def dur_of(qid, base):
        if qid in dur_over:
            return dur_over[qid]
        if qid in learn:
            base = learn[qid]["min"]
        if qid == "video":
            left = sum(1 for b in BOSS_IDS if b not in done)
            return max(20, round(base * left / 5))
        return base

    queue = []
    P = prof(day)
    gym_today = wd in P["gymDays"]
    for qid, name, dur, days, fixed, minor in PLAN_QUESTS:
        if qid == "gym":
            if not gym_today:
                continue
        elif qid == "walk":
            if gym_today:
                continue
        elif days is not None and wd not in days:
            continue
        if qid == "screen":
            fixed = day_end(P)
        if qid == "video":
            dur = P["videoMin"]
        if qid == "_custom":
            for c in day.get("custom", []):
                queue.append({"id": c["id"], "name": c["name"], "dur": c.get("dur", 30), "fixed": c.get("at"), "minor": False})
            continue
        if qid == "fine" and not day.get("_debt"):
            continue
        if qid == "vid2" and not boss_done(day) and not day.get("_debt"):
            continue
        if qid == "vid2" and day.get("_debt"):
            name = "Долг: второе видео"
        if qid in skip:
            continue
        if qid in UNTIL and now_m > to_min(UNTIL[qid]) and qid not in done:
            continue  # утро прошло: завтрак в вечер не тащим
        queue.append({"id": qid, "name": name, "dur": dur, "fixed": fixed, "minor": minor})

    items, over = [], []
    wake = day.get("at", {}).get("wake")
    cur = max(now_m, to_min(P["wake"])) if not wake else now_m
    for g, mins in sorted(rests_of(day)):
        if g + mins > now_m:
            s = g if g <= now_m else max(g, cur)
            items.append({"game": True, "start": s, "end": s + mins})
            cur = max(cur, s + mins)

    pending = [q for q in queue if not is_done(q["id"])]
    started = day.get("cur")
    if started:
        idx = next((i for i, q in enumerate(pending) if q["id"] == started.get("id")), -1)
        if idx >= 0:
            q = pending.pop(idx)
            s = to_min(started["at"])
            e = s + dur_of(q["id"], q["dur"])
            items.append({**q, "start": s, "end": max(e, now_m + 5), "started": True, "late": max(0, now_m - e)})
            cur = max(cur, e, now_m + 5)

    anchors = sorted([q for q in pending if q["fixed"]], key=lambda q: to_min(q["fixed"]))
    flex = [q for q in pending if not q["fixed"]]
    end = to_min(day_end(P))

    def place(a):
        nonlocal cur
        s = max(to_min(a["fixed"]), cur)
        items.append({**a, "start": s, "end": s + dur_of(a["id"], a["dur"]), "anchor": True, "late": max(0, cur - to_min(a["fixed"]))})
        cur = s + dur_of(a["id"], a["dur"])

    for q in flex:
        du = dur_of(q["id"], q["dur"])
        while anchors and to_min(anchors[0]["fixed"]) < cur + du and anchors[0]["fixed"] != day_end(P):
            place(anchors.pop(0))
        if cur + du > end:
            optional = q["id"] in ("vid2", "house") and not day.get("_debt")
            if not optional:  # бонусы молча не влезают, без тревоги
                over.append({**q, "dur": du})
            continue
        items.append({**q, "start": cur, "end": cur + du})
        cur += du
    while anchors:
        place(anchors.pop(0))
    return {"items": items, "over": over}
