from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.types import Message

try:
    from telegram_bot.auth import (
        BotAuthError,
        clear_telegram_user_auth,
        ensure_authorized_telegram_user,
        fetch_profile,
    )
except ModuleNotFoundError:
    from auth import BotAuthError, clear_telegram_user_auth, ensure_authorized_telegram_user, fetch_profile

router = Router(name="common")


@router.message(StateFilter(None), Command("me"))
async def handle_me(message: Message) -> None:
    if message.from_user is None:
        return
    try:
        linked_user = ensure_authorized_telegram_user(message.from_user.id)
        profile = fetch_profile(linked_user.jwt_token)
    except BotAuthError as exc:
        await message.answer(f"Не авторизован: {exc}")
        return

    await message.answer(
        "Профиль:\n"
        f"- user_id: {profile.get('user_id')}\n"
        f"- username: {profile.get('username')}\n"
        f"- email: {profile.get('email')}\n"
        f"- is_admin: {profile.get('is_admin')}"
    )


@router.message(StateFilter(None), Command("logout"))
async def handle_logout(message: Message) -> None:
    if message.from_user is None:
        return
    clear_telegram_user_auth(message.from_user.id)
    await message.answer("Сессия очищена. Используйте /start для повторной авторизации.")


@router.message(StateFilter(None))
async def handle_message(message: Message) -> None:
    if message.from_user is None:
        return
    try:
        ensure_authorized_telegram_user(message.from_user.id)
    except BotAuthError as exc:
        await message.answer(f"{exc}\nЗапустите /start для авторизации.")
        return

    text = (message.text or "").strip()
    if text:
        await message.answer(f"Получено сообщение: {text}")
        return
    await message.answer("Получено сообщение (не текстовый формат).")
