from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

try:
    from telegram_bot.auth import (
        BotAuthError,
        ensure_authorized_telegram_user,
        login_with_one_time_token,
        login_with_password,
        save_telegram_user_auth,
    )
except ModuleNotFoundError:
    from auth import (
        BotAuthError,
        ensure_authorized_telegram_user,
        login_with_one_time_token,
        login_with_password,
        save_telegram_user_auth,
    )

router = Router(name="auth")

AUTH_METHOD_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Войти по email/паролю")],
        [KeyboardButton(text="Войти по одноразовому токену")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)


class AuthStates(StatesGroup):
    waiting_auth_method = State()
    waiting_login = State()
    waiting_password = State()
    waiting_one_time_token = State()


async def request_auth_method(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return

    try:
        linked_user = await asyncio.to_thread(
            ensure_authorized_telegram_user, message.from_user.id
        )
        await state.clear()
        await message.answer(
            f"Вы уже авторизованы (user_id={linked_user.user_id}).\n"
            "Команды: /me, /logout"
        )
        return
    except BotAuthError:
        pass

    await state.set_state(AuthStates.waiting_auth_method)
    await message.answer(
        "Привет! Чтобы продолжить, авторизуйтесь.",
        reply_markup=AUTH_METHOD_KEYBOARD,
    )


@router.message(Command("login"))
async def handle_login_command(message: Message, state: FSMContext) -> None:
    await request_auth_method(message, state)


@router.message(AuthStates.waiting_auth_method, F.text == "Войти по email/паролю")
async def choose_email_password(message: Message, state: FSMContext) -> None:
    await state.set_state(AuthStates.waiting_login)
    await message.answer("Введите email или username:", reply_markup=ReplyKeyboardRemove())


@router.message(AuthStates.waiting_auth_method, F.text == "Войти по одноразовому токену")
async def choose_one_time_token(message: Message, state: FSMContext) -> None:
    await state.set_state(AuthStates.waiting_one_time_token)
    await message.answer("Отправьте одноразовый токен:", reply_markup=ReplyKeyboardRemove())


@router.message(AuthStates.waiting_auth_method)
async def wrong_auth_method(message: Message) -> None:
    await message.answer("Выберите способ авторизации через кнопки ниже.", reply_markup=AUTH_METHOD_KEYBOARD)


@router.message(AuthStates.waiting_login)
async def wait_login(message: Message, state: FSMContext) -> None:
    login_value = (message.text or "").strip()
    if not login_value:
        await message.answer("Логин не может быть пустым. Введите email или username.")
        return

    await state.update_data(login_value=login_value)
    await state.set_state(AuthStates.waiting_password)
    await message.answer(
        "Введите пароль:\n"
        "Внимание: Telegram не является безопасным менеджером паролей. "
        "Если возможно, используйте одноразовый токен."
    )


@router.message(AuthStates.waiting_password)
async def wait_password(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return

    password = (message.text or "").strip()
    if not password:
        await message.answer("Пароль не может быть пустым. Попробуйте еще раз.")
        return

    data = await state.get_data()
    login_value = str(data.get("login_value", "")).strip()
    if not login_value:
        await state.set_state(AuthStates.waiting_login)
        await message.answer("Сессия авторизации сброшена. Введите email или username заново.")
        return

    try:
        tokens = await asyncio.to_thread(login_with_password, login_value, password)
        await asyncio.to_thread(
            save_telegram_user_auth,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tokens=tokens,
        )
    except BotAuthError as exc:
        await message.answer(f"Ошибка авторизации: {exc}\nВведите email или username заново.")
        await state.set_state(AuthStates.waiting_login)
        return

    await state.clear()
    await message.answer("Авторизация успешна. Команды: /start, /me, /logout")


@router.message(AuthStates.waiting_one_time_token)
async def wait_one_time_token(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return

    token = (message.text or "").strip()
    if not token:
        await message.answer("Токен не может быть пустым. Отправьте одноразовый токен.")
        return

    try:
        tokens = await asyncio.to_thread(login_with_one_time_token, token)
        await asyncio.to_thread(
            save_telegram_user_auth,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tokens=tokens,
        )
    except BotAuthError as exc:
        await message.answer(f"Ошибка авторизации по токену: {exc}\nПопробуйте отправить токен снова.")
        return

    await state.clear()
    await message.answer("Авторизация успешна. Команды: /start, /me, /logout")
