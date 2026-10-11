"""Веб-сервер для Telegram Mini App «Квест дня».

Отдаёт webapp/index.html и API /api/state. Каждый запрос проверяется по initData от Telegram,
поэтому доступ есть только у владельца бота (TELEGRAM_USER_ID).
"""
import hashlib
import hmac
import json
import time
import logging
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

import database
from config import PORT, TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID
from services import gifts, quest

logger = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).parent / "webapp"


def validate_init_data(init_data: str) -> int | None:
    """Проверка подписи initData по документации Telegram. Возвращает user id или None."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        return None
    try:
        return int(json.loads(pairs.get("user", "{}")).get("id"))
    except (ValueError, TypeError):
        return None


async def _auth(request: web.Request) -> int:
    """Подпись Telegram, свежесть (не старше 7 дней) и доступ к боту: владелец или одобренный им."""
    from handlers.access import allowed
    init = request.headers.get("X-Init-Data", "")
    uid = validate_init_data(init)
    try:
        fresh = time.time() - int(dict(parse_qsl(init)).get("auth_date", "0")) < 7 * 86400
    except ValueError:
        fresh = False
    if uid is None or not fresh or not await allowed(uid):
        raise web.HTTPUnauthorized(text="unauthorized")
    return uid


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def get_state(request: web.Request) -> web.Response:
    uid = await _auth(request)
    return web.json_response({**await database.get_quest_state(uid), "_me": uid})


async def post_state(request: web.Request) -> web.Response:
    uid = await _auth(request)
    try:
        incoming = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="bad json")
    server = await database.get_quest_state(uid)
    merged = quest.merge_incoming(server, incoming)
    if isinstance(merged.get("house"), dict):
        from services import house
        house.get(merged, uid)
    await database.save_quest_state(uid, merged)
    return web.json_response({**merged, "_me": uid})


async def post_gifts(request: web.Request) -> web.Response:
    uid = await _auth(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    state = await database.get_quest_state(uid)
    prefs = state.get("prefs")
    if not prefs:
        raise web.HTTPBadRequest(text="no prefs")
    budget = int(body.get("budget") or prefs.get("budget") or 25)
    label = str(body.get("label") or "новая ступень")[:60]
    try:
        ideas = await gifts.suggest_gifts(uid, prefs, budget, label)
    except RuntimeError as e:
        status = 429 if str(e) == "too_often" else 502
        return web.json_response({"ideas": [], "error": str(e)}, status=status)
    return web.json_response({"ideas": ideas})


async def post_cook(request: web.Request) -> web.Response:
    """Что приготовить из того, что есть дома."""
    uid = await _auth(request)
    state = await database.get_quest_state(uid)
    from services import claude, house
    h = house.get(state, uid)
    try:
        r = await claude.client.messages.create(
            model="claude-haiku-4-5", max_tokens=700,
            messages=[{"role": "user", "content": (
                "Вот что есть дома у Андрея (он делает мил-преп на 2–3 дня, ходит в зал, любит сытное):\n"
                + house.summary(h) + "\n\nПредложи 3 блюда, которые можно сделать в основном из этого. "
                "Сначала из того, что скоро испортится. Ответь ТОЛЬКО JSON: [{\"name\": \"блюдо\", \"uses\": \"что берём из дома\", "
                "\"buy\": \"чего не хватает или пусто\", \"time\": \"минут\", \"prep\": true если годится на заготовку}]")}])
        text = "".join(b.text for b in r.content if b.type == "text")
        import json, re
        m = re.search(r"\[.*\]", text, re.S)
        ideas = json.loads(m.group(0)) if m else []
    except Exception as e:
        logger.error(f"cook error: {e}")
        return web.json_response({"ideas": [], "error": "fail"}, status=502)
    return web.json_response({"ideas": ideas[:3]})


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_web() -> web.AppRunner:
    app = web.Application(client_max_size=2 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/state", get_state)
    app.router.add_post("/api/state", post_state)
    app.router.add_post("/api/gifts", post_gifts)
    app.router.add_post("/api/cook", post_cook)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Quest web app on port {PORT}")
    return runner
