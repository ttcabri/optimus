"""Веб-сервер для Telegram Mini App «Квест дня».

Отдаёт webapp/index.html и API /api/state. Каждый запрос проверяется по initData от Telegram,
поэтому доступ есть только у владельца бота (TELEGRAM_USER_ID).
"""
import asyncio
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
BOT = None  # ставит bot.py: чтобы после «Я встал» в приложении прислать утро в чат
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
    init = request.headers.get("X-Init-Data", "") or request.query.get("init", "")
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
    tk = quest.today_key()
    woke_before = bool(((server.get("days") or {}).get(tk) or {}).get("woke"))
    merged = quest.merge_incoming(server, incoming)
    if isinstance(merged.get("house"), dict):
        from services import house
        house.get(merged, uid)
    sday = ((server.get("days") or {}).get(tk) or {})
    mday = (merged.get("days") or {}).get(tk) or {}
    if BOT:  # отметил еду в приложении: спросить, что ел, чтобы списать из Дома
        meals = {it["id"]: it["name"] for it in quest.items(quest.full_profile(merged)) if it.get("meal") == 1}
        fresh = [q for q in mday.get("done") or [] if q in meals and q not in (sday.get("done") or []) and q not in (mday.get("mealAsk") or [])]
        if fresh:
            mday["mealAsk"] = (mday.get("mealAsk") or []) + fresh
            asyncio.create_task(_meal_ask(uid, meals[fresh[0]]))
    if mday.get("woke") and not woke_before and not mday.get("mSent") and BOT:
        mday["mSent"] = True  # «Я встал» в приложении: утреннее сообщение в бот
        asyncio.create_task(_morning(uid))
    await database.save_quest_state(uid, merged)
    return web.json_response({**merged, "_me": uid})


async def _meal_ask(uid: int, name: str) -> None:
    try:
        from handlers.brain import meal_kb
        await asyncio.sleep(1)
        state = await database.get_quest_state(uid)
        await BOT.send_message(uid, f"🍽 {name}: что ел? Спишу из Дома, чтобы продукты считались сами.", reply_markup=meal_kb(state))
    except Exception as e:
        logger.error(f"meal ask error: {e}")


async def _morning(uid: int) -> None:
    try:
        from services import dayflow
        await asyncio.sleep(1)  # дождаться, пока состояние с подъёмом сохранится
        await dayflow.send_morning(BOT, uid)
    except Exception as e:
        logger.error(f"morning after wake error: {e}")


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
    from services import brain, claude, house
    h = house.get(state, uid)
    try:
        r = await claude.client.messages.create(
            model="claude-haiku-4-5", max_tokens=700,
            messages=[{"role": "user", "content": (
                "Вот что есть дома (человек готовит заготовки на 2–3 дня, ходит в зал, любит сытное):\n"
                + house.summary(h) + ("\n\nЕго сохранённые рецепты (если подходят под то, что есть, предложи их первыми): " + "; ".join(x["t"] + (": " + x.get("s", "")[:100]) for x in brain.recipes(state)[:20]) if brain.recipes(state) else "")
                + "\n\nПредложи 3 блюда, которые можно сделать в основном из этого. "
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


async def post_goal(request: web.Request) -> web.Response:
    """Цель одной фразой: {text} -> вопросы; {text, answers, fix?, prev?} -> план."""
    uid = await _auth(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    text = str(body.get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="no text")
    state = await database.get_quest_state(uid)
    from services import goalplan
    try:
        if body.get("answers") is None:
            return web.json_response(await goalplan.questions(state, text))
        return web.json_response({"plan": await goalplan.plan(state, text, body.get("answers") or [], str(body.get("fix") or ""), body.get("prev"))})
    except Exception as e:
        logger.error(f"goal plan error: {e}")
        return web.json_response({"error": "fail"}, status=502)


async def post_brain(request: web.Request) -> web.Response:
    """Мозг из приложения: {text} или форма с файлом. Бот раскладывает так же, как пересланное."""
    uid = await _auth(request)
    from services import brain
    text, file = "", None
    if request.content_type.startswith("multipart/"):
        form = await request.post()
        text = str(form.get("text") or "")[:3000]
        f = form.get("file")
        if f is not None and hasattr(f, "file") and BOT:
            from aiogram.types import BufferedInputFile
            data, name, mime = f.file.read(), (f.filename or "файл")[:80], f.content_type or ""
            try:
                if mime.startswith("image/"):
                    m = await BOT.send_photo(uid, BufferedInputFile(data, name), caption="🧠 В мозг из приложения", disable_notification=True)
                    file = {"id": m.photo[-1].file_id, "kind": "photo", "name": name}
                else:
                    m = await BOT.send_document(uid, BufferedInputFile(data, name), caption="🧠 В мозг из приложения", disable_notification=True)
                    file = {"id": m.document.file_id, "kind": "doc", "name": name, "mime": mime}
            except Exception as e:
                logger.error(f"brain upload: {e}")
                return web.json_response({"error": "upload"}, status=502)
    else:
        try:
            text = str((await request.json()).get("text") or "")[:3000]
        except ValueError:
            pass
    if not text.strip() and not file:
        raise web.HTTPBadRequest(text="empty")
    state = await database.get_quest_state(uid)
    try:
        it = await brain.ingest(state, text=text, file=file, src="app", folder="docs" if file and not text.strip() else None)
    except Exception as e:
        logger.error(f"brain ingest: {e}")
        return web.json_response({"error": "fail"}, status=502)
    offer = it.pop("_offer", "")
    await database.save_quest_state(uid, state)
    return web.json_response({"item": it, "offer": offer})


async def get_brain_file(request: web.Request) -> web.Response:
    """Файл из мозга (фото, документ): лежит в Телеграме, отдаём через бота."""
    uid = await _auth(request)
    from services import brain
    it = brain.find(await database.get_quest_state(uid), request.query.get("id", ""))
    if not it or not it.get("file") or not BOT:
        raise web.HTTPNotFound(text="no file")
    import io
    f = it["file"]
    try:
        tf = await BOT.get_file(f["id"])
        buf = io.BytesIO()
        await BOT.download_file(tf.file_path, destination=buf)
    except Exception as e:
        logger.error(f"brain file: {e}")
        return web.Response(status=502, text="Файл недоступен (Телеграм отдаёт файлы до 20 МБ)")
    ct = "image/jpeg" if f.get("kind") == "photo" else (f.get("mime") or "application/octet-stream")
    return web.Response(body=buf.getvalue(), content_type=ct.split(";")[0], headers={"Cache-Control": "private, max-age=3600"})


_BOT_NAME = None


async def post_invite(request: web.Request) -> web.Response:
    """Ссылка-приглашение: {house: true} = жить в одном доме, иначе просто пользоваться ботом (свой профиль)."""
    uid = await _auth(request)
    global _BOT_NAME
    try:
        body = await request.json()
    except ValueError:
        body = {}
    hid = None
    if body.get("house"):
        import aiosqlite
        from config import DATABASE_PATH
        async with aiosqlite.connect(DATABASE_PATH) as db:
            hid = await database.house_id_of(db, uid)
        hid = hid or f"house_{uid}"
    code = await database.invite_create(uid, hid)
    if not _BOT_NAME and BOT:
        _BOT_NAME = (await BOT.get_me()).username
    return web.json_response({"link": f"https://t.me/{_BOT_NAME or 'BotButter_bot'}?start=inv_{code}"})


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_web() -> web.AppRunner:
    app = web.Application(client_max_size=20 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/state", get_state)
    app.router.add_post("/api/state", post_state)
    app.router.add_post("/api/gifts", post_gifts)
    app.router.add_post("/api/cook", post_cook)
    app.router.add_post("/api/goal", post_goal)
    app.router.add_post("/api/brain", post_brain)
    app.router.add_post("/api/invite", post_invite)
    app.router.add_get("/api/brainfile", get_brain_file)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Quest web app on port {PORT}")
    return runner
