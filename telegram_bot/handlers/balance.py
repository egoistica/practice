from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import requests
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

try:
    from telegram_bot.auth import BotAuthError, ensure_authorized_telegram_user
    from telegram_bot.handlers.auth import request_auth_method
    from telegram_bot.utils import get_api_base_url
except ModuleNotFoundError:
    from auth import BotAuthError, ensure_authorized_telegram_user
    from handlers.auth import request_auth_method
    from utils import get_api_base_url

router = Router(name="balance")

REQUEST_TIMEOUT_SECONDS = 40


class BotAPIError(RuntimeError):
    pass


def _api_get_balance(token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_api_base_url().rstrip('/')}/tokens/balance",
        headers={"Authorization": f"Bearer {token}"},
        params={"include_transactions": "true", "transactions_limit": 5},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code in (401, 403):
        raise BotAuthError(f"Failed to load balance (HTTP {response.status_code})")
    if response.status_code >= 400:
        body = response.text.strip() or "<empty>"
        raise BotAPIError(
            f"Failed to load balance (HTTP {response.status_code}): {body}"
        )
    return response.json()


def _format_dt(raw: Any) -> str:
    if not isinstance(raw, str):
        return "n/a"
    value = raw.strip()
    if not value:
        return "n/a"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d.%m %H:%M")
    except ValueError:
        return value[:16]


def _format_balance_text(payload: dict[str, Any]) -> str:
    balance = int(payload.get("balance", 0) or 0)
    lines = [f"Ваш баланс: {balance} токенов 💰"]

    transactions = payload.get("transactions")
    if not isinstance(transactions, list) or not transactions:
        return "\n".join(lines)

    lines.append("")
    lines.append("Последние 5 транзакций:")
    for item in transactions[:5]:
        if not isinstance(item, dict):
            continue
        amount = int(item.get("amount", 0) or 0)
        reason = str(item.get("reason", "")).strip() or "Без причины"
        created_at = _format_dt(item.get("created_at"))
        sign = "+" if amount > 0 else ""
        lines.append(f"• {created_at}: {sign}{amount} ({reason})")
    return "\n".join(lines)


async def _authorized_token_or_start_auth(
    message: Message,
    state: FSMContext,
    *,
    telegram_id: int,
) -> str | None:
    try:
        linked_user = await asyncio.to_thread(ensure_authorized_telegram_user, telegram_id)
        return linked_user.jwt_token
    except BotAuthError:
        await request_auth_method(message, state, telegram_id=telegram_id)
        return None


async def _send_balance(
    message: Message,
    *,
    token: str,
) -> None:
    payload = await asyncio.to_thread(_api_get_balance, token)
    await message.answer(_format_balance_text(payload))


@router.message(Command("balance"))
@router.message(F.text == "💰 Мой баланс")
@router.message(F.text == "💰 Баланс")
async def handle_balance(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    token = await _authorized_token_or_start_auth(message, state, telegram_id=message.from_user.id)
    if token is None:
        return
    try:
        await _send_balance(message, token=token)
    except BotAuthError:
        await request_auth_method(message, state, telegram_id=message.from_user.id)
    except BotAPIError:
        await message.answer("Сервис баланса временно недоступен. Попробуйте позже.")
    except Exception:
        await message.answer("Не удалось получить баланс. Попробуйте позже.")


@router.callback_query(F.data == "menu:balance")
async def callback_balance(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    token = await _authorized_token_or_start_auth(
        callback.message,
        state,
        telegram_id=callback.from_user.id,
    )
    if token is None:
        await callback.answer("Нужна авторизация.", show_alert=True)
        return
    await callback.answer()
    try:
        await _send_balance(callback.message, token=token)
    except BotAuthError:
        await request_auth_method(
            callback.message,
            state,
            telegram_id=callback.from_user.id,
        )
    except BotAPIError:
        await callback.message.answer("Сервис баланса временно недоступен. Попробуйте позже.")
    except Exception:
        await callback.message.answer("Не удалось получить баланс. Попробуйте позже.")
