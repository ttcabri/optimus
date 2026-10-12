"""Цель одной фразой: модель разбирает текст, задаёт 2–3 вопроса под эту цель и предлагает план
(дела, как меряем, этапы с подарками). Приложение показывает план, человек утверждает или правит."""
import json
import logging
import re

from services import quest

logger = logging.getLogger(__name__)

RULES = """Ты помогаешь человеку превратить желание в реальный план в приложении «Квест дня».
В приложении: дела дня (распорядок), цели, огонёк дней подряд по делу цели, подарки за этапы, награда дня и штраф (по желанию).
Сегодня {today} ({wd}). Подъём {wake}, отбой {bed}.
Уже в распорядке (дело, мин, дни): {routine}
Уже есть цели: {goals}

Цель человека своими словами: «{text}»"""

ASK = RULES + """

Задача: пойми цель и задай 1–3 коротких вопроса, БЕЗ которых план будет нереальным (сумма, срок, текущий уровень, сколько времени есть, как часто удобно).
Не спрашивай то, что уже ясно из текста. Варианты ответов давай, где можно угадать (3–4 коротких), для чисел type number с unit.
Ответь ТОЛЬКО JSON:
{{"understood": "одна строка: как ты понял цель", "questions": [{{"id": "q1", "q": "вопрос", "type": "choice|number|text", "options": ["..."], "unit": "€"}}]}}"""

PLAN = RULES + """
Ответы на уточнения: {answers}
{fix}
Составь реальный план. Правила:
- tasks: 1–3 дела. Регулярные: days = дни недели (0=вс,1=пн..6=сб), dur в минутах. Разовые: once = "YYYY-MM-DD".
  Дело называй действием, коротко: «Отложить €40», «Теория 45 мин», «Зал». Не дублируй то, что уже в распорядке: если такое дело есть, верни его "use": "<id>" вместо нового.
  at = "HH:MM" только если время реально жёсткое, иначе null.
- metric: одно число, по которому видно прогресс, или null (например цель «рисовать каждый день»).
  mode "add" если число копится (деньги отложены, страниц прочитано), "set" если вводится текущее значение (вес, подписчики, баллы теста).
  ask: "after" спросить после дела (для add), "weekly" раз в неделю (вес, подписчики), "proof" по скрину (баллы теста).
  start = сколько сейчас (из ответов), target = сколько нужно.
- date: дата цели "YYYY-MM-DD" или null.
- milestones: 2–4 этапа по числу метрики с подарком (gift — конкретная небольшая вещь или удовольствие, budget в €). Последний этап = сама цель. Если метрики нет, milestones пустой.
- streak: 1–2 подарка за дни подряд по главному делу [{{"days": 14, "gift": "...", "budget": 15}}].
- reward_day: короткая награда за закрытый день (что человек любит, если не знаешь: «Любимый сериал») или null.
- penalty_fine: предлагаемый штраф в € за пропуск главного дела (обычно 5) или null, если цель мягкая.
- summary: одна строка, сколько и как часто (например «≈ €41 в неделю, успеваешь к 1 июня»).
Ответь ТОЛЬКО JSON:
{{"name": "коротко, 1–3 слова", "emoji": "один эмодзи", "summary": "...", "tasks": [{{"name": "...", "dur": 30, "days": [1,3,5], "at": null, "once": null, "use": null}}],
 "metric": {{"name": "Накоплено", "unit": "€", "start": 0, "target": 1500, "mode": "add", "ask": "after"}}, "date": null,
 "milestones": [{{"at": 500, "gift": "...", "budget": 30}}], "streak": [{{"days": 14, "gift": "...", "budget": 15}}], "reward_day": null, "penalty_fine": null}}"""

WD = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _ctx(state: dict, text: str) -> dict:
    P = quest.full_profile(state)
    days = lambda it: ",".join(str(d) for d in it["days"]) if it.get("days") else "каждый день"
    routine = "; ".join(f"{it['id']}: {it['name']}, {it.get('dur', 30)}, {days(it)}" for it in quest.items(P)) or "пусто"
    goals = ", ".join(g.get("name", "") for g in (P.get("goals") or []) if g.get("status") != "done") or (P.get("goal") or {}).get("name") or "нет"
    n = quest.lnow()
    return {"today": n.strftime("%Y-%m-%d"), "wd": WD[n.weekday()], "wake": P["wake"], "bed": P["bed"], "routine": routine, "goals": goals, "text": text[:1200]}


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


async def _ask_model(prompt: str, max_tokens: int) -> dict:
    from services import claude
    r = await claude.client.messages.create(model=claude.CHAT_MODEL, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return _json("".join(b.text for b in r.content if b.type == "text"))


async def questions(state: dict, text: str) -> dict:
    out = await _ask_model(ASK.format(**_ctx(state, text)), 700)
    qs = [q for q in out.get("questions") or [] if isinstance(q, dict) and q.get("q")][:3]
    for i, q in enumerate(qs):
        q["id"] = q.get("id") or f"q{i + 1}"
        if q.get("type") not in ("choice", "number", "text"):
            q["type"] = "text"
        q["options"] = [str(o)[:40] for o in (q.get("options") or [])][:4]
    return {"understood": str(out.get("understood") or "")[:160], "questions": qs}


def _clean(p: dict) -> dict:
    tasks = []
    for t in p.get("tasks") or []:
        if not isinstance(t, dict) or not t.get("name") and not t.get("use"):
            continue
        days = [int(d) for d in t.get("days") or [] if str(d).isdigit() and 0 <= int(d) <= 6]
        at = t.get("at") if isinstance(t.get("at"), str) and re.match(r"^\d{2}:\d{2}$", t.get("at") or "") else None
        once = t.get("once") if isinstance(t.get("once"), str) and re.match(r"^\d{4}-\d{2}-\d{2}$", t.get("once") or "") else None
        tasks.append({"name": str(t.get("name") or "")[:60], "dur": max(5, min(int(t.get("dur") or 30), 480)), "days": days, "at": at, "once": once, "use": t.get("use") or None})
    m = p.get("metric") if isinstance(p.get("metric"), dict) else None
    if m:
        num = lambda v: float(v) if isinstance(v, (int, float)) or (isinstance(v, str) and re.match(r"^-?\d+([.,]\d+)?$", v)) else None
        m = {"name": str(m.get("name") or "")[:30], "unit": str(m.get("unit") or "")[:15], "start": num(str(m.get("start")).replace(",", ".")) if m.get("start") is not None else 0,
             "target": num(str(m.get("target")).replace(",", ".")) if m.get("target") is not None else None,
             "mode": "add" if m.get("mode") == "add" else "set", "ask": m.get("ask") if m.get("ask") in ("after", "weekly", "proof") else "weekly"}
    date = p.get("date") if isinstance(p.get("date"), str) and re.match(r"^\d{4}-\d{2}-\d{2}$", p.get("date") or "") else None
    ms = [{"at": float(x["at"]), "gift": str(x.get("gift") or "")[:60], "budget": int(x.get("budget") or 0)} for x in p.get("milestones") or [] if isinstance(x, dict) and isinstance(x.get("at"), (int, float))][:4]
    st = [{"days": int(x["days"]), "gift": str(x.get("gift") or "")[:60], "budget": int(x.get("budget") or 0)} for x in p.get("streak") or [] if isinstance(x, dict) and str(x.get("days", "")).isdigit()][:2]
    return {"name": str(p.get("name") or "Цель")[:24], "emoji": str(p.get("emoji") or "🎯")[:4], "summary": str(p.get("summary") or "")[:120],
            "tasks": tasks[:3], "metric": m, "date": date, "milestones": ms if m else [], "streak": st,
            "reward_day": (str(p["reward_day"])[:30] if p.get("reward_day") else None),
            "penalty_fine": int(p["penalty_fine"]) if isinstance(p.get("penalty_fine"), (int, float)) and p["penalty_fine"] > 0 else None}


async def plan(state: dict, text: str, answers: list, fix: str = "", prev: dict | None = None) -> dict:
    ans = "; ".join(f"{a.get('q')}: {a.get('a')}" for a in answers if isinstance(a, dict) and a.get("a")) or "нет"
    fx = ""
    if fix:
        fx = f"Прошлый план: {json.dumps(prev or {}, ensure_ascii=False)[:2000]}\nЧеловек просит поправить: «{fix[:400]}». Сделай новый план с учётом этого."
    out = await _ask_model(PLAN.format(**_ctx(state, text), answers=ans, fix=fx), 1500)
    return _clean(out)
