"""
tg_bot.py — Telegram-бот управления на aiogram 3.x с FSM и клавиатурами.
"""

import logging
import asyncio
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


router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Добро пожаловать в FunPay Bot!\n\nВыберите действие в меню ниже.",
        reply_markup=main_keyboard(),
    )

@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard())


@router.message(F.text == "📋 Статус")
async def show_status(message: Message):
    config = await db.get_config()
    golden_key = config.get("golden_key", "")
    proxy = config.get("proxy") or "не задан"
    auto_lift = "✅ Включено" if config.get("auto_lift_enabled") else "❌ Выключено"
    db_path = db.DB_PATH

    if golden_key:
        key_status = f"✅ Задан (<code>{golden_key[:8]}...</code>)"
    else:
        key_status = "⚠️ Не задан"

    products = await db.get_all_products()

    await message.answer(
        f"<b>📊 Статус системы</b>\n\n"
        f"🔑 Golden Key: {key_status}\n"
        f"🌐 Прокси: <code>{proxy}</code>\n"
        f"⬆️ Автоподнятие: {auto_lift}\n"
        f"📦 Товаров в базе: <b>{len(products)}</b>\n"
        f"💾 БД: <code>{db_path}</code>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


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

    # Сохраняем ключ сразу, без проверки на FunPay
    await db.set_golden_key(key)
    await state.clear()

    config = await db.get_config()
    proxy = config.get("proxy")

    await message.answer(
        f"✅ Ключ сохранён: <code>{key[:8]}...</code>\n\n"
        f"⏳ Проверяю соединение с FunPay...",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

    # Проверяем в фоне с таймаутом
    try:
        client = FunPayClient(key, proxy)
        try:
            username = await asyncio.wait_for(client.validate_golden_key(), timeout=15.0)
        finally:
            await client.close()

        if username:
            await message.answer(
                f"✅ FunPay отвечает! Авторизован как <b>{username}</b>.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "⚠️ Ключ сохранён, но FunPay вернул неожиданный ответ.\n"
                "Возможно ключ устарел или нужен прокси.",
            )
    except asyncio.TimeoutError:
        await message.answer(
            "⚠️ Ключ сохранён, но FunPay не ответил за 15 секунд.\n"
            "Попробуйте установить прокси через 🌐 Прокси.",
        )
    except Exception as e:
        await message.answer(f"⚠️ Ключ сохранён. Ошибка проверки: {e}")


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


@router.message(F.text == "⬆️ Автоподнятие")
async def show_autolift(message: Message):
    config = await db.get_config()
    enabled = bool(config.get("auto_lift_enabled"))
    await message.answer(
        "⬆️ <b>Управление автоподнятием лотов</b>\n\nЛоты поднимаются каждые 2 часа.",
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


@router.message(F.text == "📦 Товары")
async def show_products_menu(message: Message):
    await message.answer(
        "📦 <b>Управление товарами</b>",
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
        lines.append(f"🆔 <code>{p['funpay_id']}</code> | {p['category']}\n   📝 {preview}")
    await callback.message.answer("<b>📦 Список товаров:</b>\n\n" + "\n\n".join(lines), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "product_add")
async def start_add_product(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddProductState.waiting_for_funpay_id)
    await callback.message.answer(
        "📦 Шаг 1/3: Введите <b>FunPay ID</b> лота (число из URL).",
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
    await message.answer("Шаг 2/3: Введите <b>название категории</b>.", parse_mode="HTML")

@router.message(AddProductState.waiting_for_category)
async def receive_product_category(message: Message, state: FSMContext):
    category = message.text.strip() if message.text else ""
    if not category:
        await message.answer("⚠️ Категория не может быть пустой.")
        return
    await state.update_data(category=category)
    await state.set_state(AddProductState.waiting_for_response_text)
    await message.answer("Шаг 3/3: Введите <b>текст автовыдачи</b>.", parse_mode="HTML")

@router.message(AddProductState.waiting_for_response_text)
async def receive_product_response(message: Message, state: FSMContext):
    response_text = message.text.strip() if message.text else ""
    if not response_text:
        await message.answer("⚠️ Текст выдачи не может быть пустым.")
        return
    data = await state.get_data()
    await db.add_or_update_product(data["funpay_id"], data["category"], response_text)
    await state.clear()
    await message.answer(
        f"✅ Товар <code>{data['funpay_id']}</code> сохранён!",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

@router.callback_query(F.data == "product_delete")
async def start_delete_product(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DeleteProductState.waiting_for_funpay_id)
    await callback.message.answer("🗑 Введите <b>FunPay ID</b> товара для удаления:", parse_mode="HTML", reply_markup=cancel_keyboard())
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


@router.message(F.text == "📊 Заказы")
async def show_orders_info(message: Message):
    config = await db.get_config()
    golden_key = config.get("golden_key", "")
    if not golden_key:
        await message.answer("⚠️ Golden Key не задан.")
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
        lines.append(f"📦 <code>{o.order_id}</code> | 👤 <b>{o.buyer_username}</b>")
    await message.answer("<b>📊 Последние заказы:</b>\n\n" + "\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Главное меню:", reply_markup=main_keyboard())
    await callback.answer()


def create_bot_and_dispatcher(token: str) -> tuple[Bot, Dispatcher]:
    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return bot, dp
