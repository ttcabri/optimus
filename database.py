import datetime

import logging

import aiosqlite

from config import DATABASE_PATH

logger = logging.getLogger(__name__)

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER UNIQUE,
    name TEXT DEFAULT 'Андрей',
    main_goal TEXT DEFAULT 'Первый платящий клиент онлайн через Upwork',
    goal_deadline TEXT DEFAULT '90 дней',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_logs (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER,
    date TEXT,
    checkin_done INTEGER DEFAULT 0,
    what_done TEXT,
    what_blocked TEXT,
    tomorrow_action TEXT,
    english_sentence TEXT,
    english_correction TEXT,
    trained INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ideas_parking (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER,
    idea TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversation_memory (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER,
    role TEXT,
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quest_state (
    telegram_id INTEGER PRIMARY KEY,
    state TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Дом живёт отдельно от личного квеста: общий для всех участников
CREATE TABLE IF NOT EXISTS quest_log (
    telegram_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    t TEXT NOT NULL,
    ev TEXT NOT NULL,
    task TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    min INTEGER,
    UNIQUE (telegram_id, day, t, ev, task)
);

CREATE TABLE IF NOT EXISTS households (
    id TEXT PRIMARY KEY,
    data TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS household_members (
    telegram_id INTEGER PRIMARY KEY,
    house_id TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS weekly_commitments (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER,
    week TEXT,
    commitment TEXT,
    done INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(CREATE_TABLES)
        await db.commit()


# --- user_profile ---

async def get_user(telegram_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM user_profile WHERE telegram_id = ?", (telegram_id,)
        )
        return await cursor.fetchone()


async def create_user(telegram_id: int, name: str, main_goal: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO user_profile (telegram_id, name, main_goal) VALUES (?, ?, ?)",
            (telegram_id, name, main_goal),
        )
        await db.commit()


async def update_main_goal(telegram_id: int, main_goal: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE user_profile SET main_goal = ? WHERE telegram_id = ?",
            (main_goal, telegram_id),
        )
        await db.commit()


# --- daily_logs ---

async def get_today_log(telegram_id: int) -> aiosqlite.Row | None:
    today = datetime.date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM daily_logs WHERE telegram_id = ? AND date = ?",
            (telegram_id, today),
        )
        return await cursor.fetchone()


async def get_log_by_date(telegram_id: int, date: str) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM daily_logs WHERE telegram_id = ? AND date = ?",
            (telegram_id, date),
        )
        return await cursor.fetchone()


async def ensure_today_log(telegram_id: int) -> None:
    today = datetime.date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM daily_logs WHERE telegram_id = ? AND date = ?",
            (telegram_id, today),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO daily_logs (telegram_id, date) VALUES (?, ?)",
                (telegram_id, today),
            )
            await db.commit()


async def update_today_log(telegram_id: int, **fields) -> None:
    if not fields:
        return
    await ensure_today_log(telegram_id)
    today = datetime.date.today().isoformat()
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values())
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"UPDATE daily_logs SET {columns} WHERE telegram_id = ? AND date = ?",
            (*values, telegram_id, today),
        )
        await db.commit()


async def get_week_logs(telegram_id: int) -> list[aiosqlite.Row]:
    today = datetime.date.today()
    week_ago = (today - datetime.timedelta(days=6)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM daily_logs WHERE telegram_id = ? AND date >= ? ORDER BY date",
            (telegram_id, week_ago),
        )
        return await cursor.fetchall()


async def get_streak(telegram_id: int) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT date, what_done FROM daily_logs WHERE telegram_id = ? AND what_done IS NOT NULL ORDER BY date DESC",
            (telegram_id,),
        )
        rows = await cursor.fetchall()

    streak = 0
    expected = datetime.date.today()
    dates_done = {row["date"] for row in rows}
    while expected.isoformat() in dates_done:
        streak += 1
        expected -= datetime.timedelta(days=1)
    return streak


# --- ideas_parking ---

async def add_idea(telegram_id: int, idea: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO ideas_parking (telegram_id, idea) VALUES (?, ?)",
            (telegram_id, idea),
        )
        await db.commit()


async def get_ideas(telegram_id: int) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM ideas_parking WHERE telegram_id = ? ORDER BY created_at DESC",
            (telegram_id,),
        )
        return await cursor.fetchall()


async def count_ideas(telegram_id: int) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM ideas_parking WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


# --- conversation_memory ---

async def add_message(telegram_id: int, role: str, content: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO conversation_memory (telegram_id, role, content) VALUES (?, ?, ?)",
            (telegram_id, role, content),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT COUNT(*) FROM conversation_memory WHERE telegram_id = ?",
            (telegram_id,),
        )
        total = (await cursor.fetchone())[0]
        if total > 80:
            await db.execute(
                """
                DELETE FROM conversation_memory
                WHERE telegram_id = ? AND id NOT IN (
                    SELECT id FROM conversation_memory
                    WHERE telegram_id = ?
                    ORDER BY id DESC
                    LIMIT 40
                )
                """,
                (telegram_id, telegram_id),
            )
            await db.commit()


async def get_recent_messages(telegram_id: int, limit: int = 20, since: str = "") -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM conversation_memory WHERE telegram_id = ? AND created_at >= ? ORDER BY id DESC LIMIT ?",
            (telegram_id, since, limit),
        )
        rows = await cursor.fetchall()
        return list(reversed(rows))


# --- weekly_commitments ---

async def add_commitment(telegram_id: int, week: str, commitment: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO weekly_commitments (telegram_id, week, commitment) VALUES (?, ?, ?)",
            (telegram_id, week, commitment),
        )
        await db.commit()


async def get_commitments(telegram_id: int, week: str) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM weekly_commitments WHERE telegram_id = ? AND week = ?",
            (telegram_id, week),
        )
        return await cursor.fetchall()


# --- quest_state (Квест дня, Mini App) ---

async def _personal_state(db, telegram_id: int) -> dict:
    import json
    cursor = await db.execute("SELECT state FROM quest_state WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    if row and row[0]:
        try:
            data = json.loads(row[0])
            if isinstance(data, dict) and isinstance(data.get("days"), dict):
                return data
        except ValueError:
            pass
    return {"days": {}}


async def _write_personal(db, telegram_id: int, state: dict) -> None:
    import json
    await db.execute(
        "INSERT INTO quest_state (telegram_id, state, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(telegram_id) DO UPDATE SET state = excluded.state, updated_at = CURRENT_TIMESTAMP",
        (telegram_id, json.dumps(state, ensure_ascii=False)),
    )


async def house_id_of(db, telegram_id: int) -> str | None:
    cursor = await db.execute("SELECT house_id FROM household_members WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    return row[0] if row else None


async def get_house(db, house_id: str) -> dict | None:
    import json
    cursor = await db.execute("SELECT data FROM households WHERE id = ?", (house_id,))
    row = await cursor.fetchone()
    try:
        return json.loads(row[0]) if row and row[0] else None
    except ValueError:
        return None


async def put_house(db, house_id: str, data: dict) -> None:
    import json
    await db.execute(
        "INSERT INTO households (id, data, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = CURRENT_TIMESTAMP",
        (house_id, json.dumps(data, ensure_ascii=False)),
    )


async def join_house(telegram_id: int, house_id: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO household_members (telegram_id, house_id) VALUES (?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET house_id = excluded.house_id", (telegram_id, house_id))
        await db.commit()


async def get_quest_state(telegram_id: int) -> dict:
    """Личный квест + дом, к которому человек привязан (дом хранится отдельно и общий для всех жильцов)."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        state = await _personal_state(db, telegram_id)
        hid = await house_id_of(db, telegram_id)
        if not hid and isinstance(state.get("house"), dict):
            # переезд со старого формата: дом лежал внутри личного состояния
            hid = state["house"].get("id") or f"house_{telegram_id}"
            if hid == "house_me":
                hid = f"house_{telegram_id}"
            state["house"]["id"] = hid
            await put_house(db, hid, state["house"])
            await db.execute("INSERT OR REPLACE INTO household_members (telegram_id, house_id) VALUES (?, ?)", (telegram_id, hid))
            house_data = state.pop("house")
            await _write_personal(db, telegram_id, state)
            await db.commit()
            state["house"] = house_data
        elif hid:
            state.pop("house", None)
            h = await get_house(db, hid)
            if h:
                state["house"] = h
        else:
            state.pop("house", None)
    if isinstance(state.get("house"), dict):
        from services import house as house_svc
        house_svc.get(state, telegram_id)  # нормализует поля и привязывает «me» к настоящему id
    return state


async def _log_events(db, telegram_id: int, state: dict) -> None:
    """Журнал для анализа: каждое событие дня (начал, сделал, снял, убрал, отдых, награда) с временем и названием.
    Пишется навсегда, отдельно от состояния. Свежие дни при каждом сохранении, всю историю один раз."""
    from datetime import datetime, timedelta
    from services import quest
    days = state.get("days") or {}
    cur = await db.execute("SELECT 1 FROM quest_log WHERE telegram_id = ? LIMIT 1", (telegram_id,))
    since = "" if await cur.fetchone() is None else (datetime.strptime(quest.today_key(), "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    nm = quest.names(quest.full_profile(state))
    chores = {"ch_" + c.get("id", ""): c.get("name", "") for c in ((state.get("house") or {}).get("chores") or [])}
    rows = []
    for k, d in days.items():
        if k < since or not isinstance(d, dict):
            continue
        custom = {c.get("id"): c.get("name", "") for c in d.get("custom") or []}
        span = d.get("span") or {}
        ev = list(d.get("ev") or [])
        logged = {(e[2], e[1]) for e in ev if len(e) == 3}
        ev += [[t, "done", q] for q, t in (d.get("at") or {}).items() if (q, "done") not in logged]  # старые дни без журнала
        for r in d.get("gl") or []:
            r = {"at": r, "min": 45} if isinstance(r, str) else r
            if (str(r.get("min")), "rest") not in logged:
                ev.append([r.get("at", ""), "rest", str(r.get("min", 45))])
        if d.get("firstSmoke") and ("", "reward") not in logged:
            ev.append([d["firstSmoke"], "reward", ""])
        for e in ev:
            if len(e) != 3:
                continue
            t, kind, q = e
            name = "Отдых" if kind == "rest" else "Награда" if kind == "reward" else custom.get(q) or chores.get(q) or nm.get(q) or q
            m = int(q) if kind == "rest" and str(q).isdigit() else (span.get(q) or {}).get("m") if kind == "done" else None
            rows.append((telegram_id, k, t, kind, q or "", name, m))
    if rows:
        await db.executemany("INSERT OR IGNORE INTO quest_log (telegram_id, day, t, ev, task, name, min) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


async def get_log(telegram_id: int, since: str) -> list[tuple]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT day, t, ev, name, min FROM quest_log WHERE telegram_id = ? AND day >= ? ORDER BY day, t", (telegram_id, since))
        return await cur.fetchall()


async def save_quest_state(telegram_id: int, state: dict) -> None:
    """Личное пишем в quest_state, дом сливаем с сохранённым домом (его могли поменять другие жильцы)."""
    from services import house as house_svc
    state = dict(state)
    incoming_house = state.pop("house", None)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await _write_personal(db, telegram_id, state)
        try:
            await _log_events(db, telegram_id, dict(state, house=incoming_house))
        except Exception as e:  # журнал не должен ломать сохранение
            logger.error(f"quest_log error: {e}")
        if isinstance(incoming_house, dict):
            hid = await house_id_of(db, telegram_id)
            if not hid:
                hid = incoming_house.get("id") if incoming_house.get("id") not in (None, "house_me") else f"house_{telegram_id}"
                await db.execute("INSERT OR REPLACE INTO household_members (telegram_id, house_id) VALUES (?, ?)", (telegram_id, hid))
            stored = await get_house(db, hid)
            merged = house_svc.merge(stored, dict(incoming_house, id=hid))
            await put_house(db, hid, merged)
        await db.commit()
