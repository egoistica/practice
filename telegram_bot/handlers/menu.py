from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

try:
    from telegram_bot.auth import BotAuthError, ensure_authorized_telegram_user
    from telegram_bot.handlers.auth import request_auth_method
except ModuleNotFoundError:
    from auth import BotAuthError, ensure_authorized_telegram_user
    from handlers.auth import request_auth_method

router = Router(name="menu")

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📤 Загрузить"), KeyboardButton(text="📖 История")],
        [KeyboardButton(text="⭐ Избранное"), KeyboardButton(text="💰 Мой баланс")],
        [KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True,
)

MENU_INLINE_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📤 Загрузить", callback_data="menu:upload")],
        [InlineKeyboardButton(text="📖 История", callback_data="menu:history")],
        [InlineKeyboardButton(text="⭐ Избранное", callback_data="menu:favourites")],
        [InlineKeyboardButton(text="💰 Мой баланс", callback_data="menu:balance")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help")],
    ]
)


async def send_main_menu(message: Message, *, welcome: bool = False) -> None:
    prefix = "Главное меню:\n" if welcome else ""
    await message.answer(
        prefix
        + "Выберите действие кнопками ниже или используйте /help.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )


async def _require_authorized_or_start_auth(
    message: Message, state: FSMContext
) -> bool:
    if message.from_user is None:
        return False
    try:
        await asyncio.to_thread(ensure_authorized_telegram_user, message.from_user.id)
        return True
    except BotAuthError:
        await request_auth_method(message, state)
        return False


async def _send_section(message: Message, title: str, details: str) -> None:
    await message.answer(
        f"{title}\n{details}",
        reply_markup=MENU_INLINE_KEYBOARD,
    )


@router.message(Command("start"))
async def handle_start(message: Message, state: FSMContext) -> None:
    if not await _require_authorized_or_start_auth(message, state):
        return
    await state.clear()
    await send_main_menu(message, welcome=True)


@router.message(Command("help"))
async def handle_help(message: Message, state: FSMContext) -> None:
    if not await _require_authorized_or_start_auth(message, state):
        return
    await message.answer(
        "Доступные команды:\n"
        "/start — открыть главное меню\n"
        "/help — показать справку\n"
        "/me — профиль\n"
        "/logout — выйти из аккаунта\n\n"
        "Также можно использовать кнопки меню.",
        reply_markup=MENU_INLINE_KEYBOARD,
    )


@router.message(F.text == "📤 Загрузить")
async def handle_upload(message: Message, state: FSMContext) -> None:
    if not await _require_authorized_or_start_auth(message, state):
        return
    await _send_section(
        message,
        "📤 Загрузить",
        "Отправьте ссылку или файл. Полная интеграция будет подключена следующим шагом.",
    )


@router.message(F.text == "📖 История")
async def handle_history(message: Message, state: FSMContext) -> None:
    if not await _require_authorized_or_start_auth(message, state):
        return
    await _send_section(
        message,
        "📖 История",
        "Здесь будет история ваших лекций. Команда уже подключена к меню.",
    )


@router.message(F.text == "⭐ Избранное")
async def handle_favourites(message: Message, state: FSMContext) -> None:
    if not await _require_authorized_or_start_auth(message, state):
        return
    await _send_section(
        message,
        "⭐ Избранное",
        "Здесь будут лекции, добавленные в избранное.",
    )


@router.message(F.text == "💰 Мой баланс")
async def handle_balance(message: Message, state: FSMContext) -> None:
    if not await _require_authorized_or_start_auth(message, state):
        return
    await _send_section(
        message,
        "💰 Мой баланс",
        "Баланс токенов будет показан после подключения endpoint'а баланса.",
    )


@router.message(F.text == "❓ Помощь")
async def handle_help_button(message: Message, state: FSMContext) -> None:
    await handle_help(message, state)


@router.callback_query(F.data == "menu:upload")
async def callback_upload(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer("📤 Раздел загрузки открыт.")
    await callback.answer()


@router.callback_query(F.data == "menu:history")
async def callback_history(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer("📖 Раздел истории открыт.")
    await callback.answer()


@router.callback_query(F.data == "menu:favourites")
async def callback_favourites(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer("⭐ Раздел избранного открыт.")
    await callback.answer()


@router.callback_query(F.data == "menu:balance")
async def callback_balance(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer("💰 Раздел баланса открыт.")
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def callback_help(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer("❓ Используйте /help для списка команд.")
    await callback.answer()
