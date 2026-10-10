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
    if now - _last_call.get(telegram_id, 0) < 10:
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
    async with httpx.AsyncClient(timeout=120) as client:
        data = {}
        for _ in range(3):  # web search может вернуть pause_turn: тогда продолжаем
            r = await client.post(API_URL, headers=headers, json=body)
            if r.status_code != 200:
                logger.error(f"Gift search error {r.status_code}: {r.text[:500]}")
                _last_call.pop(telegram_id, None)
                raise RuntimeError("api_error")
            data = r.json()
            if data.get("stop_reason") != "pause_turn":
                break
            body["messages"] = body["messages"] + [{"role": "assistant", "content": data.get("content", [])}]
        ideas = parse_ideas(data)
        if ideas:
            return ideas
        # запасной шаг: найденное превращаем в JSON без поиска
        text = _text(data)
        logger.warning(f"Gift parse failed, stop={data.get('stop_reason')}, text={text[:600]!r}")
        if text.strip():
            fix = {"model": MODEL, "max_tokens": 1200, "messages": [{"role": "user", "content":
                   "Преобразуй варианты подарков из текста ниже в JSON-массив из 3 объектов с ключами "
                   "title, what, where, price, link. Ответь только JSON.\n\n" + text[:6000]}]}
            r = await client.post(API_URL, headers=headers, json=fix)
            if r.status_code == 200:
                ideas = parse_ideas(r.json())
    if not ideas:
        _last_call.pop(telegram_id, None)  # неудача не блокирует повтор
    return ideas


def _text(data: dict) -> str:
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def parse_ideas(data: dict) -> list[dict]:
    """Достаёт JSON-массив из текста ответа. Пропускает сноски вроде [1] и мусор вокруг."""
    text = _text(data)
    raw = None
    dec = json.JSONDecoder()
    for m in re.finditer(r"\[", text):
        try:
            val, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
            raw = val
            break
    if raw is None:
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
