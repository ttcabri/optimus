import os
import re

from anthropic import AsyncAnthropic

import database
from config import ANTHROPIC_API_KEY
from prompts.system import SYSTEM_PROMPT

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-haiku-4-5")
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


async def build_context_block(telegram_id: int, state: dict, day: dict) -> str:
    from services import quest, theory
    n = quest.now()
    notes = state.get("notes") or []
    goals = state.get("goals") or {}
    body = state.get("body") or []
    return (
        "=== КОНТЕКСТ ===\n"
        f"Сейчас: {n.strftime('%H:%M, %A %d.%m.%Y')} (Нидерланды)\n\n"
        f"СЕГОДНЯ В КВЕСТЕ:\n{re.sub(r'<[^>]+>', '', quest.day_summary(day))}\n\n"
        f"КАНАЛ: подписчиков {goals.get('subs', 0)}, лучшее видео {goals.get('maxViews', 0)} просмотров\n"
        + (f"ВЕС: {body[-1]['kg']} кг ({body[-1]['d']})" + (f", цель {goals['goalKg']} кг" if goals.get("goalKg") else "") + "\n" if body else "")
        + f"\nТЕОРИЯ CBR:\n{theory.context(state)}\n\n"
        + f"ОТВЕТЫ НА ГОЛОСОВЫЕ СЕЙЧАС: {MODE_NAMES[reply_mode(state)]}\n\n"
        + "ЧТО ТЫ ЗАПОМНИЛ РАНЬШЕ:\n" + ("\n".join(f"{i + 1}. {x}" for i, x in enumerate(notes)) or "пока ничего") + "\n"
        "================\n"
    )


def _run_tool(state: dict, name: str, args: dict) -> str:
    from services import theory
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


async def get_ai_response(telegram_id: int, user_message: str, voice: bool = False) -> str:
    """Свободный диалог: Claude с контекстом дня, памятью и инструментами."""
    from services import quest
    day = await quest.get_day(telegram_id)
    state = await database.get_quest_state(telegram_id)
    system = await build_context_block(telegram_id, state, day) + "\n" + SYSTEM_PROMPT

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
    reply = ""
    for _ in range(4):
        response = await client.messages.create(
            model=CHAT_MODEL, max_tokens=1200, system=system, messages=messages, tools=TOOLS,
        )
        reply = "".join(b.text for b in response.content if b.type == "text").strip()
        uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not uses:
            break
        messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})
        results = []
        for b in uses:
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": _run_tool(state, b.name, b.input or {})})
            changed = True
        messages.append({"role": "user", "content": results})

    if changed:
        fresh = await database.get_quest_state(telegram_id)
        for k in ("notes", "theory", "settings"):
            if k in state:
                fresh[k] = state[k]
        await database.save_quest_state(telegram_id, fresh)
    reply = reply or "Записал."
    await database.add_message(telegram_id, "user", text_in)
    await database.add_message(telegram_id, "assistant", reply)
    return reply


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
