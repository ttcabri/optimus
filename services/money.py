"""Деньги: счета, траты, доходы, лимиты по категориям, регулярные платежи. Копилки = цели в €.

Отложенное на цели не уходит со счёта: это часть денег, помеченная под цель. Свободно = на счетах − отложено − платежи впереди.
"""
import calendar
import time

from services import quest

CATS = {"food": ("🛒", "Еда"), "home": ("🧴", "Быт"), "rent": ("🏠", "Жильё"), "car": ("🚗", "Машина"), "health": ("💊", "Здоровье"),
        "subs": ("📱", "Связь и подписки"), "fun": ("🎮", "Развлечения"), "cloth": ("👕", "Одежда"), "gift": ("🎁", "Подарок себе"),
        "fine": ("💸", "Штрафы"), "other": ("📦", "Прочее")}
INC = {"ww": "WW", "work": "Работа", "sale": "Продажа", "other": "Другое"}


def _ms() -> int:
    return int(time.time() * 1000)


def _id(p: str = "m") -> str:
    return p + format(_ms(), "x") + format(time.perf_counter_ns() % 4096, "x")


def get(state: dict) -> dict:
    m = state.get("money")
    if not isinstance(m, dict):
        m = state["money"] = {}
    m.setdefault("accts", [{"id": "bank", "n": "Банк", "base": 0, "u": 0}])
    if not m["accts"]:
        m["accts"] = [{"id": "bank", "n": "Банк", "base": 0, "u": 0}]
    for k in ("ops", "reg", "del"):
        m.setdefault(k, [])
    m.setdefault("lim", {})
    return m


def merge(server: dict | None, incoming: dict | None) -> dict | None:
    if not isinstance(server, dict):
        return incoming
    if not isinstance(incoming, dict):
        return server
    from services.house import _merge_list
    dead = set(server.get("del") or []) | set(incoming.get("del") or [])
    out = dict(incoming)
    for k in ("accts", "ops", "reg"):
        out[k] = _merge_list(server.get(k) or [], incoming.get(k) or [], dead)
    if server.get("lu", 0) > incoming.get("lu", 0):
        out["lim"], out["lu"] = server.get("lim") or {}, server.get("lu", 0)
    out["warn"] = {**(incoming.get("warn") or {}), **(server.get("warn") or {})}
    out["del"] = sorted(dead)[-500:]
    return out


def acct(m: dict, name_or_id: str | None) -> dict:
    q = (name_or_id or "").strip().lower()
    for a in m["accts"]:
        if q and (a["id"] == q or a["n"].lower() == q or q in a["n"].lower()):
            return a
    return m["accts"][0]


def balance(m: dict, aid: str) -> float:
    a = next((x for x in m["accts"] if x["id"] == aid), None)
    if not a:
        return 0.0
    v = float(a.get("base") or 0)
    for o in m["ops"]:
        if o.get("acc") != aid:
            continue
        v += o["amt"] if o.get("k") == "inc" else -o["amt"]
    return round(v, 2)


def total(m: dict) -> float:
    return round(sum(balance(m, a["id"]) for a in m["accts"]), 2)


def ym(day: str | None = None) -> str:
    return (day or quest.today_key())[:7]


def month_ops(m: dict, month: str | None = None) -> list:
    mo = month or ym()
    return [o for o in m["ops"] if str(o.get("d", "")).startswith(mo)]


def spent_by_cat(m: dict, month: str | None = None) -> dict:
    out = {}
    for o in month_ops(m, month):
        if o.get("k") == "exp":
            out[o.get("cat", "other")] = round(out.get(o.get("cat", "other"), 0) + o["amt"], 2)
    return out


def income(m: dict, month: str | None = None) -> float:
    return round(sum(o["amt"] for o in month_ops(m, month) if o.get("k") == "inc"), 2)


def piggies(state: dict) -> list:
    """Копилки: активные цели, где число в €. [(goal, накоплено, цель)]"""
    P = quest.full_profile(state)
    out = []
    for g in P.get("goals") or []:
        mt = g.get("metric") or {}
        if g.get("status") == "done" or mt.get("unit") != "€":
            continue
        vals = (state.get("metrics") or {}).get(g.get("id")) or []
        v = float(vals[-1].get("v") or 0) if vals else float(mt.get("start") or 0)
        out.append((g, round(v, 2), mt.get("target")))
    return out


def reserved(state: dict) -> float:
    return round(sum(v for _, v, _ in piggies(state)), 2)


def reg_left(m: dict, today: str | None = None) -> list:
    """Платежи, которые ещё впереди в этом месяце (день не прошёл и не отмечен оплаченным)."""
    t = today or quest.today_key()
    mo, d = ym(t), int(t[8:10])
    return [r for r in m["reg"] if r.get("paid") != mo and int(r.get("day") or 1) >= d]


def free(state: dict) -> float:
    m = get(state)
    return round(total(m) - reserved(state) - sum(r["amt"] for r in reg_left(m)), 2)


def days_left(today: str | None = None) -> int:
    t = today or quest.today_key()
    y, mo, d = int(t[:4]), int(t[5:7]), int(t[8:10])
    return calendar.monthrange(y, mo)[1] - d + 1


def per_day(state: dict) -> tuple[float, str] | None:
    """Сколько можно тратить в день до конца месяца: свободные деньги (минус отложенное и платежи впереди) / дни."""
    m = get(state)
    if not (any(a.get("base") for a in m["accts"]) or m["ops"]):
        return None
    return round(max(0, free(state)) / days_left(), 2), "из свободных"


def lim_left(m: dict) -> list:
    """[(cat, лимит, потрачено, осталось в день)] по категориям с лимитом."""
    sp, dl = spent_by_cat(m), days_left()
    return [(k, v, sp.get(k, 0), round(max(0, v - sp.get(k, 0)) / dl, 2)) for k, v in (m.get("lim") or {}).items() if v]


def limit_warn(m: dict, cat: str) -> str:
    """Предупреждение один раз на 80% и один раз на 100% лимита категории за месяц."""
    lim = (m.get("lim") or {}).get(cat)
    if not lim:
        return ""
    sp = spent_by_cat(m).get(cat, 0)
    lvl = 100 if sp >= lim else 80 if sp >= 0.8 * lim else 0
    key = f"{ym()}:{cat}"
    warned = (m.setdefault("warn", {})).get(key, 0)
    if lvl <= warned:
        return ""
    m["warn"][key] = lvl
    name = CATS.get(cat, ("", cat))[1]
    if lvl == 100:
        return f"⚠️ Лимит «{name}» на месяц кончился: €{sp:g} из €{lim:g}."
    return f"⚠️ «{name}»: потрачено €{sp:g} из €{lim:g}, осталось €{lim - sp:g} на {days_left()} дн."


def add(state: dict, kind: str, amt: float, cat: str = "other", acc: str | None = None, note: str = "", day: str | None = None, src: str = "") -> tuple[dict, str]:
    m = get(state)
    kind = kind if kind in ("exp", "inc") else "exp"
    cats = CATS if kind == "exp" else INC
    o = {"id": _id(), "k": kind, "amt": round(abs(float(amt)), 2), "cat": cat if cat in cats else "other", "acc": acct(m, acc)["id"],
         "n": (note or "")[:60], "d": day or quest.today_key(), "u": _ms()}
    if src:
        o["src"] = src
    m["ops"].append(o)
    return o, (limit_warn(m, o["cat"]) if kind == "exp" else "")


def remove(state: dict, oid: str) -> bool:
    m = get(state)
    n = len(m["ops"])
    m["ops"] = [o for o in m["ops"] if o.get("id") != oid]
    if len(m["ops"]) < n:
        m["del"] = (m["del"] + [oid])[-500:]
        return True
    return False


def set_balance(state: dict, acc: str | None, value: float, name: str | None = None) -> dict:
    """Сверка: на счёте сейчас value. Новый счёт, если такого нет и задано имя."""
    m = get(state)
    a = None
    if name:
        a = next((x for x in m["accts"] if x["n"].lower() == name.strip().lower()), None)
        if not a:
            a = {"id": _id("a"), "n": name.strip()[:30], "base": 0, "u": _ms()}
            m["accts"].append(a)
    a = a or acct(m, acc)
    a["base"] = round(float(a.get("base") or 0) + float(value) - balance(m, a["id"]), 2)
    a["u"] = _ms()
    return a


def summary(state: dict) -> str:
    m = get(state)
    if not m["ops"] and not any(a.get("base") for a in m["accts"]) and not m["reg"]:
        return "ДЕНЬГИ: пока ничего не записано. «Заправка 60», «пришло 1200 WW», «на счету 1800» = инструмент money."
    sp = spent_by_cat(m)
    lim = m.get("lim") or {}
    cats = ", ".join(f"{CATS.get(k, ('', k))[1]} €{v:g}" + (f"/{lim[k]:g}" if lim.get(k) else "") for k, v in sorted(sp.items(), key=lambda x: -x[1])) or "трат нет"
    pdv = per_day(state)
    rl = reg_left(m)
    def lab(o):
        return o.get("n") or (CATS.get(o.get("cat"), ("", "трата"))[1] if o["k"] == "exp" else INC.get(o.get("cat"), "доход"))
    last = "; ".join(f"{o['d'][5:]} {'+' if o['k'] == 'inc' else '−'}€{o['amt']:g} {lab(o)}" for o in m["ops"][-6:])
    return ("ДЕНЬГИ (инструмент money; советуй по делу, когда видишь риск: лимит, крупная трата, мало свободных):\n"
            f"Счета: {', '.join(a['n'] + ' €' + format(balance(m, a['id']), 'g') for a in m['accts'])}. Всего €{total(m):g}, отложено на цели €{reserved(state):g}, свободно €{free(state):g}.\n"
            f"Этот месяц: потрачено {cats}. Пришло €{income(m):g}. Лимиты: {', '.join(CATS.get(k, ('', k))[1] + ' €' + format(v, 'g') for k, v in lim.items() if v) or 'нет'}.\n"
            + (f"Платежи впереди: {', '.join(r['n'] + ' €' + format(r['amt'], 'g') + ' ' + str(r.get('day', '')) + '-го' for r in rl)}.\n" if rl else "")
            + (f"Можно тратить ~€{pdv[0]:g} в день до конца месяца ({pdv[1]}).\n" if pdv else "")
            + "".join(f"Лимит {CATS[k][1]}: €{sp_:g} из €{v:g}, ~€{d:g}/день.\n" for k, v, sp_, d in lim_left(m))
            + (f"Последние: {last}" if last else ""))
