"""
main.py — Точка входа. Запуск Telegram-бота и двух фоновых asyncio-задач.
Токены читаются из переменных окружения (Railway Environment Variables).
"""

import asyncio
import logging
import os
import sys

from aiogram import Bot

import database as db
from funpay_client import FunPayClient
from tg_bot import create_bot_and_dispatcher

# ─── Настройки из переменных окружения ────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_TELEGRAM_ID  = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))

if not TELEGRAM_BOT_TOKEN:
    print("ОШИБКА: переменная окружения TELEGRAM_BOT_TOKEN не задана.", file=sys.stderr)
    sys.exit(1)

if not OWNER_TELEGRAM_ID:
    print("ОШИБКА: переменная окружения OWNER_TELEGRAM_ID не задана.", file=sys.stderr)
    sys.exit(1)

# Интервалы
ORDER_CHECK_INTERVAL_SEC = int(os.environ.get("ORDER_CHECK_INTERVAL_SEC", "10"))
LIFT_INTERVAL_SEC        = int(os.environ.get("LIFT_INTERVAL_SEC", str(2 * 60 * 60)))

# ─── Логирование ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ─── Фоновая задача 1: Мониторинг заказов ─────────────────────────────────────

async def orders_monitor_loop(bot: Bot) -> None:
    logger.info("Запущен мониторинг заказов.")
    while True:
        try:
            config = await db.get_config()
            golden_key = config.get("golden_key", "")

            if not golden_key:
                logger.warning("Мониторинг: golden_key не задан, жду следующей итерации.")
                await asyncio.sleep(ORDER_CHECK_INTERVAL_SEC)
                continue

            client = FunPayClient(golden_key, config.get("proxy"))
            try:
                paid_orders = await client.get_paid_orders()
            finally:
                await client.close()

            for order in paid_orders:
                if await db.is_order_known(order.order_id):
                    continue

                logger.info(f"Новый заказ: {order.order_id} | покупатель: {order.buyer_username}")

                product = None
                if order.product_funpay_id:
                    product = await db.get_product_by_funpay_id(order.product_funpay_id)

                if product:
                    send_client = FunPayClient(golden_key, config.get("proxy"))
                    try:
                        sent = await send_client.send_message(
                            chat_id=order.chat_id,
                            text=product["response_text"],
                        )
                    finally:
                        await send_client.close()

                    if sent:
                        await db.add_order(order.order_id, "delivered")
                        await bot.send_message(
                            chat_id=OWNER_TELEGRAM_ID,
                            text=(
                                f"✅ <b>Заказ выполнен автоматически</b>\n\n"
                                f"📦 Заказ: <code>{order.order_id}</code>\n"
                                f"👤 Покупатель: <b>{order.buyer_username}</b>\n"
                                f"🎁 Товар ID: <code>{order.product_funpay_id}</code>\n\n"
                                f"📨 Текст выдачи отправлен в чат FunPay."
                            ),
                            parse_mode="HTML",
                        )
                    else:
                        await db.add_order(order.order_id, "send_failed")
                        await bot.send_message(
                            chat_id=OWNER_TELEGRAM_ID,
                            text=(
                                f"⚠️ <b>Ошибка автовыдачи</b>\n\n"
                                f"📦 Заказ: <code>{order.order_id}</code>\n"
                                f"👤 Покупатель: <b>{order.buyer_username}</b>\n\n"
                                f"❌ Не удалось отправить сообщение. Проверьте вручную."
                            ),
                            parse_mode="HTML",
                        )
                else:
                    await db.add_order(order.order_id, "no_product")
                    await bot.send_message(
                        chat_id=OWNER_TELEGRAM_ID,
                        text=(
                            f"🔔 <b>Новый заказ — требует внимания</b>\n\n"
                            f"📦 Заказ: <code>{order.order_id}</code>\n"
                            f"👤 Покупатель: <b>{order.buyer_username}</b>\n"
                            f"🎁 Товар ID: <code>{order.product_funpay_id or 'не определён'}</code>\n\n"
                            f"⚠️ Товар не найден в базе. Выдайте вручную."
                        ),
                        parse_mode="HTML",
                    )

        except asyncio.CancelledError:
            logger.info("Мониторинг заказов остановлен.")
            break
        except Exception as e:
            logger.exception(f"Ошибка в мониторинге заказов: {e}")

        await asyncio.sleep(ORDER_CHECK_INTERVAL_SEC)


# ─── Фоновая задача 2: Автоподнятие лотов ─────────────────────────────────────

async def auto_lift_loop(bot: Bot) -> None:
    logger.info("Запущен цикл автоподнятия лотов.")
    while True:
        try:
            config = await db.get_config()

            if not config.get("auto_lift_enabled"):
                await asyncio.sleep(ORDER_CHECK_INTERVAL_SEC)
                continue

            golden_key = config.get("golden_key", "")
            if not golden_key:
                await asyncio.sleep(LIFT_INTERVAL_SEC)
                continue

            logger.info("Начинаю поднятие лотов...")
            client = FunPayClient(golden_key, config.get("proxy"))
            try:
                success = await client.raise_lots()
            finally:
                await client.close()

            if success:
                await bot.send_message(
                    chat_id=OWNER_TELEGRAM_ID,
                    text="⬆️ <b>Лоты подняты</b>\n\nАвтоподнятие выполнено успешно.",
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=OWNER_TELEGRAM_ID,
                    text=(
                        "⚠️ <b>Автоподнятие</b>\n\n"
                        "Не удалось поднять лоты или нет активных лотов."
                    ),
                    parse_mode="HTML",
                )

        except asyncio.CancelledError:
            logger.info("Цикл автоподнятия остановлен.")
            break
        except Exception as e:
            logger.exception(f"Ошибка в автоподнятии: {e}")

        await asyncio.sleep(LIFT_INTERVAL_SEC)


# ─── Запуск ───────────────────────────────────────────────────────────────────

async def main() -> None:
    await db.init_db()
    logger.info("База данных инициализирована.")

    bot, dp = create_bot_and_dispatcher(TELEGRAM_BOT_TOKEN)

    order_task = asyncio.create_task(orders_monitor_loop(bot))
    lift_task  = asyncio.create_task(auto_lift_loop(bot))

    try:
        try:
            await bot.send_message(
                chat_id=OWNER_TELEGRAM_ID,
                text=(
                    "🚀 <b>FunPay Bot запущен на Railway</b>\n\n"
                    "Мониторинг заказов и автоподнятие активны.\n"
                    "Используйте /start для управления."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить стартовое уведомление: {e}")

        logger.info("Запускаю polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

    finally:
        order_task.cancel()
        lift_task.cancel()
        await asyncio.gather(order_task, lift_task, return_exceptions=True)
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
