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


async def _require_authorized_callback(callback: CallbackQuery) -> bool:
    if callback.from_user is None:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return False
    try:
        await asyncio.to_thread(ensure_authorized_telegram_user, callback.from_user.id)
        return True
    except BotAuthError:
        await callback.answer("Сессия истекла. Используйте /start.", show_alert=True)
        return False


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


@router.message(F.text == "❓ Помощь")
async def handle_help_button(message: Message, state: FSMContext) -> None:
    await handle_help(message, state)


@router.callback_query(F.data == "menu:help")
async def callback_help(callback: CallbackQuery) -> None:
    if not await _require_authorized_callback(callback):
        return
    if callback.message:
        await callback.message.answer("❓ Используйте /help для списка команд.")
    await callback.answer()
