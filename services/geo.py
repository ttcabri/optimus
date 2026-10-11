"""Где он и сколько ехать: геокодинг (Nominatim, OpenStreetMap) и маршрут (OSRM). Без ключей и денег."""
import logging
import math
import time

import httpx

logger = logging.getLogger(__name__)
UA = {"User-Agent": "ButterBOT-quest/1.0 (personal Telegram bot)"}
NOMINATIM = "https://nominatim.openstreetmap.org/search"
ROUTERS = {"car": "https://routing.openstreetmap.de/routed-car/route/v1/driving",
           "bike": "https://routing.openstreetmap.de/routed-bike/route/v1/driving",
           "foot": "https://routing.openstreetmap.de/routed-foot/route/v1/driving"}
PARKING_MIN = {"car": 3, "bike": 1, "foot": 0}
FRESH_SEC = 2 * 3600  # геолокация считается «сейчас» 2 часа


async def geocode(query: str, near: tuple[float, float] | None = None) -> dict | None:
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "nl,de,be", "addressdetails": 0}
    if near:  # ищем сначала рядом (±25 км), чтобы «Kruidvat» был ближайший
        lat, lon = near
        params.update({"viewbox": f"{lon - 0.35},{lat + 0.22},{lon + 0.35},{lat - 0.22}", "bounded": 1})
    try:
        async with httpx.AsyncClient(timeout=15, headers=UA) as c:
            r = await c.get(NOMINATIM, params=params)
            data = r.json() if r.status_code == 200 else []
            if not data and near:
                params.pop("viewbox"), params.pop("bounded")
                r = await c.get(NOMINATIM, params=params)
                data = r.json() if r.status_code == 200 else []
        if data:
            d = data[0]
            return {"lat": float(d["lat"]), "lon": float(d["lon"]), "name": d.get("display_name", query)[:120]}
    except Exception as e:
        logger.error(f"geocode error: {e}")
    return None


async def route(a: tuple[float, float], b: tuple[float, float], mode: str = "car") -> dict | None:
    url = f"{ROUTERS.get(mode, ROUTERS['car'])}/{a[1]},{a[0]};{b[1]},{b[0]}"
    try:
        async with httpx.AsyncClient(timeout=15, headers=UA) as c:
            r = await c.get(url, params={"overview": "false"})
            if r.status_code == 200 and r.json().get("routes"):
                x = r.json()["routes"][0]
                return {"min": max(1, round(x["duration"] / 60)), "km": round(x["distance"] / 1000, 1)}
            logger.warning(f"route {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"route error: {e}")
    # запасной расчёт по прямой, если сервис лёг
    km = _haversine(a, b) * 1.3
    speed = {"car": 40, "bike": 15, "foot": 5}.get(mode, 40)
    return {"min": max(1, round(km / speed * 60)), "km": round(km, 1), "rough": True}


def _haversine(a, b) -> float:
    r = 6371
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def origin(state: dict, frm: str = "") -> tuple[tuple[float, float] | None, str]:
    """Откуда считать: «сейчас» = свежая геолокация, иначе дом."""
    loc, home = state.get("loc") or {}, state.get("home") or {}
    fresh = loc.get("lat") and time.time() - loc.get("t", 0) < FRESH_SEC
    if frm in ("", "here", "сейчас") and fresh:
        return (loc["lat"], loc["lon"]), f"от того места, где ты сейчас (геолокация {int((time.time() - loc['t']) / 60)} мин назад)"
    if home.get("lat"):
        return (home["lat"], home["lon"]), f"от дома ({home.get('address', '')})"
    if fresh:
        return (loc["lat"], loc["lon"]), "от того места, где ты сейчас"
    return None, ""


async def travel(state: dict, to: str, frm: str = "", mode: str = "car") -> str:
    o, label = origin(state, frm)
    if frm and frm not in ("here", "home", "сейчас", "дом"):
        g = await geocode(frm)
        if g:
            o, label = (g["lat"], g["lon"]), f"от {g['name']}"
    elif frm in ("home", "дом"):
        home = state.get("home") or {}
        if home.get("lat"):
            o, label = (home["lat"], home["lon"]), f"от дома ({home.get('address', '')})"
    if not o:
        return "не знаю, откуда считать: нет адреса дома и свежей геолокации. Попроси скинуть геолокацию (скрепка → Геопозиция) или назвать адрес."
    dest = await geocode(to, near=o)
    if not dest:
        return f"не нашёл на карте «{to}». Уточни адрес или город."
    r = await route(o, (dest["lat"], dest["lon"]), mode)
    word = {"car": "на машине", "bike": "на велике", "foot": "пешком"}.get(mode, "на машине")
    total = r["min"] + PARKING_MIN.get(mode, 0)
    return (f"{dest['name']}: {word} ≈ {total} мин ({r['km']} км{', с парковкой' if mode == 'car' else ''}), {label}."
            + (" Расчёт грубый, по прямой." if r.get("rough") else ""))
