"""Действия, которые помощник в чате делает сам: дела, отметки, вес, подходы, отдых, цифры канала.

Каждая функция меняет state на месте и возвращает короткий итог для модели.
"""
import os
import re
import time
from datetime import datetime, timedelta

from services import quest

BASE_NAMES = {q[0]: q[1] for q in quest.PLAN_QUESTS if q[0] != "_custom"}
BASE_NAMES.update({"b1": "Видео: текст", "b2": "Видео: озвучка", "b3": "Видео: картинка",
                   "b4": "Видео: субтитры", "b5": "Видео: загрузка"})
# Эти закрываются только пруфом (фото или ссылка), руками нельзя
PROOF_ONLY = quest.PROOF_IDS

TOOLS = [
    {"name": "add_task",
     "description": "Добавить своё дело в план. day: today, tomorrow или YYYY-MM-DD. at: время HH:MM, если привязано к часу. dur: сколько минут займёт (по умолчанию 30).",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "day": {"type": "string"}, "at": {"type": "string"}, "dur": {"type": "integer"}},
         "required": ["name"]}},
    {"name": "edit_task",
     "description": "Изменить дело по id из плана: новое название, время at (HH:MM, пустая строка = без времени), длительность dur в минутах, или перенести на другой день move_to (today/tomorrow/YYYY-MM-DD, только для своих дел). Для базовых дел (зал, теория и т.п.) можно менять только dur.",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string"}, "day": {"type": "string"}, "name": {"type": "string"}, "at": {"type": "string"},
         "dur": {"type": "integer"}, "move_to": {"type": "string"}}, "required": ["id"]}},
    {"name": "delete_task",
     "description": "Убрать дело по id. Своё дело удаляется совсем. Базовое дело убирается только на сегодня.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "day": {"type": "string"}}, "required": ["id"]}},
    {"name": "complete_task",
     "description": "Отметить дело сделанным по id (свои дела, подъём, еда, шаги видео b1–b4, прогулка, жильё). Теорию, зал, Duolingo, загрузку видео и штраф так закрыть нельзя: нужен пруф фото или ссылкой.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "uncomplete_task",
     "description": "Снять отметку «сделано» с дела по id (кроме закрытых пруфом).",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "start_task",
     "description": "Отметить, что Андрей начал дело сейчас (id; для видео используй video). Нужно для замеров времени и плана.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "start_rest",
     "description": "Начать отдых сейчас на min минут (30, 45, 60 или 90). Нельзя в штрафной день, пока долг не закрыт.",
     "input_schema": {"type": "object", "properties": {"min": {"type": "integer"}}, "required": ["min"]}},
    {"name": "end_rest", "description": "Закончить текущий отдых раньше.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "log_weight", "description": "Записать вес тела за сегодня, кг.",
     "input_schema": {"type": "object", "properties": {"kg": {"type": "number"}}, "required": ["kg"]}},
    {"name": "set_weight_goal", "description": "Поставить цель по весу, кг.",
     "input_schema": {"type": "object", "properties": {"kg": {"type": "number"}}, "required": ["kg"]}},
    {"name": "log_lift", "description": "Записать подход в зале: упражнение, вес в кг, повторы.",
     "input_schema": {"type": "object", "properties": {
         "ex": {"type": "string"}, "kg": {"type": "number"}, "reps": {"type": "integer"}}, "required": ["ex", "kg", "reps"]}},
    {"name": "set_channel", "description": "Обновить цифры канала: подписчики и/или просмотры лучшего видео.",
     "input_schema": {"type": "object", "properties": {"subs": {"type": "integer"}, "best_views": {"type": "integer"}}}},
    {"name": "take_reward", "description": "Отметить, что Андрей взял награду дня. Только если он прямо сказал, что взял.",
     "input_schema": {"type": "object", "properties": {}}},
]


def _new_id() -> str:
    return f"t{int(time.time() * 1000):x}{os.urandom(2).hex()}"


def _key(day: str | None) -> str:
    d = (day or "today").strip().lower()
    n = quest.now()
    if d in ("today", "сегодня", ""):
        return n.strftime("%Y-%m-%d")
    if d in ("tomorrow", "завтра"):
        return (n + timedelta(days=1)).strftime("%Y-%m-%d")
    if d in ("послезавтра",):
        return (n + timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return n.strftime("%Y-%m-%d")


def _at(v) -> str | None:
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", str(v or ""))
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def _find_custom(state: dict, qid: str, day: str | None):
    keys = [_key(day)] if day else sorted(state.get("days", {}), reverse=True)[:14]
    for k in keys:
        d = state.get("days", {}).get(k) or {}
        for c in d.get("custom", []):
            if c.get("id") == qid:
                return k, d, c
    return None, None, None


def _name(state: dict, qid: str) -> str:
    _, _, c = _find_custom(state, qid, None)
    return c["name"] if c else BASE_NAMES.get(qid, qid)


def run(state: dict, name: str, a: dict) -> str | None:
    """None = это не наш инструмент."""
    today = quest.today_key()
    td = quest._day(state, today)

    if name == "add_task":
        nm = str(a.get("name") or "").strip()[:80]
        if not nm:
            return "пустое название"
        k = _key(a.get("day"))
        d = quest._day(state, k)
        item = {"id": _new_id(), "name": nm, "dur": max(5, min(int(a.get("dur") or 30), 480))}
        if _at(a.get("at")):
            item["at"] = _at(a.get("at"))
        d["custom"].append(item)
        if k == today:
            quest.log_ev(td, "add", item["id"])
        return f"добавлено на {k}: {nm}" + (f" в {item['at']}" if item.get("at") else "") + f", {item['dur']} мин, id {item['id']}"

    if name == "edit_task":
        qid = str(a.get("id") or "")
        k, d, c = _find_custom(state, qid, a.get("day"))
        if c:
            if a.get("name"):
                c["name"] = str(a["name"])[:80]
            if "at" in a:
                t = _at(a.get("at"))
                if t:
                    c["at"] = t
                else:
                    c.pop("at", None)
            if a.get("dur"):
                c["dur"] = max(5, min(int(a["dur"]), 480))
            if a.get("move_to"):
                nk = _key(a["move_to"])
                if nk != k:
                    d["custom"] = [x for x in d["custom"] if x is not c]
                    d["del"] = sorted(set(d.get("del", [])) | {qid})
                    nc = dict(c, id=_new_id())
                    quest._day(state, nk)["custom"].append(nc)
                    return f"перенесено на {nk}: {nc['name']}, новый id {nc['id']}"
            return f"изменено: {c['name']}" + (f" в {c['at']}" if c.get("at") else "") + f", {c.get('dur', 30)} мин"
        if qid in BASE_NAMES and a.get("dur"):
            td.setdefault("dur", {})[qid] = max(5, min(int(a["dur"]), 480))
            return f"{BASE_NAMES[qid]}: сегодня {td['dur'][qid]} мин"
        return "не нашёл такое дело или это поле у базового дела не меняется"

    if name == "delete_task":
        qid = str(a.get("id") or "")
        k, d, c = _find_custom(state, qid, a.get("day"))
        if c:
            d["custom"] = [x for x in d["custom"] if x is not c]
            d["done"] = [x for x in d.get("done", []) if x != qid]
            d["del"] = sorted(set(d.get("del", [])) | {qid})
            return f"удалено: {c['name']}"
        if qid in BASE_NAMES:
            td.setdefault("skip", [])
            if qid not in td["skip"]:
                td["skip"].append(qid)
            quest.log_ev(td, "skip", qid)
            return f"убрано на сегодня: {BASE_NAMES[qid]}"
        return "не нашёл такое дело"

    if name == "complete_task":
        qid = str(a.get("id") or "")
        if qid == "video":
            qid = next((b for b in quest.BOSS_IDS if b not in td["done"]), "b5")
        if qid in PROOF_ONLY:
            return f"{_name(state, qid)} закрывается только пруфом: фото или ссылкой в этот чат"
        if qid not in BASE_NAMES and not _find_custom(state, qid, "today")[2]:
            return "не нашёл такое дело на сегодня"
        if qid in td["done"]:
            return "уже отмечено"
        quest.record_span(td, qid)
        td["done"].append(qid)
        td["at"][qid] = quest.hhmm()
        quest.log_ev(td, "done", qid)
        cur = td.get("cur") or {}
        if cur.get("id") == qid or (cur.get("id") == "video" and quest.boss_done(td)):
            td.pop("cur", None)
        sp = (td.get("span") or {}).get(qid)
        return f"отмечено: {_name(state, qid)}" + (f", заняло {sp['m']} мин" if sp else "")

    if name == "uncomplete_task":
        qid = str(a.get("id") or "")
        if qid in PROOF_ONLY:
            return "это закрыто пруфом, снять нельзя"
        if qid not in td["done"]:
            return "и так не отмечено"
        td["done"] = [x for x in td["done"] if x != qid]
        td["at"].pop(qid, None)
        (td.get("span") or {}).pop(qid, None)
        quest.log_ev(td, "undo", qid)
        return f"снято: {_name(state, qid)}"

    if name == "start_task":
        qid = str(a.get("id") or "")
        if qid in quest.BOSS_IDS:
            qid = "video"
        td["cur"] = {"id": qid, "at": quest.hhmm()}
        quest.log_ev(td, "start", qid)
        return f"начато в {td['cur']['at']}: {_name(state, qid)}"

    if name == "start_rest":
        if quest.has_debt(state) and not ("vid2" in td["done"] and "fine" in td["done"]):
            return "штрафной день: отдых только после второго видео и €5"
        m = int(a.get("min") or 45)
        m = min((30, 45, 60, 90), key=lambda x: abs(x - m))
        td.setdefault("gl", []).append({"at": quest.hhmm(), "min": m})
        td["games"] = max(td.get("games", 0), len(td["gl"]))
        quest.log_ev(td, "rest", str(m))
        total = sum(mm for _, mm in quest.rests_of(td))
        return f"отдых {m} мин с {quest.hhmm()}, всего отдыха сегодня {total} мин"

    if name == "end_rest":
        n = quest.now()
        now_m = n.hour * 60 + n.minute
        gl = td.get("gl", [])
        for i, (s, m) in enumerate(quest.rests_of(td)):
            if s <= now_m < s + m:
                gl[i] = {"at": quest.hm(s), "min": max(1, now_m - s)}
                quest.log_ev(td, "rest_end", "")
                return "отдых закончен"
        return "сейчас нет отдыха"

    if name == "log_weight":
        kg = float(a.get("kg") or 0)
        if not 30 < kg < 250:
            return "странный вес, уточни"
        state["body"] = [b for b in state.get("body", []) if b.get("d") != today] + [{"d": today, "kg": round(kg, 1)}]
        state["body"].sort(key=lambda b: b["d"])
        first = state["body"][0]["kg"]
        return f"записал {round(kg, 1)} кг, с первого взвешивания {round(kg - first, 1):+} кг"

    if name == "set_weight_goal":
        kg = float(a.get("kg") or 0)
        if not 30 < kg < 250:
            return "странная цель, уточни"
        g = dict(state.get("goals") or {})
        if g.get("goalKg") != kg:
            body = state.get("body") or []
            g["startKg"] = body[-1]["kg"] if body else None
        g["goalKg"] = round(kg, 1)
        state["goals"] = g
        return f"цель по весу {g['goalKg']} кг"

    if name == "log_lift":
        ex = str(a.get("ex") or "").strip()[:40]
        kg, reps = float(a.get("kg") or 0), int(a.get("reps") or 0)
        if not ex or kg < 0 or reps <= 0:
            return "не хватает данных: упражнение, вес, повторы"
        prev = [x for x in state.get("lifts", []) if x.get("ex", "").lower() == ex.lower()]
        best = max((x["kg"] * (1 + x.get("reps", 1) / 30) for x in prev), default=0)
        state.setdefault("lifts", []).append({"d": today, "ex": ex, "kg": round(kg, 1), "reps": reps, "clean": not td.get("firstSmoke")})
        return f"записал {ex} {kg} × {reps}" + (", это рекорд" if prev and kg * (1 + reps / 30) > best else "")

    if name == "set_channel":
        g = dict(state.get("goals") or {})
        if a.get("subs") is not None:
            g["subs"] = int(a["subs"])
        if a.get("best_views") is not None:
            g["maxViews"] = max(int(a["best_views"]), g.get("maxViews", 0))
        state["goals"] = g
        log = [x for x in state.get("chanLog") or [] if x.get("d") != today]
        state["chanLog"] = (log + [{"d": today, "subs": g.get("subs", 0), "views": g.get("maxViews", 0)}])[-60:]
        return f"канал: {g.get('subs', 0)} подписчиков, лучшее видео {g.get('maxViews', 0)}"

    if name == "take_reward":
        if not td.get("smokes"):
            td["firstSmoke"] = quest.hhmm()
        td["smokes"] = td.get("smokes", 0) + 1
        quest.log_ev(td, "reward", "")
        day = dict(td, _debt=quest.has_debt(state), _profile=state.get("profile"))
        return "записал. " + quest.reward_line(day).replace("\n", " ")

    return None


def plan_context(state: dict) -> str:
    """План на сегодня с id и свои дела на завтра: чтобы модель знала, чем управлять."""
    today = quest.today_key()
    day = dict(quest._day(state, today))
    day["_debt"] = quest.has_debt(state)
    day["_profile"] = state.get("profile")
    day["_learn"] = quest.learned(state)
    p = quest.build_plan(day)
    lines = ["ПЛАН НА СЕГОДНЯ (id: дело, время):"]
    for it in p["items"]:
        if it.get("game"):
            lines.append(f"- отдых {quest.hm(it['start'])}–{quest.hm(it['end'])}")
        elif not it.get("gap"):
            lines.append(f"- {it['id']}: {it['name']} {quest.hm(it['start'])}–{quest.hm(it['end'])}" + (" (идёт сейчас)" if it.get("started") else ""))
    for o in p["over"]:
        lines.append(f"- {o['id']}: {o['name']} (не влезает до отбоя)")
    done = day.get("done", [])
    if done:
        lines.append("Сделано: " + ", ".join(f"{_name(state, x)} в {day['at'].get(x, '?')}" for x in done))
    tk = quest.tomorrow_key()
    tc = (state.get("days", {}).get(tk) or {}).get("custom", [])
    if tc:
        lines.append("СВОИ ДЕЛА НА ЗАВТРА: " + "; ".join(f"{c['id']}: {c['name']}" + (f" в {c['at']}" if c.get("at") else "") for c in tc))
    L = day["_learn"]
    if L:
        lines.append("ТВОЁ РЕАЛЬНОЕ ВРЕМЯ (медиана): " + ", ".join(f"{BASE_NAMES.get(k, k) if k != 'video' else 'видео целиком'} {v['min']} мин" for k, v in L.items()))
    ev = day.get("ev") or []
    if ev:
        lines.append("ЖУРНАЛ СЕГОДНЯ: " + "; ".join(f"{t} {e} {_name(state, i) if i and not i.isdigit() else i}".strip() for t, e, i in ev[-15:]))
    return "\n".join(lines)
