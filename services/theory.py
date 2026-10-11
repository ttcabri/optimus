"""Учёба на теорию CBR (авто, категория B): темы по порядку, результаты тестов, что учить сегодня.

Экзамен с апреля 2025: 50 вопросов вперемешку, 30 минут, сдал = минимум 44 правильных.
Можно сдавать на английском.
"""

PASS, TOTAL = 44, 50

TOPICS = [
    {"ru": "Дорожные знаки, разметка, светофоры, жесты регулировщика",
     "en": "Traffic signs, road markings, traffic lights, traffic controllers",
     "focus": "все знаки по группам (запрещающие, предписывающие, предупреждающие), акульи зубы, сплошная и прерывистая, синие зоны парковки, что важнее: регулировщик > светофор > знаки > общие правила"},
    {"ru": "Приоритет (кто едет первым)",
     "en": "Right of way",
     "focus": "правый приоритет на равнозначных, главная дорога, круговое движение, трамвай, велосипедисты и пешеходы при повороте, выезд со двора и с грунтовки уступает всем"},
    {"ru": "Положение на дороге и манёвры",
     "en": "Positioning on the road, lane choice, merging, overtaking",
     "focus": "держаться правее, выбор полосы перед поворотом, ritsen (слияние «молнией»), где обгон запрещён, обгон велосипедиста, смена полосы"},
    {"ru": "Особые дороги и участники",
     "en": "Special roads and road users",
     "focus": "autosnelweg и autoweg (минимальная скорость, разгон, аварийная полоса), erf (жилая зона, шагом), fietsstraat, автобусы, тракторы, спецмашины с маячками"},
    {"ru": "Парковка, остановка, разворот, задний ход",
     "en": "Stopping, parking, reversing, turning around",
     "focus": "где нельзя останавливаться и где нельзя парковаться (разница!), расстояния до перекрёстка и переходов, жёлтая разметка у бордюра, парковочный диск"},
    {"ru": "Скорость, дистанция, безопасность",
     "en": "Speed limits, following distance, safe driving",
     "focus": "лимиты: 50 город, 80 вне города, 100 autoweg, 100/130 autosnelweg; правило 2 секунд; фары и туманки; ремни, детские кресла; телефон за рулём"},
    {"ru": "ДТП, поломки, алкоголь и законы",
     "en": "Accidents, breakdowns, alcohol, Dutch traffic law",
     "focus": "что делать при ДТП и поломке на трассе, аварийный треугольник, промилле (0,5 и 0,2 для новичков), штрафные баллы для новичков, документы"},
    {"ru": "Машина, экология, распознавание опасности",
     "en": "Vehicle knowledge, eco-driving, hazard perception",
     "focus": "лампочки на приборке, шины (минимум 1,6 мм), тормоза, экономичная езда; в видео-вопросах: тормозить, отпустить газ или ничего, смотреть на детей, велосипедистов, мяч, открытые двери"},
]

MOCK = len(TOPICS)  # после тем: полные пробные экзамены


def get(state: dict) -> dict:
    th = state.get("theory")
    if not isinstance(th, dict):
        th = {"i": 0, "log": []}
        state["theory"] = th
    th.setdefault("i", 0)
    th.setdefault("log", [])
    return th


def stage_name(i: int) -> str:
    if i >= MOCK:
        return "Полные пробные экзамены по 50 вопросов"
    return f"Тема {i + 1} из {len(TOPICS)}: {TOPICS[i]['ru']}"


def today_task(state: dict) -> str:
    th = get(state)
    i = th["i"]
    last = [x for x in th["log"] if x.get("i") == i]
    if i >= MOCK:
        good = sum(1 for x in th["log"][-3:] if x.get("total") == TOTAL and x.get("score", 0) >= PASS)
        return (f"📚 <b>{stage_name(i)}</b>\nОдин полный тест на TheorieExamen, на английском, как на экзамене. "
                f"Сдал = {PASS} из {TOTAL}. Три сдачи подряд и можно записываться в CBR.\n"
                f"Подряд сейчас: {good} из 3. Скрин результата сюда.")
    t = TOPICS[i]
    tail = ""
    if last:
        x = last[-1]
        tail = f"\nПрошлый раз по этой теме: {x.get('score')}/{x.get('total')}." + (f" Ошибки: {x['weak']}." if x.get("weak") else "")
    return (f"📚 <b>{stage_name(i)}</b>\n<i>{t['en']}</i>\n\n"
            f"Что учить: {t['focus']}.\n\n"
            f"Как: прочитай главу по этой теме, потом тест по ней на TheorieExamen. "
            f"Набрал 88% и выше два раза, переходим дальше. Скрин результата сюда.{tail}")


def record(state: dict, score: int | None, total: int | None, weak: str, day: str) -> str:
    """Записывает результат теста. Возвращает строку для ответа (и двигает тему, если пора)."""
    th = get(state)
    if not score or not total:
        return ""
    i = th["i"]
    th["log"] = (th["log"] + [{"d": day, "i": i, "score": score, "total": total, "weak": weak[:120]}])[-120:]
    pct = round(100 * score / total)
    if i >= MOCK:
        verdict = "Сдал бы ✅" if total == TOTAL and score >= PASS else f"Не сдал бы ❌ Нужно {PASS} из {TOTAL}."
        good = 0
        for x in reversed(th["log"]):
            if x.get("total") == TOTAL and x.get("score", 0) >= PASS:
                good += 1
            else:
                break
        if good >= 3:
            verdict += "\n\n🎯 Три сдачи подряд. Пора записываться на экзамен в CBR (можно на английском)."
        return f"📊 {score}/{total} ({pct}%). {verdict}"
    hits = [x for x in th["log"] if x.get("i") == i and x.get("total") and x["score"] / x["total"] >= 0.88]
    if len(hits) >= 2:
        th["i"] = i + 1
        return f"📊 {score}/{total} ({pct}%). Тема закрыта ✅\nДальше: {stage_name(th['i'])}."
    if pct >= 88:
        return f"📊 {score}/{total} ({pct}%). Хорошо. Ещё один такой результат по этой теме и идём дальше."
    return f"📊 {score}/{total} ({pct}%). Мало, нужно 88%+. Разбери ошибки и повтори тест по теме." + (f"\nЧто подтянуть: {weak}." if weak else "")


def set_topic(state: dict, i: int) -> str:
    th = get(state)
    th["i"] = max(0, min(MOCK, i))
    return stage_name(th["i"])


def context(state: dict) -> str:
    th = get(state)
    lines = [f"Этап: {stage_name(th['i'])}"]
    for x in th["log"][-5:]:
        lines.append(f"- {x['d']}: {x['score']}/{x['total']}" + (f", ошибки: {x['weak']}" if x.get("weak") else ""))
    lines.append("План тем: " + "; ".join(f"{n + 1}) {t['ru']}" for n, t in enumerate(TOPICS)) + f"; {MOCK + 1}) полные пробные экзамены")
    return "\n".join(lines)
