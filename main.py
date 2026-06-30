"""
main.py — Точка входа. Запуск бота и трёх фоновых задач.
"""

import asyncio
import logging
import os
import sys

from aiogram import Bot

import database as db
from funpay_client import FunPayClient
from tg_bot import create_bot_and_dispatcher

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_TELEGRAM_ID  = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))

if not TELEGRAM_BOT_TOKEN:
    print("ОШИБКА: TELEGRAM_BOT_TOKEN не задан.", file=sys.stderr)
    sys.exit(1)
if not OWNER_TELEGRAM_ID:
    print("ОШИБКА: OWNER_TELEGRAM_ID не задан.", file=sys.stderr)
    sys.exit(1)

ORDER_CHECK_INTERVAL_SEC = int(os.environ.get("ORDER_CHECK_INTERVAL_SEC", "10"))
LIFT_INTERVAL_SEC        = int(os.environ.get("LIFT_INTERVAL_SEC", str(2 * 60 * 60)))
AUTORESPONSE_INTERVAL_SEC = 15

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def orders_monitor_loop(bot: Bot) -> None:
    logger.info("Запущен мониторинг заказов.")
    while True:
        try:
            config = await db.get_config()
            golden_key = config.get("golden_key", "")
            if not golden_key:
                await asyncio.sleep(ORDER_CHECK_INTERVAL_SEC)
                continue

            client = FunPayClient(golden_key, config.get("proxy"))
            try:
                paid_orders = await asyncio.wait_for(client.get_paid_orders(), timeout=20.0)
            finally:
                await client.close()

            for order in paid_orders:
                if await db.is_order_known(order.order_id):
                    continue

                logger.info(f"Новый заказ: {order.order_id} | {order.buyer_username}")

                product = None
                if order.product_funpay_id:
                    product = await db.get_product_by_funpay_id(order.product_funpay_id)

                if product:
                    send_client = FunPayClient(golden_key, config.get("proxy"))
                    try:
                        sent = await asyncio.wait_for(
                            send_client.send_message(order.chat_id, product["response_text"]),
                            timeout=15.0
                        )
                    finally:
                        await send_client.close()

                    if sent:
                        await db.add_order(order.order_id, "delivered")
                        await db.increment_stat("deliveries")
                        await db.add_log("success", f"Выдан заказ {order.order_id} → {order.buyer_username}")
                        await bot.send_message(
                            chat_id=OWNER_TELEGRAM_ID,
                            text=(
                                f"✅ <b>Заказ выполнен!</b>\n\n"
                                f"📦 Заказ: <code>{order.order_id}</code>\n"
                                f"👤 Покупатель: <b>{order.buyer_username}</b>\n"
                                f"🎁 Товар ID: <code>{order.product_funpay_id}</code>\n\n"
                                f"📨 Текст выдачи отправлен автоматически."
                            ),
                            parse_mode="HTML",
                        )
                    else:
                        await db.add_order(order.order_id, "send_failed")
                        await db.add_log("error", f"Ошибка отправки для заказа {order.order_id}")
                        await bot.send_message(
                            chat_id=OWNER_TELEGRAM_ID,
                            text=(
                                f"❌ <b>Ошибка автовыдачи</b>\n\n"
                                f"📦 Заказ: <code>{order.order_id}</code>\n"
                                f"👤 Покупатель: <b>{order.buyer_username}</b>\n\n"
                                f"Не удалось отправить сообщение. Проверьте вручную."
                            ),
                            parse_mode="HTML",
                        )
                else:
                    await db.add_order(order.order_id, "no_product")
                    await db.add_log("warning", f"Заказ {order.order_id} — товар не найден в базе")
                    await bot.send_message(
                        chat_id=OWNER_TELEGRAM_ID,
                        text=(
                            f"🔔 <b>Новый заказ — нужна выдача вручную</b>\n\n"
                            f"📦 Заказ: <code>{order.order_id}</code>\n"
                            f"👤 Покупатель: <b>{order.buyer_username}</b>\n"
                            f"🎁 Товар ID: <code>{order.product_funpay_id or 'не определён'}</code>\n\n"
                            f"⚠️ Товар не найден в базе. Добавьте его через 📦 Товары."
                        ),
                        parse_mode="HTML",
                    )

        except asyncio.CancelledError:
            break
        except asyncio.TimeoutError:
            logger.warning("Таймаут при проверке заказов.")
        except Exception as e:
            logger.exception(f"Ошибка в мониторинге заказов: {e}")

        await asyncio.sleep(ORDER_CHECK_INTERVAL_SEC)


async def auto_lift_loop(bot: Bot) -> None:
    logger.info("Запущен цикл автоподнятия.")
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

            client = FunPayClient(golden_key, config.get("proxy"))
            try:
                success = await asyncio.wait_for(client.raise_lots(), timeout=30.0)
            finally:
                await client.close()

            if success:
                await db.add_log("success", "Лоты подняты автоматически")
                await bot.send_message(
                    chat_id=OWNER_TELEGRAM_ID,
                    text="⬆️ <b>Лоты подняты!</b>\n\nАвтоподнятие выполнено успешно.",
                    parse_mode="HTML",
                )
            else:
                await db.add_log("warning", "Автоподнятие: нет активных лотов или ошибка")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Ошибка в автоподнятии: {e}")

        await asyncio.sleep(LIFT_INTERVAL_SEC)


async def autoresponse_loop(bot: Bot) -> None:
    logger.info("Запущен автоответчик.")
    while True:
        try:
            config = await db.get_config()
            golden_key = config.get("golden_key", "")
            autoresponse_text = await db.get_autoresponse()

            if not golden_key or not autoresponse_text:
                await asyncio.sleep(AUTORESPONSE_INTERVAL_SEC)
                continue

            client = FunPayClient(golden_key, config.get("proxy"))
            try:
                chats = await asyncio.wait_for(client.get_chats(), timeout=15.0)
            finally:
                await client.close()

            for chat in chats:
                if not chat.unread:
                    continue
                if await db.is_chat_responded(chat.chat_id):
                    continue

                send_client = FunPayClient(golden_key, config.get("proxy"))
                try:
                    sent = await asyncio.wait_for(
                        send_client.send_message(chat.chat_id, autoresponse_text),
                        timeout=10.0
                    )
                finally:
                    await send_client.close()

                if sent:
                    await db.mark_chat_responded(chat.chat_id)
                    await db.increment_stat("autoresponses")
                    await db.add_log("info", f"Автоответ → {chat.username}")
                    await bot.send_message(
                        chat_id=OWNER_TELEGRAM_ID,
                        text=(
                            f"💬 <b>Автоответ отправлен</b>\n\n"
                            f"👤 {chat.username}"
                        ),
                        parse_mode="HTML",
                    )

        except asyncio.CancelledError:
            break
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.exception(f"Ошибка в автоответчике: {e}")

        await asyncio.sleep(AUTORESPONSE_INTERVAL_SEC)


async def main() -> None:
    await db.init_db()
    logger.info("БД инициализирована.")

    bot, dp = create_bot_and_dispatcher(TELEGRAM_BOT_TOKEN)

    from tg_bot import set_bot_commands
    await set_bot_commands(bot)

    order_task        = asyncio.create_task(orders_monitor_loop(bot))
    lift_task         = asyncio.create_task(auto_lift_loop(bot))
    autoresponse_task = asyncio.create_task(autoresponse_loop(bot))

    try:
        try:
            await bot.send_message(
                chat_id=OWNER_TELEGRAM_ID,
                text=(
                    "🚀 <b>FunPay Bot Pro запущен!</b>\n\n"
                    "✅ Мониторинг заказов активен\n"
                    "✅ Автоответчик готов\n"
                    "✅ Автоподнятие настроено\n\n"
                    "Используйте /start для управления."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить стартовое уведомление: {e}")

        await db.add_log("info", "Бот запущен")
        logger.info("Запускаю polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

    finally:
        order_task.cancel()
        lift_task.cancel()
        autoresponse_task.cancel()
        await asyncio.gather(order_task, lift_task, autoresponse_task, return_exceptions=True)
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
