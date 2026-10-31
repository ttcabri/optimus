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
             "zwolle": "8011", "kampen": "8261", "harderwijk": "3841", "amsterdam": "1012", "delft": "2611", "dordrecht": "3311"}

OLD_AREAS = [["gouda", "waddinxveen", "alphen aan den rijn", "zoetermeer", "bodegraven"],
             ["rijswijk", "den haag", "delft", "zoetermeer", "leidschendam", "leiden", "rotterdam"]]
DEFAULT = {"on": False, "areas": ["gouda", "waddinxveen", "bodegraven", "alphen aan den rijn", "zoetermeer", "woerden", "nieuwerkerk aan den ijssel", "dordrecht"],
           "max": 1000, "room_max": 800, "fresh_days": 21, "min_m2": 0, "kitchen": True, "about": "", "seen": {}, "items": {}, "stat": {}}

ABOUT0 = ("Андрей Ковалёв, 30 лет, из Украины, в Нидерландах с мая 2022. Последние 2 года работал на производстве паллет в Кампене, "
          "бруто около €3000 в месяц, расчётки есть. Сейчас ищу работу в районе Гауды. Живу один, не курю, без животных, есть машина. "
          "Говорю по-английски. Могу заплатить 3 месяца аренды вперёд. Въехать могу быстро.")

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
    if not out.get("m1000"):  # разово: потолок €1000 (доход 2720 бруто, ×3 правило)
        out.update(max=1000, m1000=True)
    if not out.get("mdord"):  # разово: добавить Dordrecht
        if "dordrecht" not in out["areas"]:
            out["areas"] = out["areas"] + ["dordrecht"]
        out["mdord"] = True
    if not out.get("mclean"):  # разово 27.10: чистая база, остаются только отмеченные (★ и «написал»), дальше только новое
        mark = out.get("mark") or {}
        keep = {k for k, v in mark.items() if v in ("star", "sent", "view")}
        out["items"] = {k: v for k, v in (out.get("items") or {}).items() if k in keep}
        out["mark"] = {k: v for k, v in mark.items() if k in keep}
        out["viewed"] = int(time.time())
        out["mclean"] = True
    if not out.get("mph"):  # разово 31.10: фото старых объявлений Kamernet были с «похожих», перечитываем
        for x in (out.get("items") or {}).values():
            if x.get("src") == "Kamernet":
                for f in ("photos", "det", "big", "facts"):
                    x.pop(f, None)
        out["mph"] = True
    if not out.get("mno"):  # разово 30.10: «Не надо» теперь удаляет; старые скрытые убираем, дубли склеиваем
        for k in [k for k, v in (out.get("mark") or {}).items() if v == "no"]:
            drop(out, k)
        seenfp: dict = {}
        for k, x in sorted((out.get("items") or {}).items(), key=lambda kv: -(kv[1].get("t") or 0)):
            f = fp(x)
            if f and f in seenfp and (out.get("mark") or {}).get(k) not in ("star", "sent", "view"):
                out["items"].pop(k, None)
            elif f:
                seenfp[f] = k
        out["mno"] = True
    if not out.get("mabout") and not out.get("about"):
        from config import TELEGRAM_USER_ID
        if uid == TELEGRAM_USER_ID:
            out["about"] = ABOUT0
        out["mabout"] = True
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


ME_AGE = 30  # возраст владельца для фильтра «до N лет»
STUDENT = re.compile(r"(alleen|uitsluitend|enkel|only|exclusively)\s+(voor\s+|for\s+)?(\w+\s+)?student|students?\s+only|studentenhuis|studentenwoning|studentenkamer|student\s*(housing|house|room|campus)|campus|"
                     r"(must|need to|have to)\s+be\s+(a\s+)?(full[- ]time\s+)?student|ingeschreven\s+(bij|aan)\s+(een\s+)?(hogeschool|universiteit|onderwijs)|studiefinanciering|\bDUO\b|bewijs van inschrijving", re.I)
WOMEN = re.compile(r"(alleen|only|uitsluitend)\s+(voor\s+|for\s+)?(vrouw|dame|meisje|female|wom[ae]n|girl)|female\s+(only|tenant)|vrouwelijke\s+(huurder|student)|(dames|meiden)huis", re.I)
AGE_MAX = re.compile(r"(?:\b1[6-9]|\b2\d)\s*(?:[-–]|t/?m|tot|to|and)\s*(\d{2})\s*(?:jaar|years|yrs|y\.?o\.?|jr)|(?:tot|t/?m|max(?:imaal|imum)?|under|younger than|jonger dan|up to|not older than|niet ouder dan)\s*(?:de\s+leeftijd\s+van\s+|age\s+(?:of\s+)?)?(\d{2})\s*(?:jaar|years|yrs|jr)\b|(?:age|leeftijd)\s*(?:max(?:imaal|imum)?|tot|up to)\s*:?\s*(\d{2})\b|(?:maximum|max)\s+age\s*:?\s*(\d{2})\b|(?:leeftijd|age)\s*:?\s*(?:1[6-9]|2\d)\s*[-–]\s*(\d{2})", re.I)
AGE_MIN = re.compile(r"\b(\d{2})\s*\+|(?:vanaf|minimaal|minimum(?: age)?|ouder dan|older than|at least)\s*(\d{2})\s*(?:jaar|years|jr)|senior|55-plus|65-plus", re.I)
YOUTH = re.compile(r"jongerencontract|jongerenhuisvesting|jongeren(woning|huisvesting|wonen)|youth (contract|housing)|campuscontract", re.I)


def unfit(x: dict) -> str:
    """Причина, почему Андрей не подходит (студенты, возраст, только женщины). Пусто: подходит или не сказано."""
    t = f"{x.get('title') or ''} {x.get('text') or ''} {x.get('full') or ''}"
    if x.get("student"):
        return "только студенты"
    if STUDENT.search(t):
        return "только студенты"
    if WOMEN.search(t):
        return "только женщины"
    if YOUTH.search(t):
        return "молодёжный договор (до 28)"
    for m in AGE_MAX.finditer(t):
        n = next((int(g) for g in m.groups() if g), None)
        if n and 18 <= n < ME_AGE:
            return f"до {n} лет"
    for m in AGE_MIN.finditer(t):
        g = next((g for g in m.groups() if g), None)
        if g is None or (g.isdigit() and int(g) > ME_AGE + 5 and int(g) < 100):
            return "для пожилых" if g is None or int(g) >= 50 else f"от {g} лет"
    return str(x.get("ux") or "")


ROOM = re.compile(r"\bkamer\b|\broom\b|onzelfstandig|gedeelde (keuken|badkamer)|shared", re.I)
NO_REG = re.compile(r"(geen|niet|zonder)\s+(mogelijkheid\s+(tot|om)\s+)?(in)?schrijv|inschrijv\w*\s+(is\s+)?niet\s+(mogelijk|toegestaan)|no registration|registration (is )?not (possible|allowed)", re.I)
YES_REG = re.compile(r"inschrijv\w*\s+(is\s+)?(mogelijk|toegestaan)|registration (is )?(possible|allowed)|bsn registration", re.I)


NL_MON = {"jan": 1, "feb": 2, "mrt": 3, "apr": 4, "mei": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}


def age_days(d) -> int | None:
    """Сколько дней объявлению. ISO, «Vandaag», «Gisteren», «2 aug. 26», «12 okt»."""
    import datetime as _dt
    if not d:
        return None
    t = str(d).strip().lower()
    today = _dt.date.today()
    if t.startswith(("vandaag", "today")):
        return 0
    if t.startswith(("gisteren", "yesterday")):
        return 1
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return (today - _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))).days
    m = re.match(r"(\d{1,2})\s+([a-z]{3})\w*\.?\s*(?:'?(\d{2,4}))?", t)
    if m and m.group(2) in NL_MON:
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            dd = _dt.date(y, NL_MON[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
        if dd > today:
            dd = dd.replace(year=dd.year - 1)
        return (today - dd).days
    return None


def tag(x: dict) -> None:
    t = f"{x.get('title') or ''} {x.get('text') or ''}"
    x["room"] = bool(ROOM.search(t))
    x["reg"] = "no" if NO_REG.search(t) else "yes" if YES_REG.search(t) else "?"


def _ok(x: dict, s: dict) -> bool:
    tag(x)
    x["age"] = age_days(x.get("date"))
    if x["age"] is not None and x["age"] > s.get("fresh_days", 21):  # старые висяки: скорее всего уже сдано
        return False
    p = x.get("price")
    if unfit(x):  # студенты, возраст, только женщины
        return False
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
    jobs = []
    for city in s["areas"]:
        pc = POSTCODES.get(city.lower())
        if pc:
            for q in ("huurwoning", "studio te huur", "appartement te huur"):
                jobs.append((city, f"https://www.marktplaats.nl/lrp/api/search?query={quote(q)}&l1CategoryId=1032&postcode={pc}&distanceMeters=10000"
                                   f"&priceTo={int(s['max']) * 100}&limit=30&offset=0&sortBy=SORT_INDEX&sortOrder=DECREASING&viewOptions=list-view"))
    sem = asyncio.Semaphore(5)

    async def get1(url):
        async with sem:
            r = await cl.get(url, headers={**HEAD, "Accept": "application/json"})
            r.raise_for_status()
            return r.json().get("listings") or []
    res = await asyncio.gather(*(get1(u) for _, u in jobs), return_exceptions=True)
    if res and all(isinstance(r, Exception) for r in res):
        raise res[0]
    for (city, _), lst in zip(jobs, res):
        if isinstance(lst, Exception):
            continue
        for it in lst:
            iid = str(it.get("itemId") or it.get("id") or "")
            if not iid or iid in seen:
                continue
            seen.add(iid)
            loc = it.get("location") or {}
            vert = " ".join(it.get("verticals") or [])
            if loc.get("abroad") or (loc.get("countryName") or "Nederland") != "Nederland" or "business" in vert or (loc.get("distanceMeters") or 0) > 15000:
                continue  # заграница, бизнес-недвижимость
            if age_days(it.get("date")) is None:
                continue  # без понятной даты не берём (там висят объявления годами)
            pi = it.get("priceInfo") or {}
            price = (pi.get("priceCents") or 0) / 100 or None
            link = it.get("vipUrl") or it.get("selfLink") or ""
            out.append({"src": "Marktplaats", "id": iid, "title": it.get("title") or "", "price": price,
                        "city": loc.get("cityName") or city.title(), "m2": None,
                        "url": link if link.startswith("http") else "https://www.marktplaats.nl" + link,
                        "text": (it.get("description") or "")[:400], "img": ((it.get("pictures") or [{}])[0] or {}).get("mediumUrl"),
                        "date": it.get("date") or it.get("sortDate") or ""})
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
NEAR = {"gouda", "reeuwijk", "waddinxveen", "boskoop", "bodegraven", "alphen aan den rijn", "zoetermeer", "woerden", "moordrecht",
        "gouderak", "nieuwerkerk aan den ijssel", "zevenhuizen", "moerkapelle", "capelle aan den ijssel", "rotterdam", "krimpen aan den ijssel",
        "ouderkerk aan den ijssel", "haastrecht", "oudewater", "schoonhoven", "lekkerkerk", "nieuwegein", "utrecht", "leiden", "leiderdorp",
        "den haag", "'s-gravenhage", "delft", "pijnacker", "nootdorp", "berkel en rodenrijs", "bleiswijk", "lansingerland", "dordrecht",
        "zwijndrecht", "papendrecht", "sliedrecht", "alblasserdam", "hendrik-ido-ambacht", "ridderkerk", "barendrecht", "schiedam",
        "rijswijk", "voorburg", "leidschendam", "maassluis", "vlaardingen", "montfoort", "ijsselstein", "harmelen", "nieuwerbrug"}
KN_TYPE = {1: "kamer", 2: "appartement", 4: "studio", 8: "anti-kraak", 16: "huis"}
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
                if not url2 and x.get("citySlug") and x.get("streetSlug"):
                    kt = KN_TYPE.get(int(x.get("listingType") or 0), "appartement")
                    url2 = f"https://kamernet.nl/huren/{kt}-{x['citySlug']}/{x['streetSlug']}/{kt}-{lid}"
                if url2 and not url2.startswith("http"):
                    url2 = "https://kamernet.nl" + (url2 if url2.startswith("/") else "/" + url2)
                kc = str(x.get("city") or "").lower()
                if kc and kc not in NEAR and kc not in s["areas"]:
                    continue  # Kamernet иногда подсовывает всю страну (Гронинген и т.п.)
                out.append({"src": "Kamernet", "id": lid, "title": ("Kamer " if is_room else "") + f"{x.get('street') or ''}, {x.get('city') or city.title()}".strip(", "),
                            "price": _num(x.get("totalRentalPrice") or x.get("rent")), "city": x.get("city") or city.title(),
                            "m2": _num(x.get("surfaceArea") or x.get("surface")), "url": url2 or url, "text": "",
                            "img": x.get("resizedFullPreviewImageUrl") or x.get("thumbnailUrl"),
                            "big": x.get("fullPreviewImageUrl") or x.get("resizedFullPreviewImageUrl"),
                            "student": bool(x.get("isStudentHouseAdvert") or x.get("studentHouseId")),
                            "date": str(x.get("publishDate") or x.get("createdAt") or x.get("activatedAt") or "")})
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
                        "city": l.address.city or city.title(), "m2": m2, "url": l.urls.full or "", "text": l.highlight or "",
                        "date": str(l.publication_date or "")})
    if errs and len(errs) == len(s["areas"]):
        raise RuntimeError("сайт обрывает соединение с сервером (" + errs[0][:60] + ")")
    return out


async def src_funda(cl: httpx.AsyncClient, s: dict) -> list[dict]:
    return await asyncio.to_thread(_funda_sync, s)


# ---------- Pararius и Funda через их же письма-оповещения (сайты режут серверы, письма не режут) ----------
MAIL_EXTRACT = """Это письмо-оповещение о новых объявлениях аренды с сайта {site}. Вытащи из него ВСЕ объявления.
Для каждого: title (адрес или заголовок как в письме), city, price (число € в месяц или null), m2 (число или null),
url (ссылка именно на это объявление из текста, как есть), text (что ещё сказано: тип, комнаты, мебель, 1 строка).
Рекламу, «похожие поиски», ссылки отписки и настройки не бери.
img: ссылка на фото этого объявления из [img …] рядом с ним, если есть, иначе "".
Ответ строго JSON-массив: [{{"title": "", "city": "", "price": null, "m2": null, "url": "", "text": "", "img": ""}}]

Письмо:
{body}"""


def _mail_fetch_sync(user: str, pw: str) -> list[tuple[str, str]]:
    """Непрочитанные письма от Pararius/Funda: [(сайт, текст со ссылками)]. Прочитанные помечаются."""
    import email
    import imaplib
    from email.header import decode_header
    from bs4 import BeautifulSoup
    out = []
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        M.login(user, pw)
        M.select("INBOX")
        typ, data = M.search(None, "UNSEEN")
        for num in (data[0].split() if data and data[0] else [])[-20:]:
            typ, msg = M.fetch(num, "(RFC822)")
            m = email.message_from_bytes(msg[0][1])
            frm = str(m.get("From") or "").lower()
            subj = "".join(t.decode(c or "utf-8", "ignore") if isinstance(t, bytes) else t for t, c in decode_header(m.get("Subject") or ""))
            site = "Pararius" if "pararius" in frm else "Funda" if "funda" in frm else None
            if not site:
                continue
            html_part = text_part = ""
            for part in m.walk():
                ct = part.get_content_type()
                if ct in ("text/html", "text/plain"):
                    try:
                        payload = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                    except Exception:
                        continue
                    if ct == "text/html":
                        html_part += payload
                    else:
                        text_part += payload
            if html_part:
                soup = BeautifulSoup(html_part, "html.parser")
                for im in soup.find_all("img", src=True):
                    if str(im["src"]).startswith("http") and not re.search(r"logo|icon|pixel|track|spacer", im["src"], re.I):
                        im.replace_with(f" [img {im['src']}] ")
                for a in soup.find_all("a", href=True):
                    a.replace_with(f"{a.get_text(' ', strip=True)} [{a['href']}]")
                body = soup.get_text("\n", strip=True)
            else:
                body = text_part
            out.append((site, f"Тема: {subj}\n{body}"[:12000]))
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out


async def src_mail(cl: httpx.AsyncClient, s: dict) -> list[dict]:
    import os
    user, pw = os.getenv("FLATS_MAIL"), os.getenv("FLATS_MAIL_PASS")
    if not user or not pw:
        raise RuntimeError("почта не подключена")
    mails = await asyncio.to_thread(_mail_fetch_sync, user, pw.replace(" ", ""))
    from services import claude
    out = []
    for site, body in mails:
        try:
            r = await claude.client.messages.create(model=claude.FALLBACK_MODEL, max_tokens=3000,
                                                    messages=[{"role": "user", "content": MAIL_EXTRACT.format(site=site, body=body)}])
            t = "".join(b.text for b in r.content if b.type == "text")
            m = re.search(r"\[.*\]", t, re.S)
            for x in json.loads(m.group(0)) if m else []:
                title = str(x.get("title") or "")[:120]
                price = _num(x.get("price"))
                out.append({"src": site, "id": re.sub(r"\W+", "", f"{title}{price}".lower())[:50], "title": title, "price": price,
                            "city": str(x.get("city") or ""), "m2": _num(x.get("m2")), "url": str(x.get("url") or ""),
                            "text": str(x.get("text") or "")[:300], "date": "vandaag", "push": True,
                            "img": str(x.get("img") or "") if str(x.get("img") or "").startswith("http") else None})
        except Exception as e:
            logger.warning(f"flats mail parse: {e}")
    return out


# Pararius и Funda напрямую режут серверы (403 / обрыв), поэтому идут через почту
SOURCES = {"Почта (Pararius+Funda)": src_mail, "Kamernet": src_kamernet}  # Marktplaats убран 27.10: почти нет квартир, одни висяки


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


_FP_DROP = re.compile(r"\b(kamer|room|studio|appartement|apartment|flat|huis|house|woning|bovenwoning|benedenwoning|te huur|for rent|huren|"
                      r"комната|студия|квартира|дом|in|aan|de|het|van|op|te|the|a)\b", re.I)


def fp(x: dict) -> str:
    """Отпечаток квартиры для склейки дублей: улица/название без типа жилья + город + цена (до €10)."""
    t = f"{x.get('title') or ''}".split(",")[0].lower()
    for c0 in [str(x.get("city") or "").lower()] + list(POSTCODES):
        if c0 and len(c0) > 3:
            t = t.replace(c0, " ")
    t = _FP_DROP.sub(" ", t)
    t = re.sub(r"[^a-zа-я0-9]+", "", t)[:24]
    c = re.sub(r"[^a-z]+", "", str(x.get("city") or "").lower())[:12]
    p = int(round((x.get("price") or 0) / 10))
    return f"{t}|{c}|{p}" if t else ""


def key(x: dict) -> str:
    return f"{x['src'][:2]}:{x['id']}"[-60:]


ONE = re.compile(r"\b(?:1|één|een|max(?:imaal)?\.?\s*1)\s*pers(?:oon|\.|\b)|geen\s+(?:stel|koppel)|alleenstaande|single\s+(?:person|occupancy)|no\s+couples|1\s+person\b|één bewoner", re.I)
TWO = re.compile(r"\b(?:2|twee|max(?:imaal)?\.?\s*2)\s*pers(?:onen|\.|\b)|\b(?:stel|koppel|stellen|koppels)\b|couples?\b|2\s+persons|twee bewoners", re.I)


def pair(x: dict) -> str:
    """Можно ли вдвоём: 'yes' / 'no' / '' (не сказано). Сначала по тексту, иначе как поняла модель."""
    t = f"{x.get('title') or ''} {x.get('text') or ''}"
    if ONE.search(t):
        return "no"
    if TWO.search(t):
        return "yes"
    return {"2": "yes", "1": "no"}.get(str(x.get("pp") or ""), "")


def card(x: dict) -> str:
    bits = [f"€{x['price']:g}" if x.get("price") else "цена ?", f"{x['m2']:g} м²" if x.get("m2") else "", html.escape(x.get("city") or "")]
    title = x.get("ru_title") or x["title"]
    bits.append({"yes": "✅ прописка можно", "?": "❔ прописка не указана"}.get(x.get("reg"), ""))
    bits.append({"yes": "👫 можно вдвоём", "no": "⚠️ только один"}.get(pair(x), ""))
    a = x.get("age")
    bits.append("" if a is None else "🆕 сегодня" if a == 0 else "🕒 вчера" if a == 1 else f"🕒 {a} дн. назад")
    return (("🛏 <b>Комната</b> · запасной вариант\n" if x.get("room") else "") + f"🏠 <b>{html.escape(title[:90] or 'Объявление')}</b>\n" + " · ".join(b for b in bits if b)
            + f"\n<i>{x['src']}" + (f" · {html.escape(x['title'][:60])}" if x.get("ru_title") else "") + "</i>"
            + (f"\n{html.escape((x.get('ru') or x['text'])[:300])}" if x.get("ru") or x.get("text") else ""))


async def translate(xs: list[dict]) -> None:
    """Заголовки и описания на русский одним запросом (быстрая модель). Не вышло: оставляем как есть."""
    if not xs:
        return
    from services import claude
    data = [{"i": i, "t": x.get("title") or "", "d": ((x.get("text") or "") + " " + (x.get("full") or ""))[:1500]} for i, x in enumerate(xs)]
    try:
        r = await claude.client.messages.create(model=claude.FALLBACK_MODEL, max_tokens=2000, messages=[{"role": "user", "content":
            "Переведи объявления об аренде жилья с голландского на русский. Коротко и понятно, суммы и м² не меняй, "
            "в описании оставь главное (что за жильё, кухня, мебель, с какого числа, условия) в 1-2 предложениях. "
            "Поле p: \"1\" если сказано, что жильё только для одного человека (1 persoon, geen stellen), "
            "\"2\" если прямо можно вдвоём или паре (2 personen, stel), иначе \"\". Не угадывай. "
            "Поле x: жилец мужчина 30 лет, работает, не студент. Если в тексте прямо сказано, что он не подходит "
            "(только студенты, возраст до 29 или старше 35, только женщины, только для пар/семей, только для экспатов компании X), "
            "напиши причину по-русски в 2-4 слова, иначе \"\". Не угадывай. "
            "Ответ строго JSON-массив [{\"i\": 0, \"t\": \"заголовок\", \"d\": \"описание\", \"p\": \"\", \"x\": \"\"}]:\n" + json.dumps(data, ensure_ascii=False)}])
        t = "".join(b.text for b in r.content if b.type == "text")
        m = re.search(r"\[.*\]", t, re.S)
        for o in json.loads(m.group(0)) if m else []:
            x = xs[int(o["i"])]
            x["ru_title"], x["ru"] = str(o.get("t") or "")[:120], str(o.get("d") or "")[:400]
            x["pp"] = str(o.get("p") or "")
            x["ux"] = str(o.get("x") or "")[:40]
    except Exception as e:
        logger.warning(f"flats translate: {e}")


def app_url(extra: str = "flats=1") -> str:
    from config import WEBAPP_URL
    return WEBAPP_URL + ("&" if "?" in WEBAPP_URL else "?") + extra if WEBAPP_URL and WEBAPP_URL.startswith("https://") else ""


def kb(x: dict):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    k = key(x)
    row = [InlineKeyboardButton(text="🔗 Открыть", url=x["url"])] if str(x.get("url") or "").startswith("http") else []
    row2 = [InlineKeyboardButton(text="👎 Не надо", callback_data=f"fl:n:{k}"[:64])]
    if app_url():
        row2.append(InlineKeyboardButton(text="🗂 Все объявления", web_app=WebAppInfo(url=app_url())))
    return InlineKeyboardMarkup(inline_keyboard=[row + [InlineKeyboardButton(text="✍️ Ответ хозяину", callback_data=f"fl:r:{k}"[:64])], row2])


def app_kb(text: str = "🗂 Открыть «Аренду»"):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=app_url()))]]) if app_url() else None


ITEMS_MAX = 400


def keep_items(s: dict) -> None:
    """Храним последние ITEMS_MAX объявлений; отмеченные (★, написал) не выкидываем."""
    items, mark = s.get("items") or {}, s.get("mark") or {}
    if len(items) <= ITEMS_MAX:
        return
    order = sorted(items, key=lambda k: items[k].get("t") or 0)
    drop = [k for k in order if mark.get(k) not in ("star", "sent", "view")][:len(items) - ITEMS_MAX]
    for k in drop:
        items.pop(k, None)
        mark.pop(k, None)


_KN_KEYS = re.compile(r"descr|age|student|tenant|gender|prefer|suitab|candidate|occupa|requirement|condition|household", re.I)


async def kn_detail(cl: httpx.AsyncClient, x: dict) -> None:
    """Страница объявления Kamernet: описание и пожелания к жильцу (возраст, пол, статус) в x['full']."""
    try:
        r = await cl.get(x["url"], headers=HEAD)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if r.status_code != 200 or not m:
            return
        data = json.loads(m.group(1))
        lid = str(x.get("id"))
        cands: list = []

        def find(o, depth=0):
            if depth > 14:
                return
            if isinstance(o, dict):
                if str(o.get("listingId") or o.get("id") or "") == lid and len(o) > 8:
                    cands.append(o)
                for v in o.values():
                    find(v, depth + 1)
            elif isinstance(o, list):
                for v in o[:80]:
                    find(v, depth + 1)
        find(data)
        if not cands:
            if not globals().get("_kn_dbg"):
                globals()["_kn_dbg"] = 1
                pp = (data.get("props") or {}).get("pageProps") or {}
                logger.warning(f"KNDETAIL no listing obj; pageProps keys={list(pp)[:40]}")
            return
        if not globals().get("_kn_ph_dbg"):
            globals()["_kn_ph_dbg"] = 1
            paths: list = []

            def wp(o, pth):
                if len(paths) > 25:
                    return
                if isinstance(o, dict):
                    for k2, v2 in o.items():
                        wp(v2, pth + "." + str(k2))
                elif isinstance(o, list):
                    for i2, v2 in enumerate(o[:30]):
                        wp(v2, pth + f"[{i2}]")
                elif isinstance(o, str) and "kamernet.nl/image/" in o:
                    paths.append(pth[-90:])
            wp(data, "")
            html_u = set(re.findall(r"resources\.kamernet\.nl/image/([0-9a-f-]{36})", re.sub(r'<script id="__NEXT_DATA__".*?</script>', "", r.text, flags=re.S)))
            apis = sorted(set(re.findall(r'["\'](/?(?:[a-z0-9_.-]+/)*(?:api|services)/[A-Za-z0-9_/{}.-]{3,80})["\']', r.text)))[:25]
            js = sorted(set(re.findall(r'src="(/_next/static/[^"]+\.js)"', r.text)))[:40]
            logger.warning("KNPHPATH lid=" + lid + f" html_imgs={len(html_u)} apis={apis} js={[j for j in js if 'listing' in j.lower() or 'detail' in j.lower() or 'huren' in j.lower()]}")
        def strip(o):  # без «похожих объявлений»: иначе их фото и данные лезут в это
            if isinstance(o, dict):
                return {k: strip(v) for k, v in o.items() if not re.search(r"suggest|similar|recommend|related|other|nearby|more", k, re.I)}
            if isinstance(o, list):
                return [strip(v) for v in o]
            return o
        cands = [strip(c) for c in cands]
        own = [c for c in cands if str(c.get("listingId") or "") == lid] or cands
        det = max(own, key=lambda d: len(json.dumps(d, ensure_ascii=False)))
        bits: list = []

        def flat(o, pre="", depth=0):
            if depth > 4 or len(bits) > 80:
                return
            if isinstance(o, dict):
                for k, v in o.items():
                    if re.search(r"image|photo|url|slug|thumbnail|lat|lng|longitude|latitude|^id$|Id$", k, re.I):
                        continue
                    flat(v, f"{pre}{k}.", depth + 1)
            elif isinstance(o, list):
                if o and all(not isinstance(v, (dict, list)) for v in o):
                    bits.append(f"{pre[:-1]}: {', '.join(map(str, o))[:200]}")
                else:
                    for v in o[:10]:
                        flat(v, pre, depth + 1)
            elif o not in (None, "", False):
                bits.append(f"{pre[:-1]}: {str(o)[:1200]}")
        flat(det)
        # полезные поля прямо из объявления
        if det.get("publishDate"):
            x["date"] = str(det["publishDate"])[:10]
            x["age"] = age_days(x["date"])
        n = det.get("suitableForNumberOfPersons")
        if isinstance(n, int) and n in (1, 2):
            x["pp"] = str(n)
        for b in bits:
            k, _, v = b.partition(": ")
            kl = k.lower()
            if "age" in kl and re.search(r"max|to$|until", kl) and v.strip().isdigit() and 18 <= int(v) < ME_AGE:
                x["ux"] = f"до {int(v)} лет"
            elif "age" in kl and re.search(r"min|from", kl) and v.strip().isdigit() and int(v) > ME_AGE + 5:
                x["ux"] = f"от {int(v)} лет"
            elif "gender" in kl and re.fullmatch(r"\s*(female|vrouw|women|woman|2)\s*", v, re.I) and "landlord" not in kl:
                x["ux"] = "только женщины"
        extra = [f"Залог €{det['deposit']}" if det.get("deposit") else "", f"с {str(det['availabilityStartDate'])[:10]}" if det.get("availabilityStartDate") else ""]
        # фото самого объявления в данных страницы нет (там только «похожие»): берём из HTML всё, кроме фото «похожих»
        UU = r"resources\.kamernet\.nl/image/([0-9a-f-]{36})"
        other = set(re.findall(UU, json.dumps(data.get("props", {}).get("pageProps", {}).get("targetPageProps", {}).get("listingDetails", {}).get("suggestedListingResult") or [])))
        raw = re.sub(r'<script id="__NEXT_DATA__".*?</script>', "", r.text, flags=re.S) + json.dumps(det)
        ph, seen_u = [], set(other)
        for u in re.findall(UU, raw):
            if u not in seen_u:
                seen_u.add(u)
                ph.append(f"https://resources.kamernet.nl/image/{u}")
        if not ph and (x.get("big") or x.get("img")):
            ph = [x.get("big") or x.get("img")]  # своё главное фото из поиска (остальные сайт подгружает скриптом)
        if ph:
            x["photos"] = ph[:20]
            x["big"] = x.get("big") or ph[0]
            x["img"] = x.get("img") or ph[0]
        def d10(v):
            v = str(v or "")[:10]
            return f"{v[8:10]}.{v[5:7]}.{v[:4]}" if re.match(r"\d{4}-\d\d-\d\d", v) else ""
        fx = {"Залог": f"€{det['deposit']:g}" if isinstance(det.get("deposit"), (int, float)) and det["deposit"] else "",
              "Свободно с": d10(det.get("availabilityStartDate")),
              "До": d10(det.get("availabilityEndDate")) or "бессрочно" if det.get("availabilityStartDate") else "",
              "Коммуналка": "включена" if det.get("utilitiesIncluded") is True else "не включена" if det.get("utilitiesIncluded") is False else "",
              "Человек": str(det.get("suitableForNumberOfPersons") or ""),
              "Хозяин": str(det.get("landlordDisplayName") or "")[:30],
              "Отвечает": str(det.get("responseRateText") or ""),
              "На сайте с": d10(det.get("publishDate")),
              "Индекс": str(det.get("postalCode") or "")}
        x["facts"] = {k: v for k, v in fx.items() if v}
        x["street"] = str(det.get("computedStreetName") or det.get("street") or "")
        x["text"] = (" · ".join(e for e in extra if e) + ". " + str(det.get("englishDescription") or det.get("dutchDescription") or det.get("description") or "")).strip(". ")[:1500]
        x["full"] = " | ".join(str(b) for b in bits)[:2500]
        if not globals().get("_kn_dbg"):
            globals()["_kn_dbg"] = 1
            logger.warning("KNDETAIL " + re.sub(r"\s+", " ", x["full"])[-1500:])
    except Exception as e:
        logger.warning(f"kamernet detail: {e}")


async def screen(new: list) -> list:
    """Новые объявления: детали Kamernet, перевод, отсев по условиям (студенты, возраст, только женщины). Возвращает подходящие."""
    kn = [x for x in new if x["src"] == "Kamernet" and x.get("url")][:40]
    if kn:
        sem = asyncio.Semaphore(4)
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cl:
            async def one(x):
                async with sem:
                    await kn_detail(cl, x)
            await asyncio.gather(*(one(x) for x in kn))
    new = [x for x in new if not unfit(x)]
    for i in range(0, min(len(new), 60), 15):
        await translate(new[i:i + 15])
    out = []
    for x in new:
        why = unfit(x)
        if why:
            logger.info(f"flats skip ({why}): {x.get('title')}")
        else:
            x.pop("full", None)
            if x["src"] == "Kamernet":
                x["det"] = 1
            out.append(x)
    return out


GONE_TXT = re.compile(r"verhuurd|niet meer beschikbaar|no longer available|is rented|rented out|onder optie|deze advertentie is (niet )?(meer )?(actief|offline)|advertentie verlopen", re.I)


async def alive(cl: httpx.AsyncClient, x: dict) -> bool | None:
    """Объявление ещё висит? True / False (снято) / None (не удалось понять: сайт не пустил)."""
    url = str(x.get("url") or "")
    if not url.startswith("http"):
        return None
    try:
        r = await cl.get(url, headers=HEAD)
    except Exception:
        return None
    if r.status_code in (404, 410):
        return False
    if r.status_code != 200:
        return None
    if x.get("src") == "Kamernet":
        lid = str(x.get("id"))
        if lid not in str(r.url):
            return False  # перекинуло на поиск: объявления нет
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if m:
            found: list = []

            def find(o, d=0):
                if d > 14:
                    return
                if isinstance(o, dict):
                    if str(o.get("listingId") or "") == lid and "isActive" in o:
                        found.append(o)
                    for v in o.values():
                        find(v, d + 1)
                elif isinstance(o, list):
                    for v in o[:80]:
                        find(v, d + 1)
            find(json.loads(m.group(1)))
            if found:
                return bool(found[0].get("isActive"))
        return None
    return False if GONE_TXT.search(r.text[:200000]) else True


async def sweep(uid: int) -> list[str]:
    """Утро: проверяем, какие объявления из «Аренды» сняли с сайтов. Снятые удаляем (★ тоже),
    «написал» оставляем с пометкой «снято». Возвращает названия снятых из ★ и «написал»."""
    s = await get(uid)
    items, mark = s.get("items") or {}, s.get("mark") or {}
    todo = [k for k, x in items.items() if not x.get("gone")][:150]
    res: dict = {}
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cl:
        async def one(k):
            async with sem:
                res[k] = await alive(cl, items[k])
        await asyncio.gather(*(one(k) for k in todo))
    s = await get(uid)  # свежая версия: за время проверки могли поставить отметки
    items, mark = s.get("items") or {}, s.get("mark") or {}
    lost = []
    for k, ok in res.items():
        if ok is not False or k not in items:
            continue
        x = items[k]
        if mark.get(k) in ("star", "sent", "view"):
            lost.append({"star": "★ ", "sent": "✉️ ", "view": "📅 "}[mark[k]] + str(x.get("ru_title") or x.get("title") or "")[:60])
        if mark.get(k) in ("sent", "view"):
            x["gone"] = 1
        else:
            items.pop(k, None)
            mark.pop(k, None)
    s["items"], s["mark"] = items, mark
    s["sweep"] = {"t": int(time.time()), "checked": len(res), "gone": sum(1 for v in res.values() if v is False),
                  "unknown": sum(1 for v in res.values() if v is None)}
    await put(uid, s)
    logger.info(f"flats sweep: {s['sweep']}")
    return lost


SEND_NOW = 10  # квартиры и студии сразу в чат, не больше стольких за проверку


async def check(uid: int, send) -> int:
    """Одна проверка. Всё новое сохраняется в «Аренду» (приложение). В чат сразу: только студии и квартиры.
    Комнаты копятся тихо, о них вечерняя сводка. Первая проверка источника только запоминает, что уже есть (без пачки в чат)."""
    s = await get(uid)
    if not s.get("on"):
        return 0
    xs, stat = await fetch_all(s)
    if not globals().get("_kn_dbg"):  # разовая самопроверка после запуска: что видно на странице объявления Kamernet
        probe = next((dict(x) for x in xs if x["src"] == "Kamernet" and x.get("url")), None)
        if probe:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cl:
                await kn_detail(cl, probe)
            logger.warning(f"KNPROBE {probe.get('url')} unfit={unfit(probe)!r} photos={len(probe.get('photos') or [])} facts={probe.get('facts')}")
    old = s.get("seen") or {}
    seen = {k: set(v) for k, v in old.items()}
    fps = set(s.get("fp") or []) | {fp(x) for x in (s.get("items") or {}).values()}
    new = []
    now = int(time.time())
    for x in xs:
        k = key(x)
        fresh_src = x["src"] not in old
        bucket = seen.setdefault(x["src"], set())
        if k in bucket:
            continue
        bucket.add(k)
        f = fp(x)
        if f and f in fps:
            continue  # та же квартира уже была (с другого сайта или из другого письма)
        if f:
            fps.add(f)
        x["t"] = now
        if not fresh_src or x.get("push"):
            new.append(x)
    new = await screen(new)
    # сохраняем поверх свежей версии: пока шла проверка, могли нажать «Не надо», ★ и т.п.
    s2 = await get(uid)
    s2["seen"] = {k: list(v)[-1500:] for k, v in seen.items()}
    s2["fp"] = list(fps)[-3000:]
    s2["stat"] = {**stat, "_t": now}
    items = s2.get("items") or {}
    gone = set(s2.get("no") or [])
    new = [x for x in new if key(x) not in gone]
    for x in new:
        items[key(x)] = x
    s2["items"] = items
    keep_items(s2)
    await put(uid, s2)
    flats_ = [x for x in new if not x.get("room")]
    for x in flats_[:SEND_NOW]:
        try:
            await send(card(x), kb(x))
        except Exception as e:
            logger.error(f"flats send: {e}")
    if len(flats_) > SEND_NOW:
        await send(f"…и ещё {len(flats_) - SEND_NOW} студий и квартир. Все в «Аренде».", app_kb())
    return len(new)


def day_summary(s: dict) -> str | None:
    """Вечерняя сводка за сутки. None, если сказать нечего."""
    items, mark = s.get("items") or {}, s.get("mark") or {}
    day = int(time.time()) - 86400
    fresh = [x for x in items.values() if (x.get("t") or 0) > day and not x.get("seed")]
    if not fresh:
        return None
    rooms = sum(1 for x in fresh if x.get("room"))
    srcs: dict = {}
    for x in fresh:
        srcs[x["src"]] = srcs.get(x["src"], 0) + 1
    sent = sum(1 for v in mark.values() if v == "sent")
    star = sum(1 for v in mark.values() if v == "star")
    return (f"🏠 <b>Жильё за день</b>\nНовых: {len(fresh) - rooms} студий и квартир, {rooms} комнат\n"
            + (f"Написал: {sent}" if sent else "Пока никому не написал") + (f" · ★ {star}" if star else "")
            + "\n<i>" + " · ".join(f"{k}: {v}" for k, v in srcs.items()) + "</i>")


async def ensure_ru(s: dict, limit: int = 20) -> bool:
    """Догоняем перевод для объявлений без него (те, что пришли тихо). True, если что-то перевели."""
    xs = [x for x in sorted((s.get("items") or {}).values(), key=lambda x: -(x.get("t") or 0)) if not x.get("ru_title") and not x.get("_tr")][:limit]
    for x in xs:
        x["_tr"] = 1
    for i in range(0, len(xs), 10):
        await translate(xs[i:i + 10])
    return bool(xs)


async def detail(s: dict, k: str) -> bool:
    """Подробности по объявлению по запросу из приложения (фото, факты). True, если что-то обновили."""
    x = (s.get("items") or {}).get(k)
    if not x or x.get("det") or x.get("src") != "Kamernet":
        return False
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cl:
        await kn_detail(cl, x)
    x.pop("full", None)
    x["det"] = 1
    return True


def drop(s: dict, k: str) -> None:
    """«Не надо»: убрать из «Аренды» насовсем (и больше не присылать). Держим в корзине для «Вернуть»."""
    x = (s.get("items") or {}).pop(k, None)
    (s.get("mark") or {}).pop(k, None)
    no = s.setdefault("no", [])
    if k not in no:
        no.append(k)
    s["no"] = no[-2000:]
    if x:
        if fp(x):
            s.setdefault("fp", []).append(fp(x))
        tr = s.setdefault("trash", {})
        tr[k] = x
        s["trash"] = dict(list(tr.items())[-60:])


def undrop(s: dict, k: str) -> bool:
    x = (s.get("trash") or {}).pop(k, None)
    s["no"] = [n for n in s.get("no") or [] if n != k]
    if x:
        s.setdefault("items", {})[k] = x
    return bool(x)


def public(s: dict) -> dict:
    """Что отдаём приложению."""
    mark = s.get("mark") or {}
    out = []
    for k, x in (s.get("items") or {}).items():
        out.append({"k": k, "src": x.get("src"), "title": x.get("ru_title") or x.get("title"), "orig": x.get("title") if x.get("ru_title") else "",
                    "price": x.get("price"), "m2": x.get("m2"), "city": x.get("city"), "url": x.get("url"), "img": x.get("img"), "big": x.get("big") or x.get("img"),
                    "text": x.get("ru") or x.get("text") or "", "room": bool(x.get("room")), "reg": x.get("reg"), "pair": pair(x),
                    "age": x.get("age"), "t": x.get("t") or 0, "seed": bool(x.get("seed")), "m": mark.get(k, ""), "gone": bool(x.get("gone")),
                    "photos": x.get("photos") or ([x["big"]] if x.get("big") else []), "facts": x.get("facts") or {},
                    "street": x.get("street") or "", "note": (s.get("note") or {}).get(k, ""), "det": bool(x.get("det"))})
    out.sort(key=lambda o: -o["t"])
    return {"on": bool(s.get("on")), "max": s.get("max"), "room_max": s.get("room_max", 800), "areas": s.get("areas"),
            "viewed": s.get("viewed") or 0, "check": (s.get("stat") or {}).get("_t") or 0, "items": out}


def status_text(s: dict) -> str:
    st = s.get("stat") or {}
    lines = [f"🏠 <b>Поиск жилья</b>: {'включён' if s.get('on') else 'выключен'}",
             f"Где: {', '.join(a.title() for a in s['areas'])}", f"Студии и квартиры до €{s['max']:g}, комнаты (запасной вариант) до €{s.get('room_max', 800):g}"
             + (f", от {s['min_m2']} м²" if s.get("min_m2") else ""), "Без прописки (geen inschrijving) не показываю", f"Старше {s.get('fresh_days', 21)} дней не показываю"]
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


async def diag(uid: int) -> None:
    """Отладка в логи: что приходит с источников и почему отсеивается. Метка FLDIAG."""
    s = await get(uid)
    L = lambda m: logger.warning("FLDIAG " + m)
    L(f"areas={s['areas']} max={s['max']} room_max={s.get('room_max')} fresh={s.get('fresh_days')}")
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as cl:
        pc = POSTCODES.get("gouda")
        for cat in ("&l1CategoryId=1032", ""):
            url = (f"https://www.marktplaats.nl/lrp/api/search?query=studio%20te%20huur{cat}&postcode={pc}&distanceMeters=10000"
                   f"&priceTo={int(s['max']) * 100}&limit=30&offset=0&sortBy=SORT_INDEX&sortOrder=DECREASING&viewOptions=list-view")
            try:
                r = await cl.get(url, headers={**HEAD, "Accept": "application/json"})
                lst = r.json().get("listings") or []
                L(f"MP raw cat={bool(cat)} status={r.status_code} n={len(lst)}")
                for it in lst[:12]:
                    loc = it.get("location") or {}
                    L(f"MP  {it.get('date')!r} cat={it.get('categoryId')} d={loc.get('distanceMeters')} {loc.get('cityName')} "
                      f"v={it.get('verticals')} €{((it.get('priceInfo') or {}).get('priceCents') or 0)/100:g} {str(it.get('title'))[:60]}")
            except Exception as e:
                L(f"MP err {e}")
        for name, fn in SOURCES.items():
            try:
                xs = await fn(cl, s)
            except Exception as e:
                L(f"{name} ERR {e}")
                continue
            kept, why = [], {}
            for x in xs:
                if _ok(x, s):
                    kept.append(x)
                else:
                    w = ("old" if x.get("age") is not None and x["age"] > s.get("fresh_days", 21) else "noreg" if x.get("reg") == "no"
                         else "price" if x.get("price") is not None else "bad")
                    why[w] = why.get(w, 0) + 1
            L(f"{name}: raw={len(xs)} kept={len(kept)} drop={why}")
            for x in kept[:8]:
                L(f"  {x.get('city')} €{x.get('price')} {x.get('m2')}м² age={x.get('age')} room={x.get('room')} {str(x.get('title'))[:50]} {x.get('url')}")
