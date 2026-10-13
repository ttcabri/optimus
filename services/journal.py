"""Журнал изменений: при каждом сохранении сравниваем «было» и «стало» и пишем, что поменялось.

Бот видит последние строки в контексте, поэтому знает, что человек сделал руками в приложении
и что он сам записал раньше. Не дублирует и не спорит с удалённым.
"""
import contextvars

# кто сохраняет: приложение, бот (чат), бот (кнопка), бот (авто)
SRC: contextvars.ContextVar[str] = contextvars.ContextVar("journal_src", default="бот")

_MON_K = {"exp": "трата", "inc": "доход", "tr": "перевод"}


def _eur(v) -> str:
    try:
        return f"€{float(v):g}"
    except (TypeError, ValueError):
        return "€?"


def _by_id(xs) -> dict:
    return {str(x.get("id")): x for x in xs or [] if isinstance(x, dict) and x.get("id")}


def _acc_name(m: dict, aid) -> str:
    a = next((x for x in m.get("accts") or [] if x.get("id") == aid), None)
    return (a or {}).get("n") or str(aid or "?")


def _op(m: dict, o: dict) -> str:
    k = o.get("k")
    if k == "tr":
        s = f"перевод {_eur(o.get('amt'))} {_acc_name(m, o.get('acc'))} → {_acc_name(m, o.get('to'))}"
    else:
        from services import money
        c = o.get("cat") or ""
        c = money.INC.get(c, c) if k == "inc" else (money.CATS.get(c) or ("", c))[1]
        s = f"{_MON_K.get(k, k)} {_eur(o.get('amt'))} «{c}» {_acc_name(m, o.get('acc'))}"
    if o.get("n"):
        s += f", заметка «{o['n']}»"
    return s + f" ({o.get('d') or ''}, id {o.get('id')})"


def _money(old: dict, new: dict) -> list[str]:
    out = []
    a, b = _by_id(old.get("ops")), _by_id(new.get("ops"))
    for i, o in b.items():
        if i not in a:
            out.append("добавил " + _op(new, o))
        else:
            p = a[i]
            ch = [f"{f}: {p.get(f)!r} → {o.get(f)!r}" for f in ("amt", "n", "cat", "acc", "to", "d", "k") if p.get(f) != o.get(f)]
            if ch:
                out.append("изменил " + _op(new, o) + ": " + "; ".join(ch))
    for i, o in a.items():
        if i not in b:
            out.append("удалил " + _op(old, o))
    oa, na = _by_id(old.get("accts")), _by_id(new.get("accts"))
    for i, x in na.items():
        p = oa.get(i)
        if p and p.get("base") != x.get("base"):
            out.append(f"сверил счёт {x.get('n')}: база {_eur(p.get('base'))} → {_eur(x.get('base'))}")
        if p and p.get("n") != x.get("n"):
            out.append(f"переименовал счёт {p.get('n')} → {x.get('n')}")
    for k, nm in (("reg", "регулярный платёж"), ("debts", "долг")):
        ra, rb = _by_id(old.get(k)), _by_id(new.get(k))
        for i, x in rb.items():
            label = f"{nm} «{x.get('n') or x.get('who') or ''}» {_eur(x.get('amt'))}"
            if i not in ra:
                out.append("добавил " + label)
            elif x.get("paid") != ra[i].get("paid") and x.get("paid"):
                out.append(f"отметил оплаченным {label} за {x['paid']}")
            elif x.get("done") and not ra[i].get("done"):
                out.append("закрыл " + label)
            elif x.get("amt") != ra[i].get("amt"):
                out.append(f"изменил {label} (было {_eur(ra[i].get('amt'))})")
        for i, x in ra.items():
            if i not in rb:
                out.append(f"удалил {nm} «{x.get('n') or x.get('who') or ''}»")
    if (old.get("lim") or {}) != (new.get("lim") or {}):
        out.append(f"лимиты: {new.get('lim') or {}}")
    return ["Деньги: " + x for x in out]


def _names(P: dict) -> dict:
    out = {}
    for it in (P or {}).get("routine") or []:
        if isinstance(it, dict) and it.get("id"):
            out[it["id"]] = it.get("name") or it["id"]
            for s in it.get("steps") or []:
                if isinstance(s, dict) and s.get("id"):
                    out[s["id"]] = f"{it.get('name')}: {s.get('name') or s['id']}"
    return out


def _all_names(st: dict) -> dict:
    from services import quest
    try:
        return _names(quest.full_profile(st))
    except Exception:
        return _names(st.get("profile") or {})


def _days(old: dict, new: dict, names: dict) -> list[str]:
    out = []
    od, nd = old.get("days") or {}, new.get("days") or {}
    for k in sorted(nd)[-3:]:  # правки идут в последние дни, старые не трогаем
        a, b = od.get(k) or {}, nd.get(k) or {}
        cust = {c.get("id"): c.get("name") for c in (a.get("custom") or []) + (b.get("custom") or []) if isinstance(c, dict)}
        nm = lambda q: names.get(q) or cust.get(q) or q  # noqa: E731
        dd = f"{k[8:10]}.{k[5:7]}"
        ad, bd = set(a.get("done") or []), set(b.get("done") or [])
        for q in sorted(bd - ad):
            out.append(f"{dd} отметил сделанным «{nm(q)}»" + (f" в {(b.get('at') or {}).get(q)}" if (b.get("at") or {}).get(q) else ""))
        for q in sorted(ad - bd):
            out.append(f"{dd} снял отметку «{nm(q)}»")
        ac, bc = _by_id(a.get("custom")), _by_id(b.get("custom"))
        for i, c in bc.items():
            if i not in ac:
                out.append(f"{dd} новое дело «{c.get('name')}»" + (f" в {c['at']}" if c.get("at") else ""))
        for i, c in ac.items():
            if i not in bc:
                out.append(f"{dd} убрал дело «{c.get('name')}»")
        for q in sorted(set(b.get("skip") or []) - set(a.get("skip") or [])):
            out.append(f"{dd} убрал на сегодня «{nm(q)}»")
        for q, o in (b.get("ovr") or {}).items():
            if (a.get("ovr") or {}).get(q) != o:
                out.append(f"{dd} поменял на сегодня «{nm(q)}»: " + ", ".join(f"{k}={v or 'без времени'}" for k, v in o.items()))
        for q, v in (b.get("dur") or {}).items():
            if (a.get("dur") or {}).get(q) != v:
                out.append(f"{dd} длительность на сегодня «{nm(q)}»: {v} мин")
        if b.get("woke") and not a.get("woke"):
            out.append(f"{dd} встал в {b['woke']}")
        if (b.get("ord") or []) != (a.get("ord") or []) and b.get("ord"):
            out.append(f"{dd} поменял порядок дел: " + ", ".join(nm(q) for q in b["ord"][:8]))
        ag, bg = a.get("gl") or [], b.get("gl") or []
        if len(bg) > len(ag):
            g = bg[-1]
            out.append(f"{dd} отдых {g.get('min')} мин с {g.get('at')}")
    return out


def _routine(old: dict, new: dict) -> list[str]:
    a = _by_id(((old.get("profile") or {}).get("routine")))
    b = _by_id(((new.get("profile") or {}).get("routine")))
    if not a or not b:
        return []
    out = []
    for i, x in b.items():
        if i not in a:
            out.append(f"Распорядок: добавил «{x.get('name')}»")
        else:
            p = a[i]
            ch = [f for f in ("name", "dur", "at", "days", "main", "proof") if p.get(f) != x.get(f)]
            if ch:
                out.append(f"Распорядок: изменил «{x.get('name')}» ({', '.join(ch)})")
    for i, x in a.items():
        if i not in b:
            out.append(f"Распорядок: удалил «{x.get('name')}»")
    if [i for i in a if i in b] != [i for i in b if i in a]:
        out.append("Распорядок: поменял порядок дел")
    pa, pb = (old.get("profile") or {}).get("penalty"), (new.get("profile") or {}).get("penalty")
    if pa != pb and pb is not None:
        out.append(f"Штраф: {pb}")
    return out


def _house(old: dict, new: dict) -> list[str]:
    out = []
    a, b = _by_id(old.get("items")), _by_id(new.get("items"))
    q = lambda x: (f" {x['amt']:g} {x.get('unit') or ''}".rstrip() if isinstance(x.get("amt"), (int, float)) else "")  # noqa: E731
    for i, x in b.items():
        if i not in a:
            out.append(f"добавил продукт «{x.get('name')}»{q(x)}")
        elif (a[i].get("amt"), a[i].get("level")) != (x.get("amt"), x.get("level")):
            lv = {"ok": "есть", "low": "мало", "out": "нет"}
            out.append(f"«{x.get('name')}»: {q(a[i]) or lv.get(a[i].get('level'), '?')} → {q(x) or lv.get(x.get('level'), '?')}")
    for i, x in a.items():
        if i not in b:
            out.append(f"убрал продукт «{x.get('name')}»")
    sa, sb = _by_id(old.get("shop")), _by_id(new.get("shop"))
    for i, x in sb.items():
        if i not in sa:
            out.append(f"в покупки «{x.get('name')}»")
        elif x.get("done") and not sa[i].get("done"):
            out.append(f"купил «{x.get('name')}»")
    for i, x in sa.items():
        if i not in sb:
            out.append(f"убрал из покупок «{x.get('name')}»")
    ca, cb = _by_id(old.get("chores")), _by_id(new.get("chores"))
    for i, x in cb.items():
        if i in ca and x.get("last") != ca[i].get("last"):
            out.append(f"сделал по дому «{x.get('name')}»")
        elif i not in ca:
            out.append(f"новое дело по дому «{x.get('name')}»")
    return ["Дом: " + x for x in out]


def _misc(old: dict, new: dict) -> list[str]:
    out = []
    ba, bb = _by_id((old.get("brain") or {}).get("items")), _by_id((new.get("brain") or {}).get("items"))
    for i, x in bb.items():
        if i not in ba:
            out.append(f"Мозг: сохранил «{x.get('t')}» ({x.get('f')})")
        elif x.get("f") != ba[i].get("f") or x.get("st") != ba[i].get("st"):
            out.append(f"Мозг: «{x.get('t')}» → {x.get('f')}{' ' + x['st'] if x.get('st') else ''}")
    for i, x in ba.items():
        if i not in bb:
            out.append(f"Мозг: удалил «{x.get('t')}»")
    ga, gb = old.get("goals") or {}, new.get("goals") or {}
    for k in ("subs", "maxViews", "goalKg"):
        if gb.get(k) != ga.get(k) and gb.get(k) is not None:
            out.append(f"Цели: {k} {ga.get(k)} → {gb.get(k)}")
    wa, wb = old.get("body") or [], new.get("body") or []
    if len(wb) > len(wa) and isinstance(wb[-1], dict):
        out.append(f"Вес: {wb[-1].get('kg')} кг")
    return out


def diff(old: dict, new: dict, old_house: dict | None = None, new_house: dict | None = None) -> list[str]:
    old, new = old or {}, new or {}
    try:
        lines = (_money(old.get("money") or {}, new.get("money") or {})
                 + _days(old, new, _all_names(old) | _all_names(new))
                 + _routine(old, new) + _misc(old, new))
        if isinstance(new_house, dict):
            lines += _house(old_house or {}, new_house)
    except Exception as e:  # журнал не должен ломать сохранение
        lines = [f"(журнал: ошибка разбора {e})"]
    return [x[:300] for x in lines][:40]
