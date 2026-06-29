"""
tg_bot.py — Telegram-бот управления на aiogram 3.x с FSM и клавиатурами.
"""

import logging
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

# ─── FSM-состояния ────────────────────────────────────────────────────────────

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

# ─── Клавиатуры ───────────────────────────────────────────────────────────────

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔑 Установить golden_key"), KeyboardButton(text="📋 Статус")],
            [KeyboardButton(text="📦 Товары"), KeyboardButton(text="⬆️ Автоподнятие")],
            [KeyboardButton(text="🌐 Прокси"), KeyboardButton(text="📊 Заказы")],
        ],
        resize_keyboard=True,
    )

def products_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="product_add")],
        [InlineKeyboardButton(text="📋 Список товаров", callback_data="product_list")],
        [InlineKeyboardButton(text="🗑 Удалить товар", callback_data="product_delete")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])

def autolift_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    status_text = "✅ Включено" if enabled else "❌ Выключено"
    toggle_text = "Выключить" if enabled else "Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Сейчас: {status_text}", callback_data="noop")],
        [InlineKeyboardButton(text=f"{'🔴' if enabled else '🟢'} {toggle_text}", callback_data="autolift_toggle")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])

def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )

# ─── Роутер ───────────────────────────────────────────────────────────────────

router = Router()

# ─── Команды ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Добро пожаловать в FunPay Bot!\n\n"
        "Выберите действие в меню ниже.",
        reply_markup=main_keyboard(),
    )

@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard())

# ─── Статус ───────────────────────────────────────────────────────────────────

@router.message(F.text == "📋 Статус")
async def show_status(message: Message):
    config = await db.get_config()
    golden_key = config.get("golden_key", "")
    proxy = config.get("proxy") or "не задан"
    auto_lift = "✅ Включено" if config.get("auto_lift_enabled") else "❌ Выключено"

    if golden_key:
        client = FunPayClient(golden_key, config.get("proxy"))
        try:
            username = await client.validate_golden_key()
        finally:
            await client.close()
        key_status = f"✅ Валиден (пользователь: <b>{username or 'неизвестно'}</b>)" if username else "❌ Недействителен"
    else:
        key_status = "⚠️ Не задан"

    products = await db.get_all_products()

    await message.answer(
        f"<b>📊 Статус системы</b>\n\n"
        f"🔑 Golden Key: {key_status}\n"
        f"🌐 Прокси: <code>{proxy}</code>\n"
        f"⬆️ Автоподнятие: {auto_lift}\n"
        f"📦 Товаров в базе: <b>{len(products)}</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

# ─── Golden Key ───────────────────────────────────────────────────────────────

@router.message(F.text == "🔑 Установить golden_key")
async def ask_golden_key(message: Message, state: FSMContext):
    await state.set_state(SetKeyState.waiting_for_key)
    await message.answer(
        "🔑 Введите ваш <b>golden_key</b> из cookie FunPay.\n\n"
        "Как получить: DevTools → Application → Cookies → funpay.com → golden_key",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )

@router.message(SetKeyState.waiting_for_key)
async def receive_golden_key(message: Message, state: FSMContext):
    key = message.text.strip() if message.text else ""
    if len(key) < 10:
        await message.answer("⚠️ Слишком короткий ключ. Попробуйте ещё раз.")
        return

    await message.answer("⏳ Проверяю ключ на FunPay...")
    client = FunPayClient(key)
    try:
        username = await client.validate_golden_key()
    finally:
        await client.close()

    if username:
        await db.set_golden_key(key)
        await state.clear()
        await message.answer(
            f"✅ Ключ валиден! Вы авторизованы как <b>{username}</b>.",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
    else:
        await message.answer(
            "❌ Ключ недействителен или FunPay недоступен. Проверьте и попробуйте снова.",
            reply_markup=cancel_keyboard(),
        )

# ─── Прокси ───────────────────────────────────────────────────────────────────

@router.message(F.text == "🌐 Прокси")
async def ask_proxy(message: Message, state: FSMContext):
    config = await db.get_config()
    current = config.get("proxy") or "не задан"
    await state.set_state(SetProxyState.waiting_for_proxy)
    await message.answer(
        f"🌐 Текущий прокси: <code>{current}</code>\n\n"
        "Введите прокси в формате <code>http://user:pass@host:port</code>\n"
        "или отправьте <code>clear</code> для сброса.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )

@router.message(SetProxyState.waiting_for_proxy)
async def receive_proxy(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    if text.lower() == "clear":
        await db.set_proxy(None)
        await state.clear()
        await message.answer("✅ Прокси сброшен.", reply_markup=main_keyboard())
    elif text.startswith(("http://", "https://", "socks5://")):
        await db.set_proxy(text)
        await state.clear()
        await message.answer(f"✅ Прокси установлен: <code>{text}</code>", parse_mode="HTML", reply_markup=main_keyboard())
    else:
        await message.answer("⚠️ Неверный формат. Используйте http://user:pass@host:port или 'clear'.")

# ─── Автоподнятие ─────────────────────────────────────────────────────────────

@router.message(F.text == "⬆️ Автоподнятие")
async def show_autolift(message: Message):
    config = await db.get_config()
    enabled = bool(config.get("auto_lift_enabled"))
    await message.answer(
        "⬆️ <b>Управление автоподнятием лотов</b>\n\n"
        "Лоты поднимаются каждые 2 часа автоматически.",
        parse_mode="HTML",
        reply_markup=autolift_keyboard(enabled),
    )

@router.callback_query(F.data == "autolift_toggle")
async def toggle_autolift(callback: CallbackQuery):
    config = await db.get_config()
    current = bool(config.get("auto_lift_enabled"))
    new_state = not current
    await db.set_auto_lift(new_state)
    status = "включено ✅" if new_state else "выключено ❌"
    await callback.answer(f"Автоподнятие {status}")
    await callback.message.edit_reply_markup(reply_markup=autolift_keyboard(new_state))

@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()

# ─── Товары ───────────────────────────────────────────────────────────────────

@router.message(F.text == "📦 Товары")
async def show_products_menu(message: Message):
    await message.answer(
        "📦 <b>Управление товарами</b>\n\n"
        "Каждый товар привязан к его ID на FunPay. "
        "При оплате заказа покупателю автоматически отправляется текст выдачи.",
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
        preview = p["response_text"][:40] + ("..." if len(p["response_text"]) > 40 else "")
        lines.append(
            f"🆔 <code>{p['funpay_id']}</code> | {p['category']}\n"
            f"   📝 {preview}"
        )

    text = "<b>📦 Список товаров:</b>\n\n" + "\n\n".join(lines)
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "product_add")
async def start_add_product(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddProductState.waiting_for_funpay_id)
    await callback.message.answer(
        "📦 <b>Добавление товара</b>\n\n"
        "Шаг 1/3: Введите <b>FunPay ID</b> вашего лота (число из URL лота).\n"
        "Пример: <code>123456</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()

@router.message(AddProductState.waiting_for_funpay_id)
async def receive_product_id(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    if not text.isdigit():
        await message.answer("⚠️ ID должен содержать только цифры. Попробуйте ещё раз.")
        return
    await state.update_data(funpay_id=text)
    await state.set_state(AddProductState.waiting_for_category)
    await message.answer(
        "Шаг 2/3: Введите <b>название категории</b> товара (например: <i>Аккаунты Steam</i>).",
        parse_mode="HTML",
    )

@router.message(AddProductState.waiting_for_category)
async def receive_product_category(message: Message, state: FSMContext):
    category = message.text.strip() if message.text else ""
    if not category:
        await message.answer("⚠️ Категория не может быть пустой.")
        return
    await state.update_data(category=category)
    await state.set_state(AddProductState.waiting_for_response_text)
    await message.answer(
        "Шаг 3/3: Введите <b>текст автовыдачи</b>, который будет отправлен покупателю.\n\n"
        "Поддерживаются переносы строк. Пишите так, как должен выглядеть финальный текст.",
        parse_mode="HTML",
    )

@router.message(AddProductState.waiting_for_response_text)
async def receive_product_response(message: Message, state: FSMContext):
    response_text = message.text.strip() if message.text else ""
    if not response_text:
        await message.answer("⚠️ Текст выдачи не может быть пустым.")
        return

    data = await state.get_data()
    funpay_id = data["funpay_id"]
    category = data["category"]

    await db.add_or_update_product(funpay_id, category, response_text)
    await state.clear()
    await message.answer(
        f"✅ Товар <code>{funpay_id}</code> ({category}) успешно сохранён!\n\n"
        f"📝 Текст выдачи:\n<i>{response_text[:200]}</i>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

@router.callback_query(F.data == "product_delete")
async def start_delete_product(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DeleteProductState.waiting_for_funpay_id)
    await callback.message.answer(
        "🗑 Введите <b>FunPay ID</b> товара, который нужно удалить:",
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
        await message.answer(f"✅ Товар <code>{text}</code> удалён.", parse_mode="HTML", reply_markup=main_keyboard())
    else:
        await message.answer(f"⚠️ Товар с ID <code>{text}</code> не найден.", parse_mode="HTML", reply_markup=main_keyboard())

# ─── Заказы ───────────────────────────────────────────────────────────────────

@router.message(F.text == "📊 Заказы")
async def show_orders_info(message: Message):
    config = await db.get_config()
    golden_key = config.get("golden_key", "")
    if not golden_key:
        await message.answer("⚠️ Golden Key не задан. Установите его сначала.")
        return

    await message.answer("⏳ Запрашиваю актуальные заказы с FunPay...")
    client = FunPayClient(golden_key, config.get("proxy"))
    try:
        orders = await client.get_paid_orders()
    finally:
        await client.close()

    if not orders:
        await message.answer("📭 Оплаченных заказов не найдено.")
        return

    lines = []
    for o in orders[:10]:  # показываем последние 10
        lines.append(
            f"📦 Заказ <code>{o.order_id}</code>\n"
            f"   👤 Покупатель: <b>{o.buyer_username}</b>\n"
            f"   🎁 Товар ID: <code>{o.product_funpay_id or 'N/A'}</code>"
        )
    await message.answer(
        f"<b>📊 Последние оплаченные заказы ({len(orders)}):</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
    )

# ─── Навигация ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Главное меню:", reply_markup=main_keyboard())
    await callback.answer()

# ─── Фабрика бота ─────────────────────────────────────────────────────────────

def create_bot_and_dispatcher(token: str) -> tuple[Bot, Dispatcher]:
    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return bot, dp
