"""Короткий поиск в интернете для помощника: часы работы, адреса, цены, факты."""
import logging

import httpx

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)
API_URL = "https://api.anthropic.com/v1/messages"
MODELS = ["claude-sonnet-4-5", "claude-haiku-4-5"]


async def look_up(query: str) -> str:
    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    from services import quest
    n = quest.now()
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    prompt = (f"Сегодня {days[n.weekday()]} {n.strftime('%d.%m.%Y')}, сейчас {n.strftime('%H:%M')}. "
              "Найди в интернете и ответь по-русски, коротко, только факты. "
              "Человек живёт в Дронтене (Нидерланды); если город не указан, ищи там. "
              "Для магазина или сервиса: часы работы ИМЕННО СЕГОДНЯ и адрес. Ищи по-нидерландски "
              "(например «Kruidvat Dronten openingstijden»), в первую очередь официальный сайт сети (страница филиала), "
              "потом Google Maps, openingstijden.nl и подобные. Если источники расходятся, назови самый надёжный "
              "(официальный сайт) и укажи источник. Не отказывайся: дай лучший найденный ответ и пометку, насколько он точный.\n\n"
              "Вопрос: " + query[:300])
    body = {"max_tokens": 800, "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            "messages": [{"role": "user", "content": prompt}]}
    async with httpx.AsyncClient(timeout=90) as client:
        for model in MODELS:
            body["model"] = model
            msgs = list(body["messages"])
            try:
                for _ in range(3):
                    r = await client.post(API_URL, headers=headers, json={**body, "messages": msgs})
                    if r.status_code != 200:
                        logger.warning(f"look_up {model} {r.status_code}: {r.text[:300]}")
                        break
                    data = r.json()
                    if data.get("stop_reason") == "pause_turn":
                        msgs = msgs + [{"role": "assistant", "content": data.get("content", [])}]
                        continue
                    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
                    if text:
                        return text[:1500]
                    break
            except Exception as e:
                logger.error(f"look_up error: {e}")
    return "поиск сейчас не сработал"
