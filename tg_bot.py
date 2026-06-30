"""
tg_bot.py — Telegram-бот управления FunPay Bot Pro
"""

import logging
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import database as db
from funpay_client import FunPayClient

logger = logging.getLogger(__name__)

BOT_VERSION = "1.0.0"

# ─── FSM ──────────────────────────────────────────────────────────────────────

class SetKeyState(StatesGroup):
    waiting_for_key = State()

class AddProductState(StatesGroup):
    waiting_for_funpay_id = State()
    waiting_for_category = State()
    waiting_for_response_text = State()

class SetProxyState(StatesGroup):
    waiting_for_proxy = State()

class DeleteProductState(StatesGroup):
    waiting_for_funpay_id = State()

class SetAutoresponseState(StatesGroup):
    waiting_for_text = State()

# ─── Клавиатуры ───────────────────────────────────────────────────────────────

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="📋 Логи")],
            [KeyboardButton(text="🔑 Golden Key"), KeyboardButton(text="🌐 Прокси")],
            [KeyboardButton(text="📦 Товары"), KeyboardButton(text="💬 Автоответчик")],
            [KeyboardButton(text="⬆️ Автоподнятие"), KeyboardButton(text="📈 Заказы")],
            [KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
    )

def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )

def products_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="product_add")],
        [InlineKeyboardButton(text="📋 Список товаров", callback_data="product_list")],
        [InlineKeyboardButton(text="🗑 Удалить товар", callback_data="product_delete")],
    ])

def autolift_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    toggle = "🔴 Выключить" if enabled else "🟢 Включить"
    status = "✅ Включено" if enabled else "❌ Выключено"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Статус: {status}", callback_data="noop")],
        [InlineKeyboardButton(text=toggle, callback_data="autolift_toggle")],
    ])

# ─── Роутер ───────────────────────────────────────────────────────────────────

router = Router()

# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    config = await db.get_config()
    has_key = bool(config.get("golden_key"))
    products = await db.get_all_products()
    orders = await db.get_all_orders()
    delivered = [o for o in orders if o["status"] == "delivered"]

    key_status = "✅ Настроен" if has_key else "⚠️ Не задан"
    proxy_status = "✅ Задан" if config.get("proxy") else "➖ Не задан"
    lift_status = "✅ Вкл" if config.get("auto_lift_enabled") else "❌ Выкл"
    ar_text = await db.get_autoresponse()
    ar_status = "✅ Вкл" if ar_text else "❌ Выкл"

    await message.answer(
        f"╔══════════════════════╗\n"
        f"║  🤖 <b>FunPay Bot Pro</b> v{BOT_VERSION}  ║\n"
        f"╚══════════════════════╝\n\n"
        f"<b>Статус системы:</b>\n"
        f"🔑 Golden Key: {key_status}\n"
        f"🌐 Прокси: {proxy_status}\n"
        f"⬆️ Автоподнятие: {lift_status}\n"
        f"💬 Автоответчик: {ar_status}\n\n"
        f"<b>Статистика:</b>\n"
        f"📦 Товаров: <b>{len(products)}</b>\n"
        f"✅ Выдано заказов: <b>{len(delivered)}</b>\n\n"
        f"Выберите действие в меню 👇",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

# ─── /help ────────────────────────────────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📋 Главное меню:", reply_markup=main_keyboard())


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Справка по FunPay Bot Pro</b>\n\n"
        "<b>🔑 Golden Key</b>\n"
        "Ключ авторизации с FunPay. Получить: браузер → F12 → Application → Cookies → funpay.com → golden_key\n\n"
        "<b>📦 Товары</b>\n"
        "Добавьте товары для автовыдачи. ID товара — число из URL лота на FunPay (funpay.com/lots/offer?id=<b>ЧИСЛО</b>)\n\n"
        "<b>✅ Автовыдача</b>\n"
        "При оплате заказа бот автоматически отправит текст выдачи покупателю в чат FunPay и уведомит вас.\n\n"
        "<b>💬 Автоответчик</b>\n"
        "Автоматически отвечает на все новые сообщения заданным текстом. Каждому пользователю — один раз.\n\n"
        "<b>⬆️ Автоподнятие</b>\n"
        "Поднимает ваши лоты каждые 2 часа автоматически.\n\n"
        "<b>📋 Логи</b>\n"
        "Последние события: выдачи, автоответы, ошибки.\n\n"
        "<b>📈 Заказы</b>\n"
        "Текущие оплаченные заказы с FunPay.\n\n"
        "По вопросам: @saburovhoji_dev",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard())

@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📋 Главное меню:", reply_markup=main_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Справка по FunPay Bot</b>\n\n"
        "<b>🔑 Установить golden_key</b>\n"
        "Подключает бота к вашему аккаунту FunPay. Ключ берётся из cookies браузера.\n\n"
        "<b>📦 Товары</b>\n"
        "Привязка текста автовыдачи к ID лота. После оплаты заказа покупатель "
        "автоматически получает указанный текст в чат.\n\n"
        "<b>💬 Автоответчик</b>\n"
        "Автоматически отвечает заданным текстом каждому новому собеседнику в чатах.\n\n"
        "<b>⬆️ Автоподнятие</b>\n"
        "Поднимает все ваши лоты каждые 2 часа автоматически.\n\n"
        "<b>🌐 Прокси</b>\n"
        "Опциональная настройка для работы через прокси-сервер.\n\n"
        "<b>📊 Заказы</b>\n"
        "Показывает последние оплаченные заказы с FunPay.\n\n"
        "<b>📋 Статус</b>\n"
        "Полная информация о состоянии бота и статистика работы.\n\n"
        "❓ Если что-то не работает — проверьте 📋 Статус и убедитесь, "
        "что golden_key актуален.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

# ─── Статус ───────────────────────────────────────────────────────────────────

@router.message(F.text == "📊 Статус")
async def show_status(message: Message):
    config = await db.get_config()
    golden_key = config.get("golden_key", "")
    proxy = config.get("proxy") or "не задан"
    auto_lift = "✅ Включено" if config.get("auto_lift_enabled") else "❌ Выключено"
    ar_text = await db.get_autoresponse()
    ar_status = "✅ Включён" if ar_text else "❌ Выключен"

    products = await db.get_all_products()
    orders = await db.get_all_orders()
    delivered = [o for o in orders if o["status"] == "delivered"]
    failed = [o for o in orders if o["status"] == "send_failed"]
    no_product = [o for o in orders if o["status"] == "no_product"]

    key_line = f"✅ <code>{golden_key[:12]}...</code>" if golden_key else "⚠️ Не задан"

    await message.answer(
        f"<b>📊 Статус системы</b>\n"
        f"{'─' * 25}\n"
        f"🔑 Golden Key: {key_line}\n"
        f"🌐 Прокси: <code>{proxy[:40]}</code>\n"
        f"⬆️ Автоподнятие: {auto_lift}\n"
        f"💬 Автоответчик: {ar_status}\n"
        f"{'─' * 25}\n"
        f"<b>📈 Статистика заказов:</b>\n"
        f"📦 Товаров в базе: <b>{len(products)}</b>\n"
        f"✅ Выдано успешно: <b>{len(delivered)}</b>\n"
        f"⚠️ Без товара: <b>{len(no_product)}</b>\n"
        f"❌ Ошибки отправки: <b>{len(failed)}</b>\n"
        f"{'─' * 25}\n"
        f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

# ─── Логи ─────────────────────────────────────────────────────────────────────

@router.message(F.text == "📋 Логи")
async def show_logs(message: Message):
    orders = await db.get_all_orders()
    logs = await db.get_logs(limit=15)

    if not logs:
        await message.answer("📋 Логов пока нет.", reply_markup=main_keyboard())
        return

    lines = []
    for log in logs:
        icon = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}.get(log["level"], "•")
        lines.append(f"{icon} <code>{log['created_at']}</code>\n   {log['message']}")

    await message.answer(
        f"<b>📋 Последние события:</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

# ─── Golden Key ───────────────────────────────────────────────────────────────

@router.message(F.text == "🔑 Golden Key")
async def ask_golden_key(message: Message, state: FSMContext):
    await state.set_state(SetKeyState.waiting_for_key)
    await message.answer(
        "🔑 <b>Установка Golden Key</b>\n\n"
        "Как получить:\n"
        "1. Зайдите на funpay.com\n"
        "2. Нажмите F12 → Application\n"
        "3. Cookies → https://funpay.com\n"
        "4. Скопируйте значение <b>golden_key</b>\n\n"
        "Введите ключ:",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )

@router.message(SetKeyState.waiting_for_key)
async def receive_golden_key(message: Message, state: FSMContext):
    key = message.text.strip() if message.text else ""
    if len(key) < 10:
        await message.answer("⚠️ Слишком короткий ключ. Попробуйте ещё раз.")
        return

    await db.set_golden_key(key)
    await state.clear()
    await db.add_log("info", f"Golden Key обновлён: {key[:8]}...")

    config = await db.get_config()
    proxy = config.get("proxy")

    await message.answer(
        f"✅ Ключ сохранён!\n⏳ Проверяю соединение с FunPay...",
        reply_markup=main_keyboard(),
    )

    try:
        client = FunPayClient(key, proxy)
        try:
            username = await asyncio.wait_for(client.validate_golden_key(), timeout=15.0)
        finally:
            await client.close()

        if username:
            await db.add_log("success", f"Авторизован как {username}")
            await message.answer(f"✅ Авторизован как <b>{username}</b>!", parse_mode="HTML")
        else:
            await db.add_log("warning", "FunPay вернул неожиданный ответ при проверке ключа")
            await message.answer("⚠️ Ключ сохранён, но FunPay вернул неожиданный ответ. Возможно нужен прокси.")
    except asyncio.TimeoutError:
        await db.add_log("warning", "FunPay не ответил за 15 сек при проверке ключа")
        await message.answer("⚠️ Ключ сохранён. FunPay не ответил за 15 сек — попробуйте установить прокси.")
    except Exception as e:
        await message.answer(f"⚠️ Ключ сохранён. Ошибка проверки: {e}")

# ─── Прокси ───────────────────────────────────────────────────────────────────

@router.message(F.text == "🌐 Прокси")
async def ask_proxy(message: Message, state: FSMContext):
    config = await db.get_config()
    current = config.get("proxy") or "не задан"
    await state.set_state(SetProxyState.waiting_for_proxy)
    await message.answer(
        f"🌐 <b>Настройка прокси</b>\n\n"
        f"Текущий: <code>{current}</code>\n\n"
        f"Формат: <code>http://user:pass@host:port</code>\n"
        f"Или отправьте <code>clear</code> для сброса.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )

@router.message(SetProxyState.waiting_for_proxy)
async def receive_proxy(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    if text.lower() == "clear":
        await db.set_proxy(None)
        await db.add_log("info", "Прокси сброшен")
        await state.clear()
        await message.answer("✅ Прокси сброшен.", reply_markup=main_keyboard())
    elif text.startswith(("http://", "https://", "socks5://")):
        await db.set_proxy(text)
        await db.add_log("info", f"Прокси установлен: {text[:30]}...")
        await state.clear()
        await message.answer(f"✅ Прокси установлен!", reply_markup=main_keyboard())
    else:
        await message.answer("⚠️ Неверный формат. Используйте http://user:pass@host:port")

# ─── Автоподнятие ─────────────────────────────────────────────────────────────

@router.message(F.text == "⬆️ Автоподнятие")
async def show_autolift(message: Message):
    config = await db.get_config()
    enabled = bool(config.get("auto_lift_enabled"))
    await message.answer(
        "⬆️ <b>Автоподнятие лотов</b>\n\nЛоты поднимаются каждые 2 часа автоматически.",
        parse_mode="HTML",
        reply_markup=autolift_keyboard(enabled),
    )

@router.callback_query(F.data == "autolift_toggle")
async def toggle_autolift(callback: CallbackQuery):
    config = await db.get_config()
    new_state = not bool(config.get("auto_lift_enabled"))
    await db.set_auto_lift(new_state)
    status = "включено" if new_state else "выключено"
    await db.add_log("info", f"Автоподнятие {status}")
    await callback.answer(f"Автоподнятие {status} ✅")
    await callback.message.edit_reply_markup(reply_markup=autolift_keyboard(new_state))

@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()

# ─── Товары ───────────────────────────────────────────────────────────────────

@router.message(F.text == "📦 Товары")
async def show_products_menu(message: Message):
    products = await db.get_all_products()
    count = len(products)
    await message.answer(
        f"📦 <b>Управление товарами</b>\n\nТоваров в базе: <b>{count}</b>",
        parse_mode="HTML",
        reply_markup=products_keyboard(),
    )

@router.callback_query(F.data == "product_list")
async def list_products(callback: CallbackQuery):
    products = await db.get_all_products()
    if not products:
        await callback.answer("Товаров пока нет.", show_alert=True)
        return
    lines = []
    for p in products:
        preview = p["response_text"][:50] + ("..." if len(p["response_text"]) > 50 else "")
        lines.append(
            f"🆔 <code>{p['funpay_id']}</code> — {p['category']}\n"
            f"   📝 {preview}"
        )
    await callback.message.answer(
        "<b>📦 Список товаров:</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data == "product_add")
async def start_add_product(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddProductState.waiting_for_funpay_id)
    await callback.message.answer(
        "📦 <b>Добавление товара</b>\n\n"
        "Шаг 1/3: Введите <b>FunPay ID</b> лота\n"
        "Пример URL: funpay.com/lots/offer?id=<b>123456</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()

@router.message(AddProductState.waiting_for_funpay_id)
async def receive_product_id(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    if not text.isdigit():
        await message.answer("⚠️ ID должен содержать только цифры.")
        return
    await state.update_data(funpay_id=text)
    await state.set_state(AddProductState.waiting_for_category)
    await message.answer("Шаг 2/3: Введите <b>категорию</b> товара (например: Аккаунты Steam)", parse_mode="HTML")

@router.message(AddProductState.waiting_for_category)
async def receive_product_category(message: Message, state: FSMContext):
    category = message.text.strip() if message.text else ""
    if not category:
        await message.answer("⚠️ Категория не может быть пустой.")
        return
    await state.update_data(category=category)
    await state.set_state(AddProductState.waiting_for_response_text)
    await message.answer(
        "Шаг 3/3: Введите <b>текст автовыдачи</b>\n\n"
        "Этот текст получит покупатель сразу после оплаты.",
        parse_mode="HTML",
    )

@router.message(AddProductState.waiting_for_response_text)
async def receive_product_response(message: Message, state: FSMContext):
    response_text = message.text.strip() if message.text else ""
    if not response_text:
        await message.answer("⚠️ Текст выдачи не может быть пустым.")
        return
    data = await state.get_data()
    await db.add_or_update_product(data["funpay_id"], data["category"], response_text)
    await db.add_log("info", f"Добавлен товар ID {data['funpay_id']} ({data['category']})")
    await state.clear()
    await message.answer(
        f"✅ Товар <code>{data['funpay_id']}</code> (<b>{data['category']}</b>) добавлен!\n\n"
        f"📝 Текст выдачи сохранён.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

@router.callback_query(F.data == "product_delete")
async def start_delete_product(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DeleteProductState.waiting_for_funpay_id)
    await callback.message.answer(
        "🗑 Введите <b>FunPay ID</b> товара для удаления:",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()

@router.message(DeleteProductState.waiting_for_funpay_id)
async def receive_delete_id(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    if not text.isdigit():
        await message.answer("⚠️ ID должен содержать только цифры.")
        return
    deleted = await db.delete_product(text)
    await state.clear()
    if deleted:
        await db.add_log("info", f"Удалён товар ID {text}")
        await message.answer(f"✅ Товар <code>{text}</code> удалён.", parse_mode="HTML", reply_markup=main_keyboard())
    else:
        await message.answer(f"⚠️ Товар с ID <code>{text}</code> не найден.", parse_mode="HTML", reply_markup=main_keyboard())

# ─── Автоответчик ─────────────────────────────────────────────────────────────

@router.message(F.text == "💬 Автоответчик")
async def show_autoresponse(message: Message):
    text = await db.get_autoresponse()
    if text:
        preview = text[:150] + ("..." if len(text) > 150 else "")
        status = f"✅ <b>Включён</b>\n\n📝 Текст:\n<i>{preview}</i>"
    else:
        status = "❌ <b>Выключен</b>"
    await message.answer(
        f"💬 <b>Автоответчик</b>\n\n{status}\n\n"
        f"Отвечает всем новым собеседникам один раз.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Установить текст", callback_data="autoresponse_set")],
            [InlineKeyboardButton(text="🗑 Выключить", callback_data="autoresponse_clear")],
        ]),
    )

@router.callback_query(F.data == "autoresponse_set")
async def start_set_autoresponse(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SetAutoresponseState.waiting_for_text)
    await callback.message.answer(
        "💬 Введите текст автоответа:\n\n"
        "Пример: <i>Здравствуйте! Ваш заказ будет выдан автоматически после оплаты. По вопросам пишите сюда.</i>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()

@router.message(SetAutoresponseState.waiting_for_text)
async def receive_autoresponse_text(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    if not text:
        await message.answer("⚠️ Текст не может быть пустым.")
        return
    await db.set_autoresponse(text)
    await db.add_log("info", "Автоответчик включён")
    await state.clear()
    await message.answer(
        f"✅ Автоответчик включён!\n\n📝 <i>{text[:200]}</i>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

@router.callback_query(F.data == "autoresponse_clear")
async def clear_autoresponse(callback: CallbackQuery):
    await db.set_autoresponse(None)
    await db.add_log("info", "Автоответчик выключен")
    await callback.answer("Автоответчик выключен.")
    await callback.message.edit_text("💬 <b>Автоответчик</b>\n\n❌ Выключен", parse_mode="HTML")

# ─── Заказы ───────────────────────────────────────────────────────────────────

@router.message(F.text == "📈 Заказы")
async def show_orders(message: Message):
    config = await db.get_config()
    golden_key = config.get("golden_key", "")
    if not golden_key:
        await message.answer("⚠️ Сначала установите Golden Key.")
        return

    await message.answer("⏳ Запрашиваю заказы с FunPay...")
    client = FunPayClient(golden_key, config.get("proxy"))
    try:
        orders = await asyncio.wait_for(client.get_paid_orders(), timeout=15.0)
    except asyncio.TimeoutError:
        await message.answer("⚠️ FunPay не ответил за 15 секунд.")
        return
    finally:
        await client.close()

    if not orders:
        await message.answer("📭 Оплаченных заказов не найдено.")
        return

    lines = []
    for o in orders[:10]:
        lines.append(
            f"📦 <code>{o.order_id}</code>\n"
            f"   👤 {o.buyer_username} | 🎁 ID: <code>{o.product_funpay_id or 'N/A'}</code>"
        )
    await message.answer(
        f"<b>📈 Оплаченные заказы ({len(orders)}):</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


def create_bot_and_dispatcher(token: str) -> tuple[Bot, Dispatcher]:
    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return bot, dp


async def set_bot_commands(bot: Bot) -> None:
    """Регистрирует команды бота в меню Telegram (значок '/' рядом с полем ввода)."""
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить / перезапустить бота"),
        BotCommand(command="menu", description="Открыть главное меню"),
        BotCommand(command="help", description="Справка по функциям"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ])
