from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import requests
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

try:
    from telegram_bot.auth import BotAuthError, ensure_authorized_telegram_user
    from telegram_bot.handlers.auth import request_auth_method
    from telegram_bot.utils import get_api_base_url
except ModuleNotFoundError:
    from auth import BotAuthError, ensure_authorized_telegram_user
    from handlers.auth import request_auth_method
    from utils import get_api_base_url

router = Router(name="history")

REQUEST_TIMEOUT_SECONDS = 40
PAGE_LIMIT = 10


def _truncate(text: str, limit: int) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return f"{value[: limit - 3]}..."


def _api_get_history(token: str, *, skip: int, limit: int = PAGE_LIMIT) -> dict[str, Any]:
    response = requests.get(
        f"{get_api_base_url().rstrip('/')}/history",
        headers={"Authorization": f"Bearer {token}"},
        params={"skip": skip, "limit": limit, "sort_order": "desc"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise BotAuthError(f"Failed to load history (HTTP {response.status_code})")
    return response.json()


def _api_delete_history_entry(token: str, lecture_id: str) -> None:
    response = requests.delete(
        f"{get_api_base_url().rstrip('/')}/history/{lecture_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code == 404:
        return
    if response.status_code >= 400:
        raise BotAuthError(f"Failed to delete history entry (HTTP {response.status_code})")


def _history_text(payload: dict[str, Any]) -> str:
    items = payload.get("items")
    total = int(payload.get("total", 0) or 0)
    skip = int(payload.get("skip", 0) or 0)
    if not isinstance(items, list) or not items:
        return "📖 История\n\nПока пусто."

    start_index = skip + 1
    end_index = skip + len(items)
    lines = [f"📖 История ({start_index}-{end_index} из {total})", ""]
    for index, item in enumerate(items, start=start_index):
        if not isinstance(item, dict):
            continue
        title = _truncate(str(item.get("title", "Без названия")), 64)
        status = str(item.get("status", "unknown"))
        lines.append(f"{index}. {title} [{status}]")
    return "\n".join(lines)


def _history_list_keyboard(payload: dict[str, Any]) -> InlineKeyboardMarkup:
    items = payload.get("items")
    skip = int(payload.get("skip", 0) or 0)
    limit = int(payload.get("limit", PAGE_LIMIT) or PAGE_LIMIT)
    total = int(payload.get("total", 0) or 0)

    rows: list[list[InlineKeyboardButton]] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            lecture_id = str(item.get("lecture_id", "")).strip()
            if not lecture_id:
                continue
            title = _truncate(str(item.get("title", "Лекция")), 48)
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"📄 {title}",
                        callback_data=f"history:open:{lecture_id}:{skip}",
                    )
                ]
            )

    nav_row: list[InlineKeyboardButton] = []
    if skip > 0:
        prev_skip = max(0, skip - limit)
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"history:page:{prev_skip}"))
    if skip + limit < total:
        next_skip = skip + limit
        nav_row.append(InlineKeyboardButton(text="Ещё ➡️", callback_data=f"history:page:{next_skip}"))
    if nav_row:
        rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(text="Обновить", callback_data="history:page:0")]])


def _history_entry_keyboard(lecture_id: str, *, skip: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Конспект", callback_data=f"lecture:summary:{lecture_id}"),
                InlineKeyboardButton(text="Граф", callback_data=f"lecture:graph:{lecture_id}"),
            ],
            [
                InlineKeyboardButton(
                    text="Удалить из истории",
                    callback_data=f"history:remove:{lecture_id}:{skip}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"history:page:{skip}")],
        ]
    )


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


async def _safe_edit_message(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


async def _send_history_page(
    message: Message,
    *,
    token: str,
    skip: int,
    edit: bool,
) -> None:
    payload = await asyncio.to_thread(_api_get_history, token, skip=max(0, skip), limit=PAGE_LIMIT)
    text = _history_text(payload)
    keyboard = _history_list_keyboard(payload)
    if edit:
        await _safe_edit_message(message, text, reply_markup=keyboard)
        return
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("history"))
@router.message(F.text == "📖 История")
async def handle_history_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    token = await _authorized_token_or_start_auth(message, state, telegram_id=message.from_user.id)
    if token is None:
        return
    try:
        await _send_history_page(message, token=token, skip=0, edit=False)
    except Exception:
        await message.answer("Не удалось загрузить историю.")


@router.callback_query(F.data == "menu:history")
async def handle_history_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
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
        await _send_history_page(callback.message, token=token, skip=0, edit=True)
    except Exception:
        await callback.message.answer("Не удалось загрузить историю.")


@router.callback_query(F.data.startswith("history:page:"))
async def handle_history_page_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    skip_raw = callback.data.rsplit(":", maxsplit=1)[-1]
    try:
        skip = max(0, int(skip_raw))
    except ValueError:
        await callback.answer("Некорректная страница.", show_alert=True)
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
        await _send_history_page(callback.message, token=token, skip=skip, edit=True)
    except Exception:
        await callback.message.answer("Не удалось загрузить историю.")


@router.callback_query(F.data.startswith("history:open:"))
async def handle_history_open_callback(callback: CallbackQuery) -> None:
    if callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Некорректные данные лекции.", show_alert=True)
        return
    lecture_id = parts[2]
    skip_raw = parts[3]

    try:
        UUID(lecture_id)
        skip = max(0, int(skip_raw))
    except ValueError:
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(
        "Выберите действие для лекции:",
        reply_markup=_history_entry_keyboard(lecture_id, skip=skip),
    )


@router.callback_query(F.data.startswith("history:remove:"))
async def handle_history_remove_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    lecture_id = parts[2]
    skip_raw = parts[3]
    try:
        UUID(lecture_id)
        skip = max(0, int(skip_raw))
    except ValueError:
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    token = await _authorized_token_or_start_auth(
        callback.message,
        state,
        telegram_id=callback.from_user.id,
    )
    if token is None:
        await callback.answer("Нужна авторизация.", show_alert=True)
        return

    try:
        await asyncio.to_thread(_api_delete_history_entry, token, lecture_id)
    except Exception:
        await callback.answer("Не удалось удалить запись из истории.", show_alert=True)
        return

    await callback.answer("Удалено из истории ✅")
    try:
        await _send_history_page(callback.message, token=token, skip=skip, edit=True)
    except Exception:
        await callback.message.answer("Запись удалена, но список обновить не удалось.")
