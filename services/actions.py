"""Действия, которые помощник в чате делает сам: дела, отметки, вес, подходы, отдых, цифры канала.

Каждая функция меняет state на месте и возвращает короткий итог для модели.
"""
import os
import re
import time
from datetime import datetime, timedelta

from services import quest


TOOLS = [
    {"name": "routine_edit",
     "description": "Изменить РАСПОРЯДОК: из чего каждый день собирается план (насовсем, не только сегодня). op: add (новое дело), update (поменять поля дела по id или имени), remove (убрать насовсем). days: дни недели 0 вс .. 6 сб (пусто = каждый день). main=1: главное дело для награды дня. proof=1: закрывается только пруфом (тогда check: что должно быть на фото). at: HH:MM якорь или end = перед отбоем. minor=1: бытовое (еда, душ).",
     "input_schema": {"type": "object", "properties": {
         "op": {"type": "string", "enum": ["add", "update", "remove"]}, "id": {"type": "string"}, "name": {"type": "string"},
         "dur": {"type": "integer"}, "days": {"type": "array", "items": {"type": "integer"}}, "at": {"type": "string"},
         "main": {"type": "integer"}, "proof": {"type": "integer"}, "minor": {"type": "integer"}, "sub": {"type": "string"},
         "check": {"type": "string"}, "hint": {"type": "string"}}, "required": ["op"]}},
    {"name": "set_goal",
     "description": "Текущая цель (одна, кроме здоровья): name (например Канал, Рисование), unit (что считаем: подписчиков, картин, €), habit = id дела из распорядка, которое ведёт к цели (его серия дней). value = текущая цифра. Если цель та же, меняй только переданные поля. clear=true убирает цель.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "unit": {"type": "string"}, "habit": {"type": "string"},
         "value": {"type": "number"}, "clear": {"type": "boolean"}}}},
    {"name": "goal_value",
     "description": "Записать число по цели из списка ЦЕЛИ (накоплено €, баллы теста, страниц и т.п.). goal = название или id цели. add=true если человек говорит, сколько ДОБАВИЛ («отложил 50»), иначе value = текущее значение. Вес: log_weight, подписчики: set_channel.",
     "input_schema": {"type": "object", "properties": {"goal": {"type": "string"}, "value": {"type": "number"}, "add": {"type": "boolean"}}, "required": ["goal", "value"]}},
    {"name": "brain",
     "description": "МОЗГ (сохранённое им). op: save (сохранить заметку или идею, которую он надиктовал: title, sum, folder ideas|apply|recipes|want|docs, ctx = когда пригодится), "
                    "task (сделать запись делом: id, day today|tomorrow|YYYY-MM-DD), done (применил, больше не предлагать: id), move (переложить: id, folder), delete (id), later (не сейчас: id).",
     "input_schema": {"type": "object", "properties": {"op": {"type": "string", "enum": ["save", "task", "done", "move", "delete", "later"]}, "id": {"type": "string"},
         "title": {"type": "string"}, "sum": {"type": "string"}, "folder": {"type": "string"}, "ctx": {"type": "string"}, "day": {"type": "string"}}, "required": ["op"]}},
    {"name": "money",
     "description": "ДЕНЬГИ. op: add (трата kind=exp или доход kind=inc: amt, cat, note коротко что, acc счёт если назван, day если не сегодня), "
                    "balance (сколько сейчас: acc = cash наличные | card карта | bank банк | stocks акции | crypto крипта, amt), limit (лимит на месяц: cat, amt; 0 = убрать), "
                    "transfer (перевод между своими счетами: acc откуда, name куда, amt; «перевёл 200 с карты на акции»), regular (регулярный платёж: name, amt, day число месяца, cat), paid (регулярный оплачен: name), delete (удалить операцию: id), debt (долг: name кто, amt, dir owe_me = мне должны / i_owe = я должен, note, day = вернуть до), debt_close (долг вернули: name). "
                    "Категории трат: food еда/продукты/кафе, home быт/хозтовары, rent жильё/аренда/коммуналка, car машина/бензин/ремонт, health здоровье/аптека/спорт, "
                    "subs связь/подписки, fun развлечения/игры, cloth одежда, gift подарок себе, fine штраф, other. Доходы: ww, work, sale, other.",
     "input_schema": {"type": "object", "properties": {"op": {"type": "string", "enum": ["add", "balance", "transfer", "limit", "regular", "paid", "delete", "debt", "debt_close"]},
         "dir": {"type": "string", "enum": ["owe_me", "i_owe"]},
         "kind": {"type": "string", "enum": ["exp", "inc"]}, "amt": {"type": "number"}, "cat": {"type": "string"}, "note": {"type": "string"},
         "acc": {"type": "string"}, "name": {"type": "string"}, "day": {"type": "string"}, "id": {"type": "string"}}, "required": ["op"]}},
    {"name": "set_penalty",
     "description": "Штраф за пропуск: task = id главного дела, без которого день считается проваленным (завтра долг: повторить его и перевести fine евро в to). task пустой = штраф выключен.",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}, "fine": {"type": "integer"}, "to": {"type": "string"}}}},
    {"name": "add_task",
     "description": "Добавить своё дело в план. day: today, tomorrow или YYYY-MM-DD. at: время HH:MM, если привязано к часу. dur: сколько минут займёт (по умолчанию 30).",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "day": {"type": "string"}, "at": {"type": "string"}, "dur": {"type": "integer"}},
         "required": ["name"]}},
    {"name": "edit_task",
     "description": "Изменить дело по id из плана: новое название, время at (HH:MM, пустая строка = без времени), длительность dur в минутах, или перенести на другой день move_to (today/tomorrow/YYYY-MM-DD, только для своих дел). У дел из распорядка тут меняется только dur на сегодня; насовсем: routine_edit.",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string"}, "day": {"type": "string"}, "name": {"type": "string"}, "at": {"type": "string"},
         "dur": {"type": "integer"}, "move_to": {"type": "string"}}, "required": ["id"]}},
    {"name": "delete_task",
     "description": "Убрать дело по id. Своё дело удаляется совсем. Дело из распорядка убирается только на сегодня (насовсем: routine_edit remove).",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "day": {"type": "string"}}, "required": ["id"]}},
    {"name": "complete_task",
     "description": "Отметить дело сделанным по id из плана (свои дела, дела распорядка, шаги многошаговых дел). Дела с пометкой «пруф» в РАСПОРЯДКЕ так закрыть нельзя: нужен пруф фото или ссылкой.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "uncomplete_task",
     "description": "Снять отметку «сделано» с дела по id (кроме закрытых пруфом).",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "start_task",
     "description": "Отметить, что он начал дело сейчас (id из плана; для многошагового дела id самого дела). Нужно для замеров времени и плана.",
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
    {"name": "set_reminder",
     "description": "Напоминание сообщением в Telegram в нужное время. day: today/tomorrow/YYYY-MM-DD, at: HH:MM. Ставь, когда он просит напомнить, и к важным делам с конкретным временем.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "at": {"type": "string"}, "day": {"type": "string"}}, "required": ["text", "at"]}},
    {"name": "delete_reminder", "description": "Удалить напоминание по id.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "weekly_review",
     "description": "Сделать разбор прямо сейчас: перечитать журнал дел и переписку, обновить выводы о нём (ЧТО ТЫ ПОНЯЛ О НЁМ). Вернёт сообщение для него, перескажи своими словами. Сам по себе разбор идёт по воскресеньям в 20:30; вызывай, когда он просит «разбери мою неделю», «что ты про меня понял».",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_history",
     "description": "Журнал дел за последние days дней (по умолчанию 7): что и во сколько начал, сделал, убрал, отдых, награда, сколько заняло. Для вопросов «что я делал во вторник», «сколько у меня уходит на видео», разборов недели и советов по распорядку.",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "web_lookup",
     "description": "Поиск в интернете: часы работы магазина, адрес, цена, расписание, любой свежий факт. Используй, когда для решения нужен факт из мира (например, до скольки работает Kruidvat в Дронтене).",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "travel_time",
     "description": "Сколько ехать или идти до места по реальному маршруту (OpenStreetMap). to: место или адрес («Kruidvat Dronten», «Albert Heijn De Rede»). from: пусто = откуда он сейчас (свежая геолокация, иначе дом), home = от дома, или адрес. mode: car (по умолчанию, он ездит на машине), bike, foot. Используй всегда, когда планируешь поездку, вместо догадок.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "from": {"type": "string"}, "mode": {"type": "string", "enum": ["car", "bike", "foot"]}}, "required": ["to"]}},
    {"name": "set_home",
     "description": "Запомнить домашний адрес для расчёта дороги. Вызывай, когда он называет, где живёт, или переезжает. Если он говорит «я дома» и есть свежая геолокация, передай address=here.",
     "input_schema": {"type": "object", "properties": {"address": {"type": "string"}}, "required": ["address"]}},
    {"name": "house_add",
     "description": "Купил или положил продукты/вещи домой. items: [{name, cat, days: сколько дней годно, если знаешь точнее, amt и unit: количество, если известно (1 кг курицы = amt 1, unit кг)}]. cat: meat мясо и рыба, dairy молочное и яйца, veg овощи и фрукты, grain крупы и макароны, canned консервы и соусы, bread хлеб и сладкое, drinks напитки, spice специи и масло, other другое, home хозтовары. Можно не указывать: определится по названию. Заготовки готовой еды: prep_add.",
     "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
         "name": {"type": "string"}, "cat": {"type": "string", "enum": ["prep", "meat", "dairy", "veg", "grain", "canned", "bread", "drinks", "spice", "other", "home"]}, "days": {"type": "integer"}, "qty": {"type": "string"},
         "amt": {"type": "number"}, "unit": {"type": "string", "enum": ["г", "кг", "мл", "л", "шт"]}}, "required": ["name"]}}},
         "required": ["items"]}},
    {"name": "food_ate",
     "description": "Съел или потратил продукты: списать из Дома. items: [{name как в Доме, amt, unit г|кг|мл|л|шт}] или all=true (доел всё). "
                    "«Съел 2 яйца и 150 г курицы» = [{яйца 2 шт},{курица 150 г}]. Если он не назвал количество, оцени обычную порцию (яйцо 1 шт, курица 150 г, рис 80 г сухого, молоко 200 мл). Заготовку списывает prep_eat.",
     "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
         "name": {"type": "string"}, "amt": {"type": "number"}, "unit": {"type": "string"}, "all": {"type": "boolean"}}, "required": ["name"]}}}, "required": ["items"]}},
    {"name": "food_set", "description": "Сколько продукта осталось на самом деле (сверка): name, amt, unit.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "amt": {"type": "number"}, "unit": {"type": "string"}}, "required": ["name", "amt", "unit"]}},
    {"name": "house_level",
     "description": "Изменить уровень запаса: ok = есть, low = мало, out = кончилось. «Молоко кончилось» = out, «риса мало» = low.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "level": {"type": "string", "enum": ["ok", "low", "out"]}}, "required": ["name", "level"]}},
    {"name": "house_remove", "description": "Убрать позицию из дома совсем (съел, выбросил, не нужна).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "shop_add", "description": "Добавить в список покупок то, чего нет в учёте дома.",
     "input_schema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}}, "required": ["names"]}},
    {"name": "chore_set",
     "description": "Добавить или изменить домашнее дело с повтором: name, every (раз в сколько дней), dur (минут). Или remove=true, чтобы удалить.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "every": {"type": "integer"}, "dur": {"type": "integer"}, "remove": {"type": "boolean"}}, "required": ["name"]}},
    {"name": "prep_add",
     "description": "Приготовил заготовку: блюдо и сколько порций (days: сколько дней годна, по умолчанию 4). «Сделал курицу с рисом на 4 порции».",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "portions": {"type": "integer"}, "days": {"type": "integer"}}, "required": ["name", "portions"]}},
    {"name": "prep_eat",
     "description": "Съел порцию заготовки (n порций, по умолчанию 1). name пустое = та, что раньше испортится.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "n": {"type": "integer"}}}},
    {"name": "move_task",
     "description": "Перенести дело из плана на другой день: id из плана, to: tomorrow / YYYY-MM-DD. Своё дело переезжает целиком, дело распорядка на сегодня убирается и добавляется в тот день, если его там нет.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "to": {"type": "string"}}, "required": ["id", "to"]}},
    {"name": "project_new",
     "description": "Завести проект: разовое большое дело из нескольких шагов (переезд, оформление WW, ремонт машины, поездка). due: срок YYYY-MM-DD, если есть. Шаги добавляй project_step add=true.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "due": {"type": "string"}}, "required": ["name"]}},
    {"name": "project_step",
     "description": "Отметить шаг проекта (например переезда) сделанным или снова открытым, или добавить новый шаг (add=true). Взять шаг в план: house_take с текстом шага.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "done": {"type": "boolean"}, "add": {"type": "boolean"}, "project": {"type": "string"}}, "required": ["text"]}},
    {"name": "house_take",
     "description": "Взять на себя дело из Быта или покупку (тогда оно попадает в его план), или отдать обратно (give=true). what: название дела/покупки или «покупки» = все покупки из списка.",
     "input_schema": {"type": "object", "properties": {"what": {"type": "string"}, "give": {"type": "boolean"}}, "required": ["what"]}},
    {"name": "house_note",
     "description": "Память ДОМА, общая для всех, кто там живёт: правила и факты о жилье (мусор вывозят по четвергам, стиралка в подвале, код домофона нет, хозяин такой-то). Личное об Андрее сюда не пиши, это remember. remove=true удаляет запись по тексту.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "remove": {"type": "boolean"}}, "required": ["text"]}},
    {"name": "goal_flag",
     "description": "Отметить разовый результат: earned = первые деньги с YouTube (открывает подарок в цели Канал). Только если он прямо сказал, что это произошло.",
     "input_schema": {"type": "object", "properties": {"flag": {"type": "string", "enum": ["earned"]}}, "required": ["flag"]}},
    {"name": "take_reward", "description": "Отметить, что он взял награду дня. Только если он прямо сказал, что взял.",
     "input_schema": {"type": "object", "properties": {}}},
]


def _new_id() -> str:
    return f"t{int(time.time() * 1000):x}{os.urandom(2).hex()}"


def _key(day: str | None) -> str:
    d = (day or "today").strip().lower()
    n = quest.lnow()
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


def _names(state: dict) -> dict:
    return quest.names(quest.full_profile(state))


def _name(state: dict, qid: str) -> str:
    if qid.startswith("pr_"):
        st = next((x for p in (state.get("house") or {}).get("projects", []) for x in p.get("steps", []) if f"pr_{p['id']}_{x['id']}" == qid), None)
        return st["t"] if st else qid
    if qid.startswith("ch_"):
        c = next((x for x in (state.get("house") or {}).get("chores", []) if x.get("id") == qid[3:]), None)
        return c["name"] if c else qid
    _, _, c = _find_custom(state, qid, None)
    return c["name"] if c else _names(state).get(qid, qid)


def run(state: dict, name: str, a: dict) -> str | None:
    """None = это не наш инструмент."""
    today = quest.today_key()
    td = quest._day(state, today)
    P = quest.full_profile(state)
    BASE_NAMES = quest.names(P)
    PROOF_ONLY = quest.proof_ids(P)

    if name == "routine_edit":
        return quest.routine_edit(state, str(a.get("op") or "update"), a)

    if name == "set_goal":
        g = None if a.get("clear") else dict(P.get("goal") or {"kind": "num", "name": "Цель", "unit": ""})
        if g is not None:
            if a.get("name") and str(a["name"]).strip().lower() != g.get("name", "").lower():
                g = {"kind": "num", "name": str(a["name"])[:40], "unit": g.get("unit", "")}  # новая цель
                state["tgoals"] = {k: v for k, v in (state.get("tgoals") or {}).items() if k != "goal"}
            if a.get("unit"):
                g["unit"] = str(a["unit"])[:30]
            if a.get("habit"):
                if not quest.item(P, str(a["habit"])):
                    return "нет такого дела в распорядке для привычки"
                g["habit"] = str(a["habit"])
            if a.get("value") is not None:
                G = dict(state.get("goals") or {})
                G["subs" if g.get("kind") == "chan" else "val"] = a["value"]
                if g.get("kind") != "chan":  # история цифры для графика
                    G["valLog"] = [x for x in G.get("valLog") or [] if x.get("d") != today][-59:] + [{"d": today, "v": a["value"]}]
                state["goals"] = G
        state["profile"] = dict(state.get("profile") or {}, routine=P["routine"], penalty=P.get("penalty"), goal=g, u=int(time.time() * 1000))
        return f"цель: {g['name']}" + (f", считаем {g['unit']}" if g.get("unit") else "") + (f", привычка {g['habit']}" if g.get("habit") else "") if g else "цель убрана"

    if name == "goal_value":
        q = str(a.get("goal") or "").strip().lower()
        gs = [g for g in (P.get("goals") or []) if g.get("status") != "done" and g.get("metric")]
        g = next((g for g in gs if g.get("id") == q or g.get("name", "").lower() == q), None) or next((g for g in gs if q and (q in g.get("name", "").lower() or g.get("name", "").lower() in q)), None)
        if not g:
            return "нет такой цели с числом. Цели: " + (", ".join(x.get("name", "") for x in gs) or "нет")
        m = g["metric"]
        if m.get("src") == "body":
            return run(state, "log_weight", {"kg": a.get("value")})
        if m.get("src") == "chan":
            return run(state, "set_channel", {"subs": a.get("value")})
        v = float(a.get("value") or 0)
        L = list((state.get("metrics") or {}).get(g["id"]) or [])
        cur = L[-1]["v"] if L else (m.get("start") or 0)
        add = bool(a["add"]) if a.get("add") is not None else m.get("mode") == "add"
        nv = cur + v if add else v
        L = [x for x in L if add or x.get("d") != today] + [dict({"d": today, "v": nv}, **({"add": v} if add else {}))]
        state["metrics"] = dict(state.get("metrics") or {}, **{g["id"]: L[-300:]})
        t = m.get("target")
        return f"{g['name']}: {nv:g} {m.get('unit') or ''}" + (f" из {t:g}" if t else "")

    if name == "set_penalty":
        t = str(a.get("task") or "")
        if t and not quest.item(P, t):
            return "нет такого дела в распорядке"
        pen = {"task": t, "fine": max(0, int(a.get("fine") or 0)), "to": str(a.get("to") or "копилку")[:40]} if t else None
        state["profile"] = dict(state.get("profile") or {}, routine=P["routine"], goal=P.get("goal"), penalty=pen, u=int(time.time() * 1000))
        return f"штраф: без «{quest.item(P, t)['name']}» завтра долг и €{pen['fine']}" if pen else "штраф выключен"

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
        it = quest.item(P, qid)
        if it and it.get("steps"):
            qid = next((b for b in quest.step_ids(it) if b not in td["done"]), quest.step_ids(it)[-1])
        if qid in PROOF_ONLY:
            return f"{_name(state, qid)} закрывается только пруфом: фото или ссылкой в этот чат"
        if qid == "buy":
            from services import house
            h = house.get(state)
            got = house.my_buys(h, h.get("owner"))
            for kind, x in got:
                house.mark_bought(h, kind, x)
        elif qid.startswith("ch_"):
            from services import house
            c = house.chore_done(house.get(state), qid[3:])
            if not c:
                return "не нашёл такое домашнее дело"
        elif qid.startswith("pr_"):
            from services import house
            h = house.get(state)
            st = next((x for p in h.get("projects", []) for x in p.get("steps", []) if f"pr_{p['id']}_{x['id']}" == qid), None)
            if not st:
                return "не нашёл такой шаг проекта"
            st.update({"done": True, "u": house.now_ms()})
            st.pop("who", None)
        elif qid not in BASE_NAMES and not _find_custom(state, qid, "today")[2]:
            return "не нашёл такое дело на сегодня"
        if qid in td["done"]:
            return "уже отмечено"
        quest.record_span(td, qid, P)
        td["done"].append(qid)
        td["at"][qid] = quest.hhmm()
        quest.log_ev(td, "done", qid)
        quest.drop_cur(td, P, [qid])
        sp = (td.get("span") or {}).get(qid)
        return f"отмечено: {_name(state, qid)}" + (f", заняло {sp['m']} мин" if sp and not sp.get("g") else "")

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
        par = quest.parent_of(P, qid)
        if par:
            qid = par["id"]
        td["cur"] = {"id": qid, "at": quest.hhmm()}
        quest.log_ev(td, "start", qid)
        return f"начато в {td['cur']['at']}: {_name(state, qid)}"

    if name == "start_rest":
        if quest.has_debt(state) and not ("vid2" in td["done"] and "fine" in td["done"]):
            return "штрафной день: отдых только после долга и штрафа"
        m = int(a.get("min") or 45)
        m = min((30, 45, 60, 90), key=lambda x: abs(x - m))
        td.setdefault("gl", []).append({"at": quest.hhmm(), "min": m})
        td["games"] = max(td.get("games", 0), len(td["gl"]))
        quest.log_ev(td, "rest", str(m))
        total = sum(mm for _, mm in quest.rests_of(td))
        return f"отдых {m} мин с {quest.hhmm()}, всего отдыха сегодня {total} мин"

    if name == "end_rest":
        now_m = quest.now_min()
        gl = td.get("gl", [])
        for i, (s, m) in enumerate(quest.rests_of(td)):
            if s <= now_m < s + m:
                gl[i] = {"at": quest.hm(s), "min": max(1, now_m - s)}
                quest.log_ev(td, "rest_end", "")
                return "отдых закончен"
        return "сейчас нет отдыха"

    if name == "money":
        from services import money
        m = money.get(state)
        op = a.get("op")
        amt = a.get("amt")
        if op == "add":
            if not isinstance(amt, (int, float)) or amt <= 0:
                return "нужна сумма"
            day = _key(a.get("day")) if a.get("day") else None
            o, warn = money.add(state, a.get("kind") or "exp", amt, str(a.get("cat") or "other"), a.get("acc"), str(a.get("note") or ""), day, "chat")
            return (f"записано: {'доход' if o['k'] == 'inc' else 'трата'} €{o['amt']:g} (id {o['id']}). На жизнь €{money.free(state):g}, всего €{money.total(m):g}."
                     + (" " + warn if warn else ""))
        if op == "balance":
            if not isinstance(amt, (int, float)):
                return "нужна сумма"
            ac = money.set_balance(state, a.get("acc"), amt, a.get("name"))
            return f"счёт {ac['n']}: €{money.balance(m, ac['id']):g}. Всего €{money.total(m):g}"
        if op == "transfer":
            if not isinstance(amt, (int, float)) or amt <= 0:
                return "нужна сумма"
            o = money.transfer(state, a.get("acc"), a.get("name"), amt, str(a.get("note") or ""), _key(a.get("day")) if a.get("day") else None)
            if not o:
                return "откуда и куда одинаковые"
            return f"перевод €{o['amt']:g}: {money.acct(m, o['acc'])['n']} → {money.acct(m, o['to'])['n']} (id {o['id']}). На жизнь €{money.free(state):g}, всего €{money.total(m):g}"
        if op == "limit":
            cat = str(a.get("cat") or "")
            if cat not in money.CATS:
                return "нет такой категории"
            m["lim"] = dict(m.get("lim") or {}, **{cat: float(amt or 0)})
            m["lu"] = int(time.time() * 1000)
            return f"лимит {money.CATS[cat][1]}: €{float(amt or 0):g} в месяц" if amt else "лимит убран"
        if op == "regular":
            nm = str(a.get("name") or "").strip()
            if not nm or not isinstance(amt, (int, float)):
                return "нужно название и сумма"
            r = next((x for x in m["reg"] if x["n"].lower() == nm.lower()), None) or {"id": money._id("r"), "n": nm[:40]}
            r.update({"amt": float(amt), "day": int(a.get("day") or 1) if str(a.get("day") or "1").isdigit() else 1, "cat": a.get("cat") if a.get("cat") in money.CATS else "other", "u": int(time.time() * 1000)})
            if r not in m["reg"]:
                m["reg"].append(r)
            return f"регулярный платёж: {r['n']} €{r['amt']:g} каждое {r['day']}-е"
        if op == "paid":
            nm = str(a.get("name") or "").lower()
            r = next((x for x in m["reg"] if nm and nm in x["n"].lower()), None)
            if not r:
                return "нет такого платежа"
            money.add(state, "exp", r["amt"], r.get("cat", "other"), r.get("acc"), r["n"], None, "reg:" + r["id"])
            r["paid"], r["u"] = money.ym(), int(time.time() * 1000)
            return f"{r['n']} оплачен, €{r['amt']:g} в тратах"
        if op == "debt":
            nm = str(a.get("name") or "").strip()
            if not nm or not isinstance(amt, (int, float)):
                return "кто и сколько"
            d = {"id": money._id("d"), "who": nm[:40], "amt": float(amt), "dir": a.get("dir") if a.get("dir") in ("owe_me", "i_owe") else "owe_me",
                 "n": str(a.get("note") or "")[:60], "d": quest.today_key(), "u": int(time.time() * 1000)}
            if a.get("day"):
                d["due"] = _key(a.get("day"))
            m["debts"].append(d)
            return f"долг записан: {d['who']} {'должен тебе' if d['dir'] == 'owe_me' else '— ты должен'} €{d['amt']:g}" + (f", вернуть до {d['due']}, напомню" if d.get("due") else "")
        if op == "debt_close":
            nm = str(a.get("name") or "").lower()
            d = next((x for x in money.debts_open(m) if nm and nm in x["who"].lower()), None)
            if not d:
                return "нет такого открытого долга"
            d["done"], d["u"] = quest.today_key(), int(time.time() * 1000)
            return f"долг {d['who']} €{d['amt']:g} закрыт"
        if op == "delete":
            return "удалено" if money.remove(state, str(a.get("id") or "")) else "нет такой операции"
        return "неизвестная операция"

    if name == "brain":
        from services import brain
        op = a.get("op")
        if op == "save":
            f = a.get("folder") if a.get("folder") in brain.FOLDERS and a.get("folder") != "inbox" else "ideas"
            it = brain.touch({"id": brain._new_id(), "f": f, "t": str(a.get("title") or "Заметка")[:60], "s": str(a.get("sum") or "")[:300],
                              "ctx": str(a.get("ctx") or "")[:120], "fit": "later", "src": "chat", "d": today})
            brain.get(state)["items"].append(it)
            return f"сохранено в мозг, папка {brain.FOLDERS[f]}"
        it = brain.find(state, str(a.get("id") or ""))
        if not it:
            return "нет такой записи в мозге"
        if op == "task":
            return "в плане на " + brain.to_task(state, it, _key(a.get("day")) if a.get("day") else None)
        if op == "done":
            it["st"] = "done"
            it.pop("pin", None)
        elif op == "move" and a.get("folder") in brain.FOLDERS:
            it["f"] = a["folder"]
        elif op == "delete":
            brain.remove(state, it["id"])
            return "удалено из мозга"
        elif op == "later":
            if it.get("pin"):
                it["pin"]["off"] = 1
            it["fit"] = "later"
        else:
            return "неизвестная операция"
        brain.touch(it)
        return "готово"

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
        state.setdefault("lifts", []).append({"d": today, "ex": ex, "kg": round(kg, 1), "reps": reps})
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

    if name == "set_reminder":
        t = _at(a.get("at"))
        if not t:
            return "нужно время HH:MM"
        k = _key(a.get("day"))
        if k == today and quest.to_min(t) < quest.to_min(quest.hhmm()):
            return f"{t} уже прошло, выбери время позже"
        r = {"id": "r" + _new_id()[1:], "d": k, "at": t, "text": str(a.get("text") or "")[:200], "sent": False}
        state["reminders"] = [x for x in state.get("reminders") or [] if not x.get("sent") or x.get("d", "") >= today] + [r]
        return f"напомню {k} в {t}: {r['text']} (id {r['id']})"

    if name == "delete_reminder":
        before = len(state.get("reminders") or [])
        state["reminders"] = [x for x in state.get("reminders") or [] if x.get("id") != a.get("id")]
        return "удалено" if len(state["reminders"]) < before else "нет такого"

    if name == "move_task":
        return quest.move_task(state, str(a.get("id") or ""), _key(a.get("to") or "tomorrow"))

    if name in ("house_add", "house_level", "house_remove", "shop_add", "chore_set", "project_step", "house_note", "house_take",
                "prep_add", "prep_eat", "project_new", "food_ate", "food_set"):
        from services import house
        h = house.get(state)
        if name == "prep_add":
            return house.prep_add(h, str(a.get("name", "")), int(a.get("portions") or 1), a.get("days"))
        if name == "prep_eat":
            return house.prep_eat(h, str(a.get("name") or ""), int(a.get("n") or 1))
        if name == "project_new":
            return house.project_new(h, str(a.get("name", "")), a.get("due"))
        if name == "house_take":
            return house.take(h, str(a.get("what", "")), h.get("owner"), bool(a.get("give")))
        if name == "house_note":
            t = str(a.get("text", "")).strip()[:200]
            if a.get("remove"):
                n = house._find([dict(x, name=x["t"]) for x in h["notes"]], t)
                if not n:
                    return "нет такой записи"
                h["notes"] = [x for x in h["notes"] if x["id"] != n["id"]]
                h["del"].append(n["id"])
                return "удалено из памяти дома"
            h["notes"].append({"id": house.new_id("n"), "t": t, "u": house.now_ms()})
            return "записал в память дома"
        if name == "house_add":
            got = house.add_items(h, a.get("items") or [])
            return "записал: " + ", ".join(got) if got else "нечего добавлять"
        if name == "food_ate":
            return "; ".join(house.use(h, str(x.get("name", "")), x.get("amt"), x.get("unit"), bool(x.get("all"))) for x in a.get("items") or []) or "пусто"
        if name == "food_set":
            return house.set_amt(h, str(a.get("name", "")), a.get("amt"), a.get("unit"))
        if name == "house_level":
            return house.set_level(h, str(a.get("name", "")), a.get("level", "ok"))
        if name == "house_remove":
            return house.remove_item(h, str(a.get("name", "")))
        if name == "shop_add":
            return house.shop_add(h, a.get("names") or [])
        if name == "chore_set":
            nm = str(a.get("name", "")).strip()[:60]
            c = house._find(h["chores"], nm)
            if a.get("remove"):
                if not c:
                    return "нет такого дела"
                h["chores"] = [x for x in h["chores"] if x is not c]
                h["del"].append(c["id"])
                return f"удалено: {c['name']}"
            if not c:
                c = {"id": house.new_id("c"), "name": nm, "every": 7, "dur": 20, "last": None}
                h["chores"].append(c)
            if a.get("every"):
                c["every"] = max(1, min(int(a["every"]), 90))
            if a.get("dur"):
                c["dur"] = max(5, min(int(a["dur"]), 240))
            c["u"] = house.now_ms()
            return f"{c['name']}: раз в {c['every']} дн, {c['dur']} мин"
        if name == "project_step":
            if a.get("add"):
                p = house._find(h["projects"], a.get("project") or "") or (h["projects"][0] if h["projects"] else None)
                if not p:
                    p = {"id": house.new_id("p"), "name": a.get("project") or "Проект", "steps": [], "u": house.now_ms()}
                    h["projects"].append(p)
                p["steps"].append({"id": house.new_id("s"), "t": str(a["text"])[:100], "done": False, "u": house.now_ms()})
                return f"шаг добавлен в «{p['name']}»"
            return house.step_done(h, str(a.get("text", "")), a.get("done", True) is not False)

    if name == "goal_flag":
        f = a.get("flag")
        if f != "earned":
            return "нет такого флага"
        state["goals"] = dict(state.get("goals") or {}, earned=True)
        return "отмечено: первые деньги с YouTube, подарок в цели открыт"

    if name == "take_reward":
        if not td.get("smokes"):
            td["firstSmoke"] = quest.hhmm()
        td["smokes"] = td.get("smokes", 0) + 1
        quest.log_ev(td, "reward", "")
        day = dict(td, _debt=quest.has_debt(state), _profile=P)
        return "записал. " + quest.reward_line(day).replace("\n", " ")

    return None


DAYS_RU = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"]


def routine_context(P: dict) -> str:
    """Распорядок человека: из чего собирается каждый день."""
    out = ["РАСПОРЯДОК (id: дело, мин, дни; ★ главное для награды, 📸 только пруфом):"]
    for it in quest.items(P):
        days = ",".join(DAYS_RU[d] for d in it["days"]) if it.get("days") else "каждый день"
        tags = ("★" if it.get("main") else "") + ("📸" if it.get("proof") or any(s.get("proof") for s in it.get("steps") or []) else "")
        at = " в " + ("конце дня" if it.get("at") == "end" else it["at"]) if it.get("at") else ""
        steps = " шаги: " + ", ".join(f"{s['id']} {s['name']}" for s in it["steps"]) if it.get("steps") else ""
        out.append(f"- {it['id']}: {it['name']}, {it.get('dur', 30)}, {days}{at} {tags}{steps}".rstrip())
    pen = P.get("penalty") or {}
    pi = quest.pen_item(P)
    out.append(f"ШТРАФ: день без «{pi['name']}» = завтра долг (повторить) и €{pen.get('fine') or 0} в {pen.get('to') or 'копилку'}" if pi else "ШТРАФ: выключен")
    out.append(f"НАГРАДА ДНЯ: {P.get('reward') or 'нет'}. Подъём {P['wake']}, отбой {P['bed']}.")
    g = P.get("goal")
    gs = [x for x in (P.get("goals") or []) if x.get("status") != "done"]
    if gs:
        out.append("ЦЕЛИ (дела цели в распорядке, число пиши через goal_value, вес log_weight, подписчики set_channel):")
        for x in gs:
            m = x.get("metric") or {}
            num = (f", {m.get('name') or 'число'} ({m.get('unit') or ''})" + (f", цель {m['target']:g}" if isinstance(m.get('target'), (int, float)) else "")) if m else ""
            out.append(f"- {x.get('emoji', '')} {'ЗДОРОВЬЕ (основа, не цель): ' if x.get('kind') == 'health' else ''}{x['name']} (id {x['id']}){num}" + (f", к {x['date']}" if x.get("date") else "") + f", дела: {', '.join(x.get('tasks') or []) or 'нет'}")
    else:
        out.append(f"ТЕКУЩАЯ ЦЕЛЬ: {g['name']} ({g.get('unit') or 'без цифры'}), привычка: {g.get('habit') or 'не задана'}" if g else "ТЕКУЩАЯ ЦЕЛЬ: нет (здоровье есть всегда)")
    return "\n".join(out)


def plan_context(state: dict) -> str:
    """План на сегодня с id и свои дела на завтра: чтобы модель знала, чем управлять."""
    today = quest.today_key()
    day = dict(quest._day(state, today))
    day["_debt"] = quest.has_debt(state)
    day["_profile"] = P = quest.full_profile(state)
    day["_learn"] = quest.learned(state)
    day["_chores"] = quest.chores_for_plan(state)
    p = quest.build_plan(day)
    lines = [routine_context(P), "ПЛАН НА СЕГОДНЯ (id: дело, время):"]
    for it in p["items"]:
        if it.get("game"):
            lines.append(f"- отдых {quest.hm(it['start'])}–{quest.hm(it['end'])}")
        elif not it.get("gap"):
            lines.append(f"- {it['id']}: {it['name']} {quest.hm(it['start'])}–{quest.hm(it['end'])}" + (" (идёт сейчас)" if it.get("started") else ""))
    for o in p["over"]:
        lines.append(f"- {o['id']}: {o['name']} (не влезает до отбоя)")
    now_m = quest.now_min()
    spans = sorted((it["start"], it["end"]) for it in p["items"] if not it.get("gap"))
    free, cur = [], max(now_m, quest.to_min(P["wake"]))
    for s0, e0 in spans:
        if s0 - cur >= 20:
            free.append(f"{quest.hm(cur)}–{quest.hm(s0)}")
        cur = max(cur, e0)
    bed = quest.bed_min(P) - 45
    if bed - cur >= 20:
        free.append(f"{quest.hm(cur)}–{quest.hm(bed)}")
    lines.append("СВОБОДНЫЕ ОКНА СЕГОДНЯ: " + (", ".join(free) if free else "нет, день забит"))
    rem = [r for r in state.get("reminders") or [] if not r.get("sent")]
    if rem:
        lines.append("НАПОМИНАНИЯ: " + "; ".join(f"{r['id']}: {r['d']} {r['at']} {r['text']}" for r in rem))
    done = day.get("done", [])
    if done:
        lines.append("Сделано: " + ", ".join(f"{_name(state, x)} в {day['at'].get(x, '?')}" for x in done))
    tk = quest.tomorrow_key()
    tc = (state.get("days", {}).get(tk) or {}).get("custom", [])
    if tc:
        lines.append("СВОИ ДЕЛА НА ЗАВТРА: " + "; ".join(f"{c['id']}: {c['name']}" + (f" в {c['at']}" if c.get("at") else "") for c in tc))
    L = day["_learn"]
    if L:
        nm = quest.names(P)
        lines.append("ТВОЁ РЕАЛЬНОЕ ВРЕМЯ (медиана): " + ", ".join(f"{nm.get(k, k)} {v['min']} мин" for k, v in L.items()))
    from services import house as _h
    if isinstance(state.get("house"), dict):
        lines.append("ДОМ:\n" + _h.summary(state["house"]))
    ev = day.get("ev") or []
    if ev:
        lines.append("ЖУРНАЛ СЕГОДНЯ: " + "; ".join(f"{t} {e} {_name(state, i) if i and not i.isdigit() else i}".strip() for t, e, i in ev[-15:]))
    return "\n".join(lines)
