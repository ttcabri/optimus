"""Разбор недели: бот перечитывает журнал дел и переписку и записывает, что понял о человеке.

Выводы лежат в state["insights"] и идут в контекст каждого ответа. Так бот со временем знает человека лучше.
"""
import html
import json
import logging
import re
from datetime import timedelta

import database
from services import quest

logger = logging.getLogger(__name__)
MAX_ITEMS = 12

PROMPT = """Ты личный помощник человека и раз в неделю делаешь разбор: что ты понял о нём по фактам.

ЕГО РАСПОРЯДОК, ШТРАФ, ЦЕЛЬ:
{routine}

ЖУРНАЛ ДЕЛ ЗА 14 ДНЕЙ (время, событие, дело, сколько заняло):
{log}

ПЕРЕПИСКА С НИМ ЗА НЕДЕЛЮ (кусками):
{chat}

ПАМЯТЬ О НЁМ:
{notes}

ПРОШЛЫЕ ВЫВОДЫ (обнови: подтверди, исправь или убери то, что больше не так):
{old}

Сделай:
1. insights: до {n} коротких выводов о нём, только то, что видно по фактам выше. Паттерны: сколько на самом деле занимают дела против плана, в какие дни и часы он продуктивнее, что пропускает и когда, где отдых съедает вечер, что работает. Плюс как с ним лучше говорить, если это видно из переписки. Каждый вывод одной фразой, с цифрой, если есть. Без воды, без оценок личности, без диагнозов.
2. message: сообщение ему на неделю, по-русски, разговорно, 4–7 коротких строк: главное, что заметил (с цифрами), и одно конкретное предложение, что поменять в распорядке или плане. Без длинных тире, без markdown.
Если данных мало, так и скажи в message и дай меньше выводов.

Ответь ТОЛЬКО JSON: {{"insights": ["..."], "message": "..."}}"""


def _lines(rows) -> str:
    out, day = [], None
    for d, t, ev, name, m in rows:
        if d != day:
            day = d
            out.append(d + ":")
        out.append(f"  {t} {ev} {name}" + (f" ({m} мин)" if m else ""))
    return "\n".join(out)


async def run(uid: int) -> str | None:
    """Делает разбор, сохраняет выводы, возвращает текст для человека (None, если не вышло)."""
    from services import actions, claude
    state = await database.get_quest_state(uid)
    now = quest.now()
    rows = await database.get_log(uid, (now - timedelta(days=13)).strftime("%Y-%m-%d"))
    msgs = await database.get_recent_messages(uid, 80, (now - timedelta(days=7)).strftime("%Y-%m-%d"))
    chat = "\n".join(f"{'он' if m['role'] == 'user' else 'ты'}: {str(m['content'])[:300]}" for m in msgs[-60:])
    old = (state.get("insights") or {}).get("items") or []
    prompt = PROMPT.format(routine=actions.routine_context(quest.full_profile(state)), log=_lines(rows)[-12000:] or "пусто",
                           chat=chat[-8000:] or "нет", notes="\n".join(state.get("notes") or []) or "пусто",
                           old="\n".join(f"- {x}" for x in old) or "нет", n=MAX_ITEMS)
    try:
        r = await claude.client.messages.create(model=claude.CHAT_MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in r.content if b.type == "text")
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except Exception as e:
        logger.error(f"review error: {e}")
        return None
    items = [str(x).strip()[:200] for x in data.get("insights") or [] if str(x).strip()][:MAX_ITEMS]
    fresh = await database.get_quest_state(uid)
    fresh["insights"] = {"d": quest.today_key(), "items": items}
    await database.save_quest_state(uid, fresh)
    logger.info(f"review: {len(items)} insights")
    return str(data.get("message") or "").strip() or None


async def send(bot, uid: int) -> None:
    """Воскресенье вечером: разбор недели сообщением."""
    from services import morning
    if not morning.notify(await database.get_quest_state(uid)).get("review", True):
        return
    msg = await run(uid)
    if msg:
        from handlers.proof import quest_keyboard
        await bot.send_message(uid, "🧠 <b>Разбор недели</b>\n\n" + html.escape(msg), reply_markup=quest_keyboard())
