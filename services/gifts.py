"""Подбор реальных подарков за уровень: Claude + веб-поиск по вкусам из «Мои вкусы»."""
import json
import logging
import re
import time

import httpx

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5"
_last_call: dict[int, float] = {}

PROMPT = """Ты подбираешь реальную награду человеку за то, что он взял уровень {level} в своём трекере дисциплины.
Найди в интернете 3 РЕАЛЬНЫХ варианта, которые он может купить или заказать сейчас.

Человек:
- город: {city} (Нидерланды, если не указано иное)
- бюджет: до €{budget}
- что радует: {likes}
- любимая еда: {foods}
- ещё любит: {more}

Правила:
- Только реальные места и товары, которые существуют: конкретный ресторан в его городе или рядом (до 30 минут), конкретная доставка (Thuisbezorgd, Uber Eats), конкретная вещь в магазине (Zalando, bol.com, официальный сайт бренда), конкретное развлечение рядом.
- Цена в пределах бюджета. Если точную цену не нашёл, пиши «около €X».
- Разнообразь: не три ресторана, а разные типы наград, если человеку нравится разное.
- Ссылка на страницу места или товара, если нашёл.

Ответь ТОЛЬКО JSON-массивом без markdown, 3 элемента:
[{{"title": "коротко, что это (до 6 слов)", "what": "одна фраза, почему это кайф", "where": "место или магазин", "price": "€..", "link": "https://..."}}]"""


async def suggest_gifts(telegram_id: int, prefs: dict, level: int) -> list[dict]:
    now = time.time()
    if now - _last_call.get(telegram_id, 0) < 30:
        raise RuntimeError("too_often")
    _last_call[telegram_id] = now

    prompt = PROMPT.format(
        level=level,
        city=(prefs.get("city") or "Dronten")[:40],
        budget=int(prefs.get("budget") or 25),
        likes=", ".join(prefs.get("likes") or []) or "не указано",
        foods=", ".join(prefs.get("foods") or []) or "не указано",
        more=(prefs.get("more") or "не указано")[:120],
    )
    body = {
        "model": MODEL,
        "max_tokens": 1500,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(API_URL, headers=headers, json=body)
    if r.status_code != 200:
        logger.error(f"Gift search error {r.status_code}: {r.text[:300]}")
        raise RuntimeError("api_error")
    return parse_ideas(r.json())


def parse_ideas(data: dict) -> list[dict]:
    """Склеивает текстовые блоки ответа и достаёт JSON-массив."""
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for g in raw[:3]:
        if not isinstance(g, dict) or not g.get("title"):
            continue
        link = str(g.get("link") or "")
        out.append({
            "title": str(g["title"])[:60],
            "what": str(g.get("what") or "")[:160],
            "where": str(g.get("where") or "")[:80],
            "price": str(g.get("price") or "")[:20],
            "link": link if link.startswith(("http://", "https://")) else "",
        })
    return out
