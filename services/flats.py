"""Поиск жилья: каждые 5 минут смотрим новые объявления на Pararius, Funda, Kamernet и Marktplaats
и сразу шлём в Телеграм. Под объявлением кнопка «Ответ хозяину»: готовое письмо на голландском.

Хозяевам бот сам ничего не отправляет: письмо копирует и шлёт человек (так нет банов и спама).
Facebook не читаем: нужен вход под аккаунтом, его быстро блокируют.
Настройки и «уже видел» лежат в отдельной таблице, не в состоянии квеста (приложение их не трогает).
"""
import asyncio
import html
import json
import logging
import re
import time
from urllib.parse import quote

import aiosqlite
import httpx

from config import DATABASE_PATH

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
HEAD = {"User-Agent": UA, "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}

# город → почтовый индекс центра (для Marktplaats, он ищет по индексу и радиусу)
POSTCODES = {"gouda": "2801", "waddinxveen": "2741", "bodegraven": "2411", "alphen aan den rijn": "2405",
             "zoetermeer": "2711", "reeuwijk": "2811", "rotterdam": "3011", "den haag": "2511", "'s-gravenhage": "2511",
             "leiden": "2311", "utrecht": "3511", "woerden": "3441", "nieuwerkerk aan den ijssel": "2911",
             "capelle aan den ijssel": "2901", "rijswijk": "2281", "leidschendam": "2265", "voorburg": "2271",
             "schiedam": "3111", "pijnacker": "2641", "nootdorp": "2631", "wateringen": "2291", "lelystad": "8232", "almere": "1315", "dronten": "8251",
             "zwolle": "8011", "kampen": "8261", "harderwijk": "3841", "amsterdam": "1012", "delft": "2611"}

OLD_AREAS = [["gouda", "waddinxveen", "alphen aan den rijn", "zoetermeer", "bodegraven"],
             ["rijswijk", "den haag", "delft", "zoetermeer", "leidschendam", "leiden", "rotterdam"]]
DEFAULT = {"on": False, "areas": ["gouda", "waddinxveen", "bodegraven", "alphen aan den rijn", "zoetermeer", "woerden", "nieuwerkerk aan den ijssel"],
           "max": 1350, "room_max": 800, "min_m2": 0, "kitchen": True, "about": "", "seen": {}, "items": {}, "stat": {}}

BAD = re.compile(r"gezocht|zoek(?:t|en)?\b|ruil|te koop|koopwoning|garage|parkeer|berging|bedrijfsruimte|kantoor|vakantie", re.I)


# ---------- хранение ----------
async def _db():
    db = await aiosqlite.connect(DATABASE_PATH)
    await db.execute("CREATE TABLE IF NOT EXISTS flats (telegram_id INTEGER PRIMARY KEY, data TEXT)")
    return db


async def get(uid: int) -> dict:
    db = await _db()
    try:
        cur = await db.execute("SELECT data FROM flats WHERE telegram_id = ?", (uid,))
        row = await cur.fetchone()
    finally:
        await db.close()
    try:
        s = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        s = {}
    out = {**json.loads(json.dumps(DEFAULT)), **s}
    if out.get("areas") in OLD_AREAS:  # старые наборы городов по умолчанию → текущий (вокруг Reeuwijk/Гауды)
        out.update(areas=DEFAULT["areas"], max=DEFAULT["max"], seen={})
    return out


async def put(uid: int, s: dict) -> None:
    db = await _db()
    try:
        await db.execute("INSERT INTO flats (telegram_id, data) VALUES (?, ?) ON CONFLICT(telegram_id) DO UPDATE SET data = excluded.data",
                         (uid, json.dumps(s, ensure_ascii=False)))
        await db.commit()
    finally:
        await db.close()


def slug(city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", city.lower().replace("'s-", "s-")).strip("-")


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d.,]*", str(v))
    if not m:
        return None
    t = m.group(0)
    t = t.replace(".", "").replace(",", ".") if re.search(r"\d\.\d{3}", t) or "," in t else t
    try:
        return float(t)
    except ValueError:
        return None


ROOM = re.compile(r"\bkamer\b|\broom\b|onzelfstandig|gedeelde (keuken|badkamer)|shared", re.I)
NO_REG = re.compile(r"(geen|niet|zonder)\s+(mogelijkheid\s+(tot|om)\s+)?(in)?schrijv|inschrijv\w*\s+(is\s+)?niet\s+(mogelijk|toegestaan)|no registration|registration (is )?not (possible|allowed)", re.I)
YES_REG = re.compile(r"inschrijv\w*\s+(is\s+)?(mogelijk|toegestaan)|registration (is )?(possible|allowed)|bsn registration", re.I)


def tag(x: dict) -> None:
    t = f"{x.get('title') or ''} {x.get('text') or ''}"
    x["room"] = bool(ROOM.search(t))
    x["reg"] = "no" if NO_REG.search(t) else "yes" if YES_REG.search(t) else "?"


def _ok(x: dict, s: dict) -> bool:
    tag(x)
    p = x.get("price")
    if x.get("reg") == "no":  # без прописки не берём: BRP по месту жительства обязателен
        return False
    lim = s.get("room_max", 800) if x.get("room") else s["max"]
    if p is not None and (p > lim or p < 250):
        return False
    if s.get("min_m2") and x.get("m2") and x["m2"] < s["min_m2"]:
        return False
    return not BAD.search(x.get("title") or "")


# ---------- источники ----------
async def src_marktplaats(cl: httpx.AsyncClient, s: dict) -> list[dict]:
    out, seen = [], set()
    for city in s["areas"]:
        pc = POSTCODES.get(city.lower())
        if not pc:
            continue
        for q in ("huurwoning", "studio te huur", "appartement te huur"):
            url = (f"https://www.marktplaats.nl/lrp/api/search?query={quote(q)}&postcode={pc}&distanceMeters=10000"
                   f"&priceTo={int(s['max']) * 100}&limit=30&offset=0&sortBy=SORT_INDEX&sortOrder=DECREASING&viewOptions=list-view")
            r = await cl.get(url, headers={**HEAD, "Accept": "application/json"})
            r.raise_for_status()
            for it in r.json().get("listings") or []:
                iid = str(it.get("itemId") or it.get("id") or "")
                if not iid or iid in seen:
                    continue
                seen.add(iid)
                pi = it.get("priceInfo") or {}
                price = (pi.get("priceCents") or 0) / 100 or None
                loc = it.get("location") or {}
                link = it.get("vipUrl") or it.get("selfLink") or ""
                out.append({"src": "Marktplaats", "id": iid, "title": it.get("title") or "", "price": price,
                            "city": loc.get("cityName") or city.title(), "m2": None,
                            "url": link if link.startswith("http") else "https://www.marktplaats.nl" + link,
                            "text": (it.get("description") or "")[:400], "img": ((it.get("pictures") or [{}])[0] or {}).get("mediumUrl")})
    return out


async def src_pararius(cl: httpx.AsyncClient, s: dict) -> list[dict]:
    from bs4 import BeautifulSoup
    out = []
    for city in s["areas"]:
        r = await cl.get(f"https://www.pararius.nl/huurwoningen/{slug(city)}/0-{int(s['max'])}", headers=HEAD)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("section.listing-search-item") or soup.select("[class*=listing-search-item]")
        for c in cards:
            a = c.select_one("a[class*=link--title]") or next((x for x in c.select("a[href]") if "-te-huur/" in x["href"] or "/huurwoning" in x["href"]), None)
            if not a:
                continue
            href = a["href"]
            pr = c.select_one("[class*=__price]")
            sub = c.select_one("[class*=sub-title]")
            area = c.select_one("[class*=surface-area]")
            m2 = _num(area.get_text(" ", strip=True)) if area else None
            if m2 is None:
                mm = re.search(r"(\d+)\s*m²", c.get_text(" ", strip=True))
                m2 = float(mm.group(1)) if mm else None
            out.append({"src": "Pararius", "id": href, "title": a.get_text(" ", strip=True), "price": _num(pr.get_text(" ", strip=True)) if pr else None,
                        "city": (sub.get_text(" ", strip=True) if sub else city.title()), "m2": m2,
                        "url": href if href.startswith("http") else "https://www.pararius.nl" + href, "text": ""})
    return out


def _walk(o, found: list):
    if isinstance(o, dict):
        if "listingId" in o and ("totalRentalPrice" in o or "street" in o or "surfaceArea" in o):
            found.append(o)
        for v in o.values():
            _walk(v, found)
    elif isinstance(o, list):
        for v in o:
            _walk(v, found)


KN_PATTERNS = ["huren/huurwoningen-{c}", "huren/appartement-{c}", "huren/studio-{c}", "en/for-rent/apartments-{c}",
               "en/for-rent/studios-{c}", "en/for-rent/properties-{c}", "huren/kamer-{c}"]
_kn_ok: dict = {}  # город → шаблоны адресов, которые реально отвечают


async def src_kamernet(cl: httpx.AsyncClient, s: dict) -> list[dict]:
    out, seen, tried = [], set(), []
    for city in s["areas"]:
        pats = _kn_ok.get(city) or KN_PATTERNS
        good = []
        for pat in pats:
            url = f"https://kamernet.nl/{pat.format(c=slug(city))}?maxRent={int(s['max'])}"
            r = await cl.get(url, headers=HEAD)
            tried.append(f"{pat.split('-')[0]}:{r.status_code}")
            if r.status_code != 200:
                continue
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
            if not m:
                continue
            found: list = []
            _walk(json.loads(m.group(1)), found)
            if found:
                good.append(pat)
            for x in found:
                lid = str(x.get("listingId"))
                if lid in seen:
                    continue
                seen.add(lid)
                typ = str(x.get("listingType") or x.get("roomType") or "")
                is_room = typ.lower() in ("room", "kamer", "1")
                url2 = x.get("url") or x.get("detailUrl") or x.get("urlKey") or x.get("detailPageUrl") or ""
                if url2 and not url2.startswith("http"):
                    url2 = "https://kamernet.nl" + (url2 if url2.startswith("/") else "/" + url2)
                out.append({"src": "Kamernet", "id": lid, "title": ("Kamer " if is_room else "") + f"{x.get('street') or ''}, {x.get('city') or city.title()}".strip(", "),
                            "price": _num(x.get("totalRentalPrice") or x.get("rent")), "city": x.get("city") or city.title(),
                            "m2": _num(x.get("surfaceArea") or x.get("surface")), "url": url2 or url, "text": ""})
        if good:
            _kn_ok[city] = [p for p in good if "kamer-" not in p] or good
    if not out and not any(_kn_ok.values()):
        raise RuntimeError("не нашёл страницу поиска: " + ", ".join(sorted(set(tried)))[:110])
    return out


def _funda_sync(s: dict) -> list[dict]:
    from funda import Funda
    f = Funda()
    out, errs = [], []
    for city in s["areas"]:
        try:
            res = f.search(slug(city), category="rent", max_price=int(s["max"]), sort="newest")
        except Exception as e:
            logger.warning(f"funda {city}: {e}")
            errs.append(str(e))
            continue
        for l in res[:30]:
            raw = (l.raw or {}).get("_source") or {}
            price = None
            try:
                price = _num((raw.get("price") or {}).get("rent_price", [None])[0])
            except Exception:
                pass
            m2 = None
            try:
                m2 = _num((raw.get("floor_area") or [None])[0])
            except Exception:
                pass
            out.append({"src": "Funda", "id": str(l.listing_id), "title": l.address.title or "", "price": price,
                        "city": l.address.city or city.title(), "m2": m2, "url": l.urls.full or "", "text": l.highlight or ""})
    if errs and len(errs) == len(s["areas"]):
        raise RuntimeError("сайт обрывает соединение с сервером (" + errs[0][:60] + ")")
    return out


async def src_funda(cl: httpx.AsyncClient, s: dict) -> list[dict]:
    return await asyncio.to_thread(_funda_sync, s)


SOURCES = {"Pararius": src_pararius, "Funda": src_funda, "Kamernet": src_kamernet, "Marktplaats": src_marktplaats}


async def fetch_all(s: dict) -> tuple[list[dict], dict]:
    """Все источники параллельно. Ошибка одного не мешает остальным. Возвращает (объявления, статус по источникам)."""
    stat = {}
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as cl:
        async def one(name, fn):
            t = time.time()
            try:
                xs = [x for x in await fn(cl, s) if _ok(x, s)]
                stat[name] = {"ok": True, "n": len(xs), "t": round(time.time() - t, 1)}
                return xs
            except Exception as e:
                stat[name] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:120]}"}
                logger.warning(f"flats {name}: {e}")
                return []
        res = await asyncio.gather(*(one(n, f) for n, f in SOURCES.items()))
    return [x for xs in res for x in xs], stat


def key(x: dict) -> str:
    return f"{x['src'][:2]}:{x['id']}"[-60:]


def card(x: dict) -> str:
    bits = [f"€{x['price']:g}" if x.get("price") else "цена ?", f"{x['m2']:g} м²" if x.get("m2") else "", html.escape(x.get("city") or "")]
    title = x.get("ru_title") or x["title"]
    bits.append({"yes": "✅ прописка можно", "?": "❔ прописка не указана"}.get(x.get("reg"), ""))
    return (("🛏 <b>Комната</b> · запасной вариант\n" if x.get("room") else "") + f"🏠 <b>{html.escape(title[:90] or 'Объявление')}</b>\n" + " · ".join(b for b in bits if b)
            + f"\n<i>{x['src']}" + (f" · {html.escape(x['title'][:60])}" if x.get("ru_title") else "") + "</i>"
            + (f"\n{html.escape((x.get('ru') or x['text'])[:300])}" if x.get("ru") or x.get("text") else ""))


async def translate(xs: list[dict]) -> None:
    """Заголовки и описания на русский одним запросом (быстрая модель). Не вышло: оставляем как есть."""
    if not xs:
        return
    from services import claude
    data = [{"i": i, "t": x.get("title") or "", "d": (x.get("text") or "")[:400]} for i, x in enumerate(xs)]
    try:
        r = await claude.client.messages.create(model=claude.FALLBACK_MODEL, max_tokens=2000, messages=[{"role": "user", "content":
            "Переведи объявления об аренде жилья с голландского на русский. Коротко и понятно, суммы и м² не меняй, "
            "в описании оставь главное (что за жильё, кухня, мебель, с какого числа, условия) в 1-2 предложениях. "
            "Ответ строго JSON-массив [{\"i\": 0, \"t\": \"заголовок\", \"d\": \"описание\"}]:\n" + json.dumps(data, ensure_ascii=False)}])
        t = "".join(b.text for b in r.content if b.type == "text")
        m = re.search(r"\[.*\]", t, re.S)
        for o in json.loads(m.group(0)) if m else []:
            x = xs[int(o["i"])]
            x["ru_title"], x["ru"] = str(o.get("t") or "")[:120], str(o.get("d") or "")[:400]
    except Exception as e:
        logger.warning(f"flats translate: {e}")


def kb(x: dict):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть", url=x["url"]),
                                                  InlineKeyboardButton(text="✍️ Ответ хозяину", callback_data=f"fl:r:{key(x)}"[:64])]])


async def check(uid: int, send) -> int:
    """Одна проверка: новые объявления → send(text, kb). Первая проверка только запоминает, что уже есть."""
    s = await get(uid)
    if not s.get("on"):
        return 0
    xs, stat = await fetch_all(s)
    old = s.get("seen") or {}
    seen = {k: set(v) for k, v in old.items()}
    new = []
    for x in xs:
        k = key(x)
        fresh_src = x["src"] not in old  # источник впервые дал объявления: просто запоминаем, не шлём пачкой
        bucket = seen.setdefault(x["src"], set())
        if k in bucket:
            continue
        bucket.add(k)
        if not fresh_src:
            new.append(x)
    s["seen"] = {k: list(v)[-400:] for k, v in seen.items()}
    s["stat"] = {**stat, "_t": int(time.time())}
    await translate(new[:15])
    items = s.get("items") or {}
    for x in new[:15]:
        items[key(x)] = x
    s["items"] = dict(list(items.items())[-120:])
    await put(uid, s)
    for x in new[:15]:
        try:
            await send(card(x), kb(x))
        except Exception as e:
            logger.error(f"flats send: {e}")
    if len(new) > 15:
        await send(f"…и ещё {len(new) - 15} новых. Сузь поиск: /flats", None)
    return len(new)


def status_text(s: dict) -> str:
    st = s.get("stat") or {}
    lines = [f"🏠 <b>Поиск жилья</b>: {'включён' if s.get('on') else 'выключен'}",
             f"Где: {', '.join(a.title() for a in s['areas'])}", f"Студии и квартиры до €{s['max']:g}, комнаты (запасной вариант) до €{s.get('room_max', 800):g}"
             + (f", от {s['min_m2']} м²" if s.get("min_m2") else ""), "Без прописки (geen inschrijving) не показываю"]
    if st.get("_t"):
        import datetime as _dt
        import pytz
        from config import TIMEZONE
        lines.append(f"\nПоследняя проверка {_dt.datetime.fromtimestamp(st['_t'], pytz.timezone(TIMEZONE)).strftime('%H:%M')}:")
        for n in SOURCES:
            v = st.get(n)
            if v:
                lines.append(f"{'✅' if v.get('ok') else '⚠️'} {n}: " + (f"{v['n']} объявл." if v.get("ok") else html.escape(v.get("err", ""))))
    lines.append("\nКоманды: /flats on · /flats off · /flats test\n/flats gouda, zoetermeer 1200 (города и цена)\n/flats about … (о себе для писем)")
    return "\n".join(lines)


LETTER = """Напиши короткое первое сообщение хозяину жилья в Нидерландах на голландском (B1, вежливо, просто).
Объявление: {ad}
О человеке: {about}
Правила: 5–8 коротких предложений. Без длинных тире. Не выдумывай факты, которых нет в «О человеке». Спроси, свободно ли жильё и когда можно посмотреть.
Если в «О человеке» есть про доход, сбережения или оплату вперёд, упомяни это одной фразой.
Обязательно спроси, можно ли прописаться по этому адресу (inschrijven bij de gemeente), если в объявлении это не сказано.
Ответ строго в двух частях:
NL:
<текст на голландском>
RU:
<перевод на русский>"""


async def letter(x: dict, about: str) -> str:
    from services import claude
    ad = f"{x['src']}: {x.get('title')}, {x.get('city')}, " + (f"€{x['price']:g}" if x.get("price") else "") + (f", {x['m2']:g} m²" if x.get("m2") else "") + (f". {x.get('text')}" if x.get("text") else "")
    r = await claude._create(max_tokens=900, messages=[{"role": "user", "content": LETTER.format(ad=ad[:600], about=about or "Andrey Kovalev, zoekt een studio voor zichzelf.")}])
    return "".join(b.text for b in r.content if b.type == "text").strip()
