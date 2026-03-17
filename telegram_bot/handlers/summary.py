from __future__ import annotations

import asyncio
import re
from typing import Any

import requests
from aiogram import Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

try:
    from telegram_bot.auth import BotAuthError, ensure_authorized_telegram_user
    from telegram_bot.utils import get_api_base_url
except ModuleNotFoundError:
    from auth import BotAuthError, ensure_authorized_telegram_user
    from utils import get_api_base_url

router = Router(name="summary")

REQUEST_TIMEOUT_SECONDS = 40
MAX_MESSAGE_LEN = 3800
_CHUNK_SAFETY_MARGIN = 32

_QUOTE_TERM_RE = re.compile(r"[\"«]([^\"»]{2,80})[\"»]")
_DEF_TERM_RE = re.compile(r"(?:^|[\n\.]\s*)([A-ZА-Я][A-Za-zА-Яа-я0-9\- ]{2,40}):")
_MD_V2_SPECIALS = "\\_*[]()~`>#+-=|{}.!"


def _api_get_summary(token: str, lecture_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_api_base_url().rstrip('/')}/lectures/{lecture_id}/summary",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise BotAuthError(f"Failed to load summary (HTTP {response.status_code})")
    return response.json()


def _api_export_pdf(token: str, lecture_id: str) -> bytes:
    response = requests.get(
        f"{get_api_base_url().rstrip('/')}/lectures/{lecture_id}/export?format=pdf",
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


def _highlight_terms_in_escaped_text(escaped_text: str, terms: list[str]) -> str:
    highlighted = escaped_text
    for term in terms:
        escaped_term = _escape_markdown_v2(term)
        if not escaped_term:
            continue
        highlighted = highlighted.replace(escaped_term, f"*{escaped_term}*", 1)
    return highlighted


def _split_escaped_text(escaped_text: str, max_len: int) -> list[str]:
    if max_len <= 0:
        return []
    remaining = escaped_text.strip()
    if not remaining:
        return []

    chunks: list[str] = []
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        split_at = max_len
        newline_pos = remaining.rfind("\n", 0, max_len + 1)
        space_pos = remaining.rfind(" ", 0, max_len + 1)
        boundary = max(newline_pos, space_pos)
        if boundary >= int(max_len * 0.6):
            split_at = boundary

        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:max_len]
        chunks.append(chunk)
        remaining = remaining[len(chunk) :].lstrip()

    return chunks


def _build_block_header(block: dict[str, Any]) -> str:
    title = _escape_markdown_v2(str(block.get("title", "Блок")).strip() or "Блок")
    block_type = _escape_markdown_v2(str(block.get("type", "thought")).strip() or "thought")
    time_start = block.get("timecode_start")
    time_end = block.get("timecode_end")

    header = f"*\\#\\# {title}*\n_Тип: {block_type}_"
    if time_start is not None or time_end is not None:
        header += (
            "\n_Таймкод: "
            f"{_escape_markdown_v2(str(time_start))} - {_escape_markdown_v2(str(time_end))}_"
        )
    return header.strip()


def _split_summary_chunks(blocks: list[dict[str, Any]]) -> list[str]:
    chunks: list[str] = []
    for block in blocks:
        header = _build_block_header(block)
        raw_text = str(block.get("text", "")).strip()
        escaped_body = _escape_markdown_v2(raw_text)
        terms = _extract_key_terms(raw_text)

        if not header and not escaped_body:
            continue

        if not escaped_body:
            if len(header) <= MAX_MESSAGE_LEN:
                chunks.append(header)
            else:
                chunks.extend(_split_escaped_text(header, MAX_MESSAGE_LEN))
            continue

        first_limit = max(200, MAX_MESSAGE_LEN - len(header) - 1 - _CHUNK_SAFETY_MARGIN)
        first_body_chunks = _split_escaped_text(escaped_body, first_limit)
        if not first_body_chunks:
            chunks.append(header[:MAX_MESSAGE_LEN])
            continue

        first_body = _highlight_terms_in_escaped_text(first_body_chunks[0], terms)
        chunks.append(f"{header}\n{first_body}".strip())

        remaining = escaped_body[len(first_body_chunks[0]) :].lstrip()
        for part in _split_escaped_text(remaining, MAX_MESSAGE_LEN - _CHUNK_SAFETY_MARGIN):
            chunks.append(_highlight_terms_in_escaped_text(part, terms))

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
