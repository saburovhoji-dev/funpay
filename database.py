"""
database.py — Работа с локальной SQLite-базой данных через aiosqlite.
Путь к БД берётся из переменной окружения DB_PATH (Railway Volume).
"""

import os
import aiosqlite
from typing import Optional

# Railway Volume монтируется в /data — задайте этот путь в Variables
# Локально по умолчанию создаётся рядом со скриптом
DB_PATH = os.environ.get("DB_PATH", "/data/funpay_bot.db")


async def init_db() -> None:
    """Создаёт все необходимые таблицы при первом запуске."""
    # Убеждаемся, что директория существует (важно для Railway Volume)
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
                status TEXT NOT NULL DEFAULT 'new'
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
            if row is None:
                return {"id": 1, "golden_key": "", "proxy": None, "auto_lift_enabled": 0}
            return dict(row)


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
        await db.execute(
            "UPDATE config SET auto_lift_enabled = ? WHERE id = 1",
            (1 if enabled else 0,)
        )
        await db.commit()


# ─── PRODUCTS ─────────────────────────────────────────────────────────────────

async def add_or_update_product(funpay_id: str, category: str, response_text: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO products (funpay_id, category, response_text)
            VALUES (?, ?, ?)
            ON CONFLICT(funpay_id) DO UPDATE SET
                category = excluded.category,
                response_text = excluded.response_text
        """, (funpay_id, category, response_text))
        await db.commit()


async def get_product_by_funpay_id(funpay_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM products WHERE funpay_id = ?", (funpay_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_products() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def delete_product(funpay_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM products WHERE funpay_id = ?", (funpay_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


# ─── ORDERS ───────────────────────────────────────────────────────────────────

async def is_order_known(order_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM orders WHERE order_id = ?", (order_id,)
        ) as cursor:
            return await cursor.fetchone() is not None


async def add_order(order_id: str, status: str = "new") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO orders (order_id, status) VALUES (?, ?)",
            (order_id, status)
        )
        await db.commit()


async def update_order_status(order_id: str, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE orders SET status = ? WHERE order_id = ?",
            (status, order_id)
        )
        await db.commit()


async def get_order(order_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
