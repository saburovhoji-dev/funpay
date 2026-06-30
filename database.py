"""
database.py — Работа с SQLite через aiosqlite. Автоопределение пути на Railway.
"""

import os
import glob
import aiosqlite
from typing import Optional


def _find_db_path() -> str:
    explicit = os.environ.get("DB_PATH", "")
    if explicit and explicit != "/data/funpay_bot.db":
        return explicit
    patterns = [
        "/var/lib/containers/railwayapp/bind-mounts/*/vol_*/",
        "/data/",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return os.path.join(matches[0], "funpay_bot.db")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "funpay_bot.db")


DB_PATH = _find_db_path()


async def init_db() -> None:
    import logging
    logging.getLogger(__name__).info(f"БД: {DB_PATH}")
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY DEFAULT 1,
                golden_key TEXT NOT NULL DEFAULT '',
                proxy TEXT DEFAULT NULL,
                auto_lift_enabled INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funpay_id TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL DEFAULT '',
                response_text TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS responded_chats (
                chat_id TEXT NOT NULL UNIQUE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                created_at TEXT DEFAULT (strftime('%d.%m %H:%M', 'now'))
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO config (id, golden_key, proxy, auto_lift_enabled)
            VALUES (1, '', NULL, 0)
        """)
        await db.commit()


# ─── CONFIG ───────────────────────────────────────────────────────────────────

async def get_config() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM config WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {"id": 1, "golden_key": "", "proxy": None, "auto_lift_enabled": 0}

async def set_golden_key(golden_key: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET golden_key = ? WHERE id = 1", (golden_key,))
        await db.commit()

async def set_proxy(proxy: Optional[str]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET proxy = ? WHERE id = 1", (proxy,))
        await db.commit()

async def set_auto_lift(enabled: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET auto_lift_enabled = ? WHERE id = 1", (1 if enabled else 0,))
        await db.commit()


# ─── PRODUCTS ─────────────────────────────────────────────────────────────────

async def add_or_update_product(funpay_id: str, category: str, response_text: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO products (funpay_id, category, response_text) VALUES (?, ?, ?)
            ON CONFLICT(funpay_id) DO UPDATE SET category=excluded.category, response_text=excluded.response_text
        """, (funpay_id, category, response_text))
        await db.commit()

async def get_product_by_funpay_id(funpay_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products WHERE funpay_id = ?", (funpay_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_all_products() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products") as cursor:
            return [dict(r) for r in await cursor.fetchall()]

async def delete_product(funpay_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM products WHERE funpay_id = ?", (funpay_id,))
        await db.commit()
        return cursor.rowcount > 0


# ─── ORDERS ───────────────────────────────────────────────────────────────────

async def is_order_known(order_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM orders WHERE order_id = ?", (order_id,)) as cursor:
            return await cursor.fetchone() is not None

async def add_order(order_id: str, status: str = "new") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO orders (order_id, status) VALUES (?, ?)", (order_id, status))
        await db.commit()

async def update_order_status(order_id: str, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))
        await db.commit()

async def get_all_orders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders ORDER BY id DESC") as cursor:
            return [dict(r) for r in await cursor.fetchall()]


# ─── SETTINGS / АВТООТВЕТЧИК ──────────────────────────────────────────────────

async def get_autoresponse() -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'autoresponse_text'") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_autoresponse(text: Optional[str]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if text:
            await db.execute("""
                INSERT INTO settings (key, value) VALUES ('autoresponse_text', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (text,))
        else:
            await db.execute("DELETE FROM settings WHERE key = 'autoresponse_text'")
        await db.commit()

async def is_chat_responded(chat_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM responded_chats WHERE chat_id = ?", (chat_id,)) as cursor:
            return await cursor.fetchone() is not None

async def mark_chat_responded(chat_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO responded_chats (chat_id) VALUES (?)", (chat_id,))
        await db.commit()


# ─── LOGS ─────────────────────────────────────────────────────────────────────

async def add_log(level: str, message: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO logs (level, message) VALUES (?, ?)", (level, message))
        # Храним только последние 100 записей
        await db.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 100)")
        await db.commit()

async def get_logs(limit: int = 15) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


# ─── СТАТИСТИКА ───────────────────────────────────────────────────────────────

async def increment_stat(key: str) -> None:
    """Увеличивает счётчик статистики на 1."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO settings (key, value) VALUES (?, '1')
            ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)
        """, (f"stat_{key}",))
        await db.commit()

async def get_stat(key: str) -> int:
    """Возвращает значение счётчика статистики."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?", (f"stat_{key}",)
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

async def get_all_stats() -> dict:
    """Возвращает все счётчики статистики."""
    return {
        "deliveries": await get_stat("deliveries"),
        "autoresponses": await get_stat("autoresponses"),
        "lifts": await get_stat("lifts"),
        "orders_seen": await get_stat("orders_seen"),
    }
