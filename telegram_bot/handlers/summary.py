from __future__ import annotations

import asyncio
import re
from typing import Any

import requests
from aiogram import Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

try:
    from telegram_bot.auth import BotAuthError, ensure_authorized_telegram_user
except ModuleNotFoundError:
    from auth import BotAuthError, ensure_authorized_telegram_user

router = Router(name="summary")

REQUEST_TIMEOUT_SECONDS = 40
MAX_MESSAGE_LEN = 3800
DEFAULT_API_BASE_URL = "http://backend:8000"

_QUOTE_TERM_RE = re.compile(r"[\"«]([^\"»]{2,80})[\"»]")
_DEF_TERM_RE = re.compile(r"(?:^|[\n\.]\s*)([A-ZА-Я][A-Za-zА-Яа-я0-9\- ]{2,40}):")
_MD_V2_SPECIALS = "\\_*[]()~`>#+-=|{}.!"


def _api_base_url() -> str:
    import os

    raw = os.getenv("API_BASE_URL")
    return raw.strip() if raw and raw.strip() else DEFAULT_API_BASE_URL


def _api_get_summary(token: str, lecture_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{_api_base_url().rstrip('/')}/lectures/{lecture_id}/summary",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise BotAuthError(f"Failed to load summary (HTTP {response.status_code})")
    return response.json()


def _api_export_pdf(token: str, lecture_id: str) -> bytes:
    response = requests.get(
        f"{_api_base_url().rstrip('/')}/lectures/{lecture_id}/export?format=pdf",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise BotAuthError(f"Failed to export PDF (HTTP {response.status_code})")
    return response.content


def _summary_pdf_keyboard(lecture_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Скачать PDF", callback_data=f"summary:pdf:{lecture_id}")]
        ]
    )


def _extract_key_terms(text: str) -> list[str]:
    terms: list[str] = []
    for match in _QUOTE_TERM_RE.finditer(text):
        term = match.group(1).strip()
        if term and term not in terms:
            terms.append(term)
    for match in _DEF_TERM_RE.finditer(text):
        term = match.group(1).strip()
        if term and term not in terms:
            terms.append(term)
    return terms[:6]


def _escape_markdown_v2(text: str) -> str:
    escaped = text
    for char in _MD_V2_SPECIALS:
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def _apply_markdown_formatting(text: str) -> str:
    escaped = _escape_markdown_v2(text.strip())
    if not escaped:
        return escaped

    terms = _extract_key_terms(text)
    for term in terms:
        escaped_term = _escape_markdown_v2(term)
        escaped = escaped.replace(escaped_term, f"*{escaped_term}*", 1)
    return escaped


def _build_block_html(block: dict[str, Any]) -> str:
    title = _escape_markdown_v2(str(block.get("title", "Блок")).strip() or "Блок")
    block_type = _escape_markdown_v2(str(block.get("type", "thought")).strip() or "thought")
    text = _apply_markdown_formatting(str(block.get("text", "")))
    time_start = block.get("timecode_start")
    time_end = block.get("timecode_end")

    header = f"*## {title}*\n_Тип: {block_type}_"
    if time_start is not None or time_end is not None:
        header += (
            "\n_Таймкод: "
            f"{_escape_markdown_v2(str(time_start))} - {_escape_markdown_v2(str(time_end))}_"
        )
    return f"{header}\n{text}".strip()


def _split_summary_chunks(blocks: list[dict[str, Any]]) -> list[str]:
    chunks: list[str] = []
    current = ""

    for block in blocks:
        block_text = _build_block_html(block)
        if not block_text:
            continue

        candidate = f"{current}\n\n{block_text}".strip() if current else block_text
        if len(candidate) <= MAX_MESSAGE_LEN:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(block_text) <= MAX_MESSAGE_LEN:
            current = block_text
            continue

        start = 0
        while start < len(block_text):
            end = min(start + MAX_MESSAGE_LEN, len(block_text))
            chunks.append(block_text[start:end])
            start = end

    if current:
        chunks.append(current)
    return chunks


async def _authorized_token(telegram_id: int) -> str:
    linked_user = await asyncio.to_thread(ensure_authorized_telegram_user, telegram_id)
    return linked_user.jwt_token


@router.callback_query(lambda c: bool(c.data and c.data.startswith("lecture:summary:")))
async def callback_summary(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    lecture_id = callback.data.split(":")[-1]
    await callback.answer()

    try:
        token = await _authorized_token(callback.from_user.id)
        payload = await asyncio.to_thread(_api_get_summary, token, lecture_id)
    except BotAuthError as exc:
        await callback.message.answer(f"Не удалось получить конспект: {exc}")
        return
    except Exception:
        await callback.message.answer("Не удалось получить конспект.")
        return

    blocks = payload.get("blocks") if isinstance(payload, dict) else []
    if not isinstance(blocks, list) or not blocks:
        await callback.message.answer("Конспект пока недоступен.")
        return

    chunks = _split_summary_chunks([item for item in blocks if isinstance(item, dict)])
    if not chunks:
        await callback.message.answer("Конспект пока недоступен.")
        return

    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        await callback.message.answer(
            chunk,
            parse_mode="MarkdownV2",
            reply_markup=_summary_pdf_keyboard(lecture_id) if is_last else None,
        )


@router.callback_query(lambda c: bool(c.data and c.data.startswith("summary:pdf:")))
async def callback_summary_pdf(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    lecture_id = callback.data.split(":")[-1]
    await callback.answer("Готовлю PDF...")

    try:
        token = await _authorized_token(callback.from_user.id)
        content = await asyncio.to_thread(_api_export_pdf, token, lecture_id)
    except BotAuthError as exc:
        await callback.message.answer(f"Не удалось скачать PDF: {exc}")
        return
    except Exception:
        await callback.message.answer("Не удалось скачать PDF.")
        return

    await callback.message.answer_document(
        BufferedInputFile(content, filename=f"lecture-{lecture_id}-summary.pdf"),
        caption="PDF конспекта",
    )
