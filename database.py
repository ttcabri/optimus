import datetime

import aiosqlite

from config import DATABASE_PATH

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

async def get_quest_state(telegram_id: int) -> dict:
    import json
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT state FROM quest_state WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
    if row and row[0]:
        try:
            data = json.loads(row[0])
            if isinstance(data, dict) and isinstance(data.get("days"), dict):
                return data
        except ValueError:
            pass
    return {"days": {}}


async def save_quest_state(telegram_id: int, state: dict) -> None:
    import json
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO quest_state (telegram_id, state, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(telegram_id) DO UPDATE SET state = excluded.state, updated_at = CURRENT_TIMESTAMP",
            (telegram_id, json.dumps(state, ensure_ascii=False)),
        )
        await db.commit()
