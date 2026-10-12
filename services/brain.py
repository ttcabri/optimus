"""Мозг: всё, что человек сохраняет (ссылки, посты, заметки, фото, документы).

Бот раскладывает по папкам, понимает, к чему это в его жизни, и достаёт только к месту:
в разговоре, утром под план, при новой цели, раз в неделю перечитывает всё под текущий этап.
"""
import html
import json
import logging
import re
import time

from services import quest

logger = logging.getLogger(__name__)

FOLDERS = {"inbox": "Разобрать", "ideas": "Идеи", "apply": "Применить", "recipes": "Рецепты", "want": "Хочу", "docs": "Документы"}
ICON = {"inbox": "📥", "ideas": "💡", "apply": "🎯", "recipes": "🍳", "want": "🌍", "docs": "📄"}
URL_RE = re.compile(r"https?://\S+")


def get(state: dict) -> dict:
    b = state.get("brain")
    if not isinstance(b, dict):
        b = state["brain"] = {"items": [], "del": []}
    b.setdefault("items", [])
    b.setdefault("del", [])
    return b


def live(state: dict) -> list:
    return [x for x in get(state)["items"] if x.get("fit") != "no"]


def find(state: dict, iid: str) -> dict | None:
    return next((x for x in get(state)["items"] if x.get("id") == iid), None)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return "b" + format(_now_ms(), "x") + format(int(time.perf_counter_ns()) % 4096, "x")


def touch(x: dict) -> dict:
    x["u"] = _now_ms()
    return x


def remove(state: dict, iid: str) -> dict | None:
    b, x = get(state), find(state, iid)
    if x:
        b["items"] = [y for y in b["items"] if y.get("id") != iid]
        b["del"] = (b["del"] + [iid])[-300:]
    return x


def merge(server: dict | None, incoming: dict | None) -> dict | None:
    """Записи по id, кто позже менял, тот и прав. Удалённые не воскресают."""
    if not isinstance(server, dict):
        return incoming
    if not isinstance(incoming, dict):
        return server
    from services.house import _merge_list
    dead = set(server.get("del") or []) | set(incoming.get("del") or [])
    out = dict(incoming)
    for k in ("review", "sig", "stage"):
        if server.get(k) and server.get(k) != incoming.get(k) and (server.get("ru", 0) >= incoming.get("ru", 0)):
            out[k] = server[k]
    out["ru"] = max(server.get("ru", 0), incoming.get("ru", 0))
    out["items"] = _merge_list(server.get("items") or [], incoming.get("items") or [], dead)
    out["del"] = sorted(dead)[-300:]
    return out


# ---------- этап жизни ----------
def stage(state: dict) -> str:
    P = quest.full_profile(state)
    gs = [g for g in (P.get("goals") or []) if g.get("status") != "done"]
    goals = "; ".join((("Здоровье: " if g.get("kind") == "health" else "Цель: ") + g.get("name", "")) for g in gs) or "целей нет"
    routine = ", ".join(it["name"] for it in quest.items(P) if it.get("main")) or "—"
    notes = "; ".join((state.get("notes") or [])[:12])
    ins = "; ".join(((state.get("insights") or {}).get("items") or [])[:4])
    return f"{goals}. Главные дела: {routine}. Что известно: {notes or '—'}. {ins}"


def stage_sig(state: dict) -> str:
    P = quest.full_profile(state)
    return "|".join(sorted(g.get("id", "") + ":" + g.get("name", "") for g in (P.get("goals") or []) if g.get("status") != "done"))


# ---------- ссылки ----------
async def link_meta(url: str) -> str:
    """Название и описание страницы. Видео не смотрим: только то, что видно в карточке ссылки."""
    import aiohttp
    out = []
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (compatible; TelegramBot)", "Accept-Language": "ru,en"}) as s:
            if re.search(r"youtu\.?be", url):
                async with s.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"}) as r:
                    if r.status == 200:
                        j = await r.json(content_type=None)
                        out.append(f"YouTube: {j.get('title', '')} (канал {j.get('author_name', '')})")
            async with s.get(url, allow_redirects=True) as r:
                if r.status == 200 and "html" in r.headers.get("Content-Type", ""):
                    raw = (await r.content.read(400_000)).decode("utf-8", "ignore")
                    def meta(p):
                        m = re.search(r'<meta[^>]+(?:property|name)=["\']' + p + r'["\'][^>]+content=["\']([^"\']{3,600})', raw, re.I) \
                            or re.search(r'<meta[^>]+content=["\']([^"\']{3,600})["\'][^>]+(?:property|name)=["\']' + p, raw, re.I)
                        return html.unescape(m.group(1)).strip() if m else ""
                    t = meta("og:title") or (re.search(r"<title[^>]*>([^<]{3,300})", raw, re.I) or [None, ""])[1].strip()
                    d = meta("og:description") or meta("description")
                    if t and not any(t in x for x in out):
                        out.append("Заголовок: " + html.unescape(t))
                    if d:
                        out.append("Описание: " + d[:600])
                    if not d:  # статья: первые абзацы
                        ps = re.findall(r"<p[^>]*>(.{40,800}?)</p>", raw, re.S | re.I)
                        txt = " ".join(re.sub(r"<[^>]+>", "", html.unescape(p)) for p in ps[:6])
                        if txt:
                            out.append("Текст: " + txt[:1500])
    except Exception as e:
        logger.info(f"link meta {url}: {e}")
    return "\n".join(out)


# ---------- разбор новой записи ----------
INGEST = """Ты второй мозг человека. Он сохранил вещь, разложи её и пойми, к чему она в его жизни.
Сейчас у него: {stage}
Сегодня {today}.

Что он сохранил:
{content}

Папки: ideas (мысли, идеи для бизнеса и проектов), apply (лайфхаки, советы, техники, которые можно внедрить), recipes (рецепты, еда),
want (купить, поехать, попробовать, место, вещь), docs (документы: справки, анализы, письма, договоры, чеки, личные фото документов),
inbox (если по содержимому НЕЛЬЗЯ понять, о чём это, например голая ссылка на видео без названия).
Ответь ТОЛЬКО JSON:
{{"folder": "...", "title": "коротко, до 60 знаков, по-русски", "sum": "1–2 строки: суть и что из этого взять",
"ctx": "в какой ситуации это пригодится (например: зал, поездка, теория на права, готовка, деньги, бизнес)",
"fit": "now если подходит под то, что у него сейчас, later если на потом, no если явно не про него или мусор",
"offer": "одна короткая фраза-предложение, что с этим сделать сейчас, или пусто. Например: «Вставить это в Теорию на этой неделе?»",
"act": "task если можно сделать делом, habit если привычкой в распорядок, goal если это может стать целью (для want), read если просто знать, или пусто",
"memory": "факт о самом человеке, если вещь его раскрывает (например: хочет в Норвегию), иначе пусто",
"ask": "если folder=inbox: короткий вопрос, о чём это, иначе пусто"}}"""


async def classify(state: dict, content: str) -> dict:
    from services import claude
    prompt = INGEST.format(stage=stage(state)[:1500], today=quest.today_key(), content=content[:4000])
    try:
        r = await claude._create(max_tokens=600, messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in r.content if b.type == "text")
        m = re.search(r"\{.*\}", text, re.S)
        out = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"brain classify: {e}")
        out = {}
    if out.get("folder") not in FOLDERS:
        out["folder"] = "inbox"
    if out.get("fit") not in ("now", "later", "no"):
        out["fit"] = "later"
    return out


def _content(text: str, meta: str, file: dict | None, note: str) -> str:
    parts = []
    if text:
        parts.append("Текст: " + text[:2500])
    if meta:
        parts.append("По ссылке:\n" + meta)
    if file:
        parts.append(f"Файл: {file.get('kind')} {file.get('name') or ''}".strip())
    if note:
        parts.append("Что он сам сказал об этом: " + note)
    return "\n".join(parts) or "пусто"


async def ingest(state: dict, text: str = "", file: dict | None = None, src: str = "tg", note: str = "", folder: str | None = None) -> dict:
    """Сохранить новую вещь. Меняет state, возвращает запись."""
    url = (URL_RE.search(text or "") or [None])[0] if text else None
    meta = await link_meta(url) if url else ""
    c = await classify(state, _content(text, meta, file, note))
    if folder in FOLDERS:
        c["folder"] = folder
    it = {"id": _new_id(), "f": c["folder"], "t": str(c.get("title") or (text or (file or {}).get("name") or "Без названия"))[:60],
          "s": str(c.get("sum") or "")[:300], "ctx": str(c.get("ctx") or "")[:120], "fit": c["fit"], "src": src, "d": quest.today_key()}
    if url:
        it["url"] = url[:500]
    if text:
        it["text"] = text[:2000]
    if file:
        it["file"] = file
    if c.get("act") in ("task", "habit", "goal", "read"):
        it["act"] = c["act"]
    if c.get("ask") and it["f"] == "inbox":
        it["ask"] = str(c["ask"])[:120]
    get(state)["items"].append(touch(it))
    mem = str(c.get("memory") or "").strip()[:200]
    if mem and mem not in (state.get("notes") or []):
        state["notes"] = ((state.get("notes") or []) + [mem])[-40:]
    it["_offer"] = str(c.get("offer") or "").strip()[:160]
    return it


async def redo(state: dict, it: dict, note: str) -> dict:
    """Человек объяснил, о чём вещь: разложить заново."""
    c = await classify(state, _content(it.get("text", ""), "", it.get("file"), note))
    if c["folder"] == "inbox":
        c["folder"] = "ideas"
    it.update({"f": c["folder"], "t": str(c.get("title") or it["t"])[:60], "s": str(c.get("sum") or note)[:300],
               "ctx": str(c.get("ctx") or "")[:120], "fit": c["fit"]})
    it.pop("ask", None)
    if c.get("act") in ("task", "habit", "goal", "read"):
        it["act"] = c["act"]
    touch(it)
    it["_offer"] = str(c.get("offer") or "").strip()[:160]
    return it


def reply_text(it: dict) -> str:
    if it["f"] == "inbox":
        return f"🧠 Сохранил в «Разобрать». {html.escape(it.get('ask') or 'О чём это?')}\nОтветь на это сообщение одной строкой или голосом, и я разложу."
    head = f"🧠 {ICON[it['f']]} {FOLDERS[it['f']]}: <b>{html.escape(it['t'])}</b>"
    body = html.escape(it.get("s") or "")
    tail = {"now": "Подходит под то, что у тебя сейчас.", "later": "Пока на потом, достану, когда будет к месту.", "no": "Похоже, не про тебя сейчас, но сохранил."}[it["fit"]]
    offer = html.escape(it.get("_offer") or "")
    return "\n".join(x for x in [head, body, offer or tail] if x)


def kb_for(it: dict):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    row = []
    if it["f"] != "inbox" and it.get("act") in ("task", "habit") and it.get("fit") != "no":
        row.append(InlineKeyboardButton(text="Сделать делом на завтра", callback_data=f"br:task:{it['id']}"))
    row.append(InlineKeyboardButton(text="Удалить", callback_data=f"br:del:{it['id']}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def to_task(state: dict, it: dict, day: str | None = None) -> str:
    """Вещь из мозга становится делом в плане (по умолчанию завтра)."""
    dk = day or quest.tomorrow_key()
    d = quest._day(state, dk)
    name = it["t"] if len(it["t"]) <= 50 else it["t"][:50]
    d.setdefault("custom", []).append({"id": "c" + format(_now_ms(), "x"), "name": name, "dur": 30, "brain": it["id"]})
    it["st"] = "used"
    it.pop("pin", None)
    touch(it)
    return dk


# ---------- контекст для чата ----------
def context(state: dict) -> str:
    items = [x for x in live(state) if x.get("st") != "done"][-60:]
    if not items:
        return "МОЗГ: пусто (пересланные посты, ссылки и заметки сохраняются сюда)."
    rows = [f"- {x['id']} [{FOLDERS.get(x['f'], x['f'])}{', сейчас актуально' if x.get('fit') == 'now' else ''}{', уже в деле' if x.get('st') == 'used' else ''}] {x['t']}"
            + (f" — {x['s']}" if x.get("s") else "") + (f" (пригодится: {x['ctx']})" if x.get("ctx") else "") for x in items]
    return ("МОЗГ (что он сам сохранял; доставай ТОЛЬКО когда реально к месту: тема разговора, цель, план дня, готовка, подарок, поездка. "
            "Коротко, со ссылкой на то, что он это сохранял. Не перечисляй мозг без повода. Изменить: инструмент brain):\n" + "\n".join(rows))


# ---------- перечитать под этап ----------
REVIEW = """Ты второй мозг человека. Перечитай всё, что он сохранял, под то, что у него в жизни сейчас.
Сейчас у него: {stage}
{changed}
Сохранённое:
{items}

Задача:
- drop: id того, что явно мусор или больше не про него (не трогай docs и recipes).
- fit: для остальных новая оценка now/later.
- pins: 0–2 вещи, которые реально стоит внедрить именно сейчас, с причиной, привязанной к его целям и плану. Если ничего не подходит, пусто.
  act: task (сделать делом), habit (в распорядок), goal (сделать целью), read (просто вспомнить).
Ответь ТОЛЬКО JSON: {{"drop": [], "fit": {{"id": "now"}}, "pins": [{{"id": "...", "why": "одна строка, почему сейчас", "act": "task"}}]}}"""


async def review(state: dict, changed: bool = False) -> list:
    """Возвращает новые закрепы [{id, why, act}] и меняет state."""
    from services import claude
    items = [x for x in live(state) if x.get("st") != "done" and x["f"] != "inbox"]
    b = get(state)
    b["review"], b["sig"], b["ru"] = quest.today_key(), stage_sig(state), _now_ms()
    if not items:
        return []
    rows = "\n".join(f"{x['id']} [{x['f']}] {x['t']}: {x.get('s', '')} (пригодится: {x.get('ctx', '')}; сохранено {x.get('d', '')})" for x in items[-120:])
    prompt = REVIEW.format(stage=stage(state)[:1500], items=rows,
                           changed="У него новый этап: цели поменялись. Подними то, что раньше было на потом, а теперь к месту." if changed else "")
    try:
        r = await claude._create(max_tokens=900, messages=[{"role": "user", "content": prompt}])
        text = "".join(x.text for x in r.content if x.type == "text")
        m = re.search(r"\{.*\}", text, re.S)
        out = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"brain review: {e}")
        return []
    for iid in out.get("drop") or []:
        x = find(state, str(iid))
        if x and x["f"] not in ("docs", "recipes"):
            remove(state, x["id"])
    for iid, f in (out.get("fit") or {}).items():
        x = find(state, str(iid))
        if x and f in ("now", "later") and x.get("fit") != f:
            x["fit"] = f
            touch(x)
    pins = []
    for p in (out.get("pins") or [])[:2]:
        x = find(state, str(p.get("id")))
        if x and x.get("st") != "used":
            x["pin"] = {"why": str(p.get("why") or "")[:160], "act": p.get("act") if p.get("act") in ("task", "habit", "goal", "read") else "read", "d": quest.today_key()}
            touch(x)
            pins.append(x)
    return pins


def due(state: dict) -> str | None:
    """Пора ли перечитать: раз в неделю или когда поменялся этап (цели)."""
    b = get(state)
    items = [x for x in live(state) if x["f"] != "inbox"]
    if len(items) < 2:
        return None
    if b.get("sig") is not None and b.get("sig") != stage_sig(state):
        return "stage"
    last = b.get("review") or ""
    if not last or _days(last, quest.today_key()) >= 7:
        return "week"
    return None


def _days(a: str, b: str) -> int:
    from datetime import date
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except ValueError:
        return 99


def pins(state: dict) -> list:
    return [x for x in live(state) if x.get("pin") and not x["pin"].get("off") and x.get("st") != "used"]


def morning_line(state: dict) -> str:
    """Утром: одна закреплённая вещь, если она есть и ещё не показывалась утром."""
    for x in pins(state):
        if not x["pin"].get("shown"):
            x["pin"]["shown"] = quest.today_key()
            touch(x)
            return f"🧠 К месту: {html.escape(x['t'])}. {html.escape(x['pin'].get('why') or '')}"
    return ""


def recipes(state: dict) -> list:
    return [x for x in live(state) if x["f"] == "recipes"]
