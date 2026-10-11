import asyncio
import json
import logging
import os
import re

from anthropic import AsyncAnthropic

import database
from config import ANTHROPIC_API_KEY
from prompts.system import SYSTEM_PROMPT

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
logger = logging.getLogger(__name__)


CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-sonnet-4-5")
# старые сообщения «ментора по Upwork» в историю не берём
CHAT_SINCE = "2026-10-11 08:58:00"

TOOLS = [
    {"name": "remember",
     "description": "Сохранить важный долгий факт об Андрее (решение, цель, изменение в жизни, договорённость). Не для мелочей.",
     "input_schema": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}},
    {"name": "forget",
     "description": "Удалить сохранённый факт, если он устарел или Андрей просит забыть. Передай номер факта из списка.",
     "input_schema": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}},
    {"name": "set_reply_mode",
     "description": "Как отвечать на голосовые Андрея: text = только текстом, voice = только голосом, both = голосом и текстом. Вызывай ВСЕГДА, когда он просит включить, выключить или поменять голосовые ответы, в любой формулировке. Не говори, что поменял, если не вызвал.",
     "input_schema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["text", "voice", "both"]}}, "required": ["mode"]}},
    {"name": "set_theory_topic",
     "description": "Перейти на тему теории CBR по номеру из плана (1–8 темы, 9 = полные пробные экзамены).",
     "input_schema": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}},
]


MODE_NAMES = {"text": "только текстом", "voice": "только голосом", "both": "голосом и текстом"}


def reply_mode(state: dict) -> str:
    st = state.get("settings") or {}
    if st.get("reply") in MODE_NAMES:
        return st["reply"]
    return "text" if st.get("voice") is False else "both"


def _where(state: dict) -> str:
    import time as _t
    home, loc = state.get("home") or {}, state.get("loc") or {}
    out = "ГДЕ ОН: "
    if loc.get("lat") and _t.time() - loc.get("t", 0) < 7200:
        out += f"геолокация {int((_t.time() - loc['t']) / 60)} мин назад ({loc['lat']:.4f}, {loc['lon']:.4f})" + (", живая" if loc.get("live") else "")
    else:
        out += "свежей геолокации нет"
    out += f"; дом: {home['address']}" if home.get("address") else "; адрес дома не сохранён"
    return out + "\n\n"


async def build_context_block(telegram_id: int, state: dict, day: dict) -> str:
    from services import actions, quest, theory
    n = quest.now()
    notes = state.get("notes") or []
    goals = state.get("goals") or {}
    body = state.get("body") or []
    return (
        "=== КОНТЕКСТ ===\n"
        f"Сейчас: {n.strftime('%H:%M, %A %d.%m.%Y')} (Нидерланды)\n\n"
        f"СЕГОДНЯ В КВЕСТЕ:\n{re.sub(r'<[^>]+>', '', quest.day_summary(day))}\n\n"
        f"{actions.plan_context(state)}\n\n"
        f"КАНАЛ: подписчиков {goals.get('subs', 0)}, лучшее видео {goals.get('maxViews', 0)} просмотров\n"
        + (f"ВЕС: {body[-1]['kg']} кг ({body[-1]['d']})" + (f", цель {goals['goalKg']} кг" if goals.get("goalKg") else "") + "\n" if body else "")
        + f"\nТЕОРИЯ CBR:\n{theory.context(state)}\n\n"
        + f"ОТВЕТЫ НА ГОЛОСОВЫЕ СЕЙЧАС: {MODE_NAMES[reply_mode(state)]}\n\n"
        + _where(state)
        + "ЧТО ТЫ ЗНАЕШЬ О НЁМ (память):\n" + ("\n".join(f"{i + 1}. {x}" for i, x in enumerate(notes)) or "пока ничего") + "\n"
        "================\n"
    )


def _run_tool(state: dict, name: str, args: dict) -> str:
    from services import actions, theory
    res = actions.run(state, name, args)
    if res is not None:
        return res
    if name == "remember":
        fact = str(args.get("fact", "")).strip()[:200]
        if fact:
            state["notes"] = ((state.get("notes") or []) + [fact])[-40:]
        return "сохранено"
    if name == "forget":
        notes = state.get("notes") or []
        k = int(args.get("n", 0)) - 1
        if 0 <= k < len(notes):
            notes.pop(k)
            state["notes"] = notes
            return "удалено"
        return "нет такого номера"
    if name == "set_reply_mode":
        mode = args.get("mode")
        if mode not in ("text", "voice", "both"):
            return "неизвестный режим"
        state["settings"] = dict(state.get("settings") or {}, reply=mode)
        return {"text": "теперь только текст", "voice": "теперь только голос", "both": "теперь голос и текст"}[mode]
    if name == "set_theory_topic":
        return "теперь: " + theory.set_topic(state, int(args.get("n", 1)) - 1)
    return "неизвестный инструмент"


def _actions_tools() -> list:
    from services import actions
    return actions.TOOLS


CLAIM_RE = re.compile(r"(?i)\b(добавил|поставил|записал|запомнил|сохранил|удалил|убрал|перенёс|перенес|отметил|напомню|изменил|сдвинул)\b")
FALLBACK_MODEL = "claude-haiku-4-5"


async def _create(**kw):
    try:
        return await client.messages.create(model=CHAT_MODEL, **kw)
    except Exception as e:
        logger.error(f"chat model {CHAT_MODEL} failed: {e}; fallback")
        return await client.messages.create(model=FALLBACK_MODEL, **kw)


async def get_ai_response(telegram_id: int, user_message: str, voice: bool = False) -> str:
    """Свободный диалог: Claude с контекстом дня, памятью и инструментами."""
    from services import quest
    day = await quest.get_day(telegram_id)
    state = await database.get_quest_state(telegram_id)
    # постоянная часть кэшируется (дешевле), контекст дня идёт после неё
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": await build_context_block(telegram_id, state, day)}]
    tools = TOOLS + _actions_tools()
    tools = tools[:-1] + [dict(tools[-1], cache_control={"type": "ephemeral"})]

    history = await database.get_recent_messages(telegram_id, limit=24, since=CHAT_SINCE)
    messages = []
    for row in history:
        if messages and messages[-1]["role"] == row["role"]:
            messages[-1]["content"] += "\n" + row["content"]
        else:
            messages.append({"role": row["role"], "content": row["content"]})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    text_in = ("[голосовое] " if voice else "") + user_message
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n" + text_in
    else:
        messages.append({"role": "user", "content": text_in})

    changed = False
    acted = False   # был ли хоть один инструмент, который что-то меняет
    checked = False
    reply = ""
    for _ in range(8):
        response = await _create(max_tokens=1200, system=system, messages=messages, tools=tools)
        reply = "".join(b.text for b in response.content if b.type == "text").strip()
        uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not uses:
            # сказал «сделал», а инструмент не вызывал: один раз заставляем сделать по-честному
            if not acted and not checked and CLAIM_RE.search(reply):
                checked = True
                logger.warning(f"claim without tool: {reply[:120]!r}")
                messages.append({"role": "assistant", "content": reply or "..."})
                messages.append({"role": "user", "content": "[проверка бота] Ты написал, что сделал действие, но не вызвал ни одного инструмента, поэтому ничего не сохранилось. Сделай это сейчас нужным инструментом. Если сделать нельзя, честно напиши, что не сделал и почему. Пользователю ответь заново, без упоминания этой проверки."})
                continue
            break
        messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})
        results = []
        for b in uses:
            if b.name == "web_lookup":
                from services import websearch
                res = await websearch.look_up(str((b.input or {}).get("query", "")))
            elif b.name == "travel_time":
                from services import geo
                i = b.input or {}
                res = await geo.travel(state, str(i.get("to", "")), str(i.get("from", "") or ""), str(i.get("mode", "car") or "car"))
            elif b.name == "set_home":
                from services import geo
                addr = str((b.input or {}).get("address", ""))[:120]
                loc = state.get("loc") or {}
                if addr.lower() in ("here", "сейчас", "тут", "здесь", "геолокация") and loc.get("lat"):
                    g = {"lat": loc["lat"], "lon": loc["lon"], "name": "точка с геолокации"}
                    addr = "по геолокации"
                else:
                    g = await geo.geocode(addr if "," in addr or "dronten" in addr.lower() else addr + ", Dronten")
                if g:
                    state["home"] = {"address": addr, "lat": g["lat"], "lon": g["lon"]}
                    res = f"дом сохранён: {g['name']}"
                    changed = acted = True
                else:
                    res = "не нашёл адрес на карте, уточни"
            else:
                res = _run_tool(state, b.name, b.input or {})
                changed = acted = True
            logger.info(f"tool {b.name} {json.dumps(b.input, ensure_ascii=False)[:200]} -> {str(res)[:200]}")
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": res})
        messages.append({"role": "user", "content": results})

    if changed:
        # пока модель думала, приложение могло что-то сохранить: сливаем, а не затираем
        fresh = await database.get_quest_state(telegram_id)
        await database.save_quest_state(telegram_id, quest.merge_incoming(fresh, state))
    reply = reply or "Готово."
    await database.add_message(telegram_id, "user", text_in)
    await database.add_message(telegram_id, "assistant", reply)
    asyncio.create_task(learn_facts(telegram_id, user_message, reply))
    return reply


FACTS_PROMPT = """Ты ведёшь долгую память о человеке (Андрей). Уже записано:
{notes}

Новый обмен:
Андрей: {user}
Помощник: {reply}

Задача: найти НОВЫЕ ДОЛГИЕ факты, которые АНДРЕЙ САМ СКАЗАЛ в своём сообщении.
Строго:
- Только из слов Андрея. Ответ помощника не источник фактов (он может ошибаться).
- Только то, что будет правдой недели и месяцы: адрес, машина, сколько ему реально ехать до мест (если он сам назвал), привычки, предпочтения, люди, решения, цели.
- НЕ записывай разовые дела, планы на сегодня, покупки, напоминания, часы работы магазинов: это хранится в плане, не в памяти.
- Названия и адреса пиши как на карте (латиницей для нидерландских улиц, например De Oxer), а не как в расшифровке голоса.
- Не дублируй записанное. replace только если Андрей прямо поменял старый факт (переехал, сменил машину).
Ответь ТОЛЬКО JSON: {{"add": ["факт"], "replace": {{"номер": "новая версия"}}}}. Если нечего, {{"add": [], "replace": {{}}}}."""


async def learn_facts(telegram_id: int, user: str, reply: str) -> None:
    """После каждого сообщения дешёвая модель сама дописывает в память важное о нём."""
    try:
        if len(user.strip()) < 12:
            return
        st = await database.get_quest_state(telegram_id)
        notes = list(st.get("notes") or [])
        r = await client.messages.create(
            model=FALLBACK_MODEL, max_tokens=400,
            messages=[{"role": "user", "content": FACTS_PROMPT.format(
                notes="\n".join(f"{i + 1}. {x}" for i, x in enumerate(notes)) or "пусто",
                user=user[:1500], reply=reply[:800])}])
        text = "".join(b.text for b in r.content if b.type == "text")
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else {}
        changed = False
        for k, v in (data.get("replace") or {}).items():
            i = int(k) - 1
            if 0 <= i < len(notes) and str(v).strip():
                notes[i] = str(v).strip()[:200]
                changed = True
        for f in data.get("add") or []:
            f = str(f).strip()[:200]
            if f and f not in notes:
                notes.append(f)
                changed = True
        if changed:
            fresh = await database.get_quest_state(telegram_id)
            fresh["notes"] = notes[-80:]
            await database.save_quest_state(telegram_id, fresh)
            logger.info(f"memory: {data}")
    except Exception as e:
        logger.error(f"learn_facts error: {e}")


PROOF_PROMPT = """Ты проверяешь пруф выполнения задачи для Андрея. Он прислал фото.
Подпись к фото: "{caption}"
Сегодня по плану тренировка: {training}.

Определи, что на фото:
- "cbr" — скрин с TheorieExamen.nl (или другой практики теории на права в Нидерландах: verkeersborden, gevaarherkenning, вопросы CBR): пройденный урок или результат теста. Если виден результат, оцени: на экзамене проходной 44 из 50 (88%).
- "eng" — скрин Duolingo: пройденный урок, экран «урок завершён», XP за сегодня или ударный режим (streak).
- "training" — Андрей в зале или на тренировке (селфи в спортзале, тренажёры, ринг, мешок, перчатки) или скрин трекера тренировки.
- "fine" — скрин Trading 212: пополнение счёта или покупка в пироге (pie) safemoney, видно сумму и сегодняшнюю дату или «только что».
- "video" — скрин загрузки/публикации видео в YouTube Studio или опубликованное видео на канале.
- "question" — скрин ОДНОГО вопроса по теории на права (картинка ситуации, знак, варианты ответа), а не итог теста. Тогда в comment объясни по-русски простыми словами правильный ответ и правило (3-5 предложений), с английским термином в скобках. ok = true.
- "none" — всё остальное, или пруф явно старый/фейковый/не относится к задаче (например письмо, чек об оплате, реклама).

Если "cbr" и виден итог теста: score = сколько правильных, total = сколько всего вопросов, weak = коротко, по каким темам ошибки (если видно), иначе пусто.

Ответь ТОЛЬКО JSON без markdown:
{{"quest": "cbr|eng|fine|training|video|question|none", "ok": true/false, "score": null, "total": null, "weak": "", "comment": "1-2 коротких предложения по-русски, по делу. Для question: объяснение."}}"""


async def check_proof(image_bytes: bytes, caption: str, training: str) -> dict:
    """Проверяет фото-пруф через Claude. Возвращает {"quest", "ok", "comment"}."""
    import base64
    import json
    import re

    training_name = "единоборства" if training == "fight" else "зал"
    try:
        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=700,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(image_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": PROOF_PROMPT.format(caption=caption[:300], training=training_name)},
                ],
            }],
        )
        text = response.content[0].text
        match = re.search(r"\{.*\}", text, re.S)
        data = json.loads(match.group(0)) if match else {}
        def num(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        return {
            "quest": str(data.get("quest", "none")),
            "ok": bool(data.get("ok")),
            "comment": str(data.get("comment", "")),
            "score": num(data.get("score")),
            "total": num(data.get("total")),
            "weak": str(data.get("weak") or ""),
        }
    except Exception:
        return {"quest": "none", "ok": False, "comment": "Не смог проверить фото. Скинь ещё раз."}
