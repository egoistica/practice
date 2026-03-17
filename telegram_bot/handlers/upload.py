from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse
from uuid import UUID

import requests
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

try:
    from telegram_bot.auth import BotAuthError, ensure_authorized_telegram_user
    from telegram_bot.handlers.auth import request_auth_method
except ModuleNotFoundError:
    from auth import BotAuthError, ensure_authorized_telegram_user
    from handlers.auth import request_auth_method

router = Router(name="upload")
logger = logging.getLogger(__name__)

UPLOAD_MODE_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Мгновенно", callback_data="upload:mode:instant")],
        [InlineKeyboardButton(text="⏱️ По мере просмотра", callback_data="upload:mode:realtime")],
    ]
)

READY_ACTIONS_KEYBOARD = lambda lecture_id: InlineKeyboardMarkup(  # noqa: E731
    inline_keyboard=[
        [InlineKeyboardButton(text="Конспект", callback_data=f"lecture:summary:{lecture_id}")],
        [InlineKeyboardButton(text="Граф", callback_data=f"lecture:graph:{lecture_id}")],
        [InlineKeyboardButton(text="Сохранить", callback_data=f"lecture:save:{lecture_id}")],
        [InlineKeyboardButton(text="В избранное", callback_data=f"lecture:favourite:{lecture_id}")],
    ]
)

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
DEFAULT_API_BASE_URL = "http://backend:8000"
REQUEST_TIMEOUT_SECONDS = 40
POLL_INTERVAL_SECONDS = 2
MAX_PROGRESS_POLLS = 300
_URL_TRAILING_CHARS = ".,;:!?)]}>"
_BOT_TASKS_ATTR = "_upload_background_tasks"


class UploadStates(StatesGroup):
    waiting_source = State()
    waiting_mode = State()
    creating_lecture = State()


def _api_base_url() -> str:
    raw = os.getenv("API_BASE_URL")
    return raw.strip() if raw and raw.strip() else DEFAULT_API_BASE_URL


def _extract_first_url(text: str) -> str | None:
    match = URL_RE.search(text)
    if not match:
        return None
    candidate = match.group(0).strip().rstrip(_URL_TRAILING_CHARS)
    return _normalize_public_url(candidate, preserve_query=True)


def _normalize_public_url(raw_url: str, *, preserve_query: bool = False) -> str | None:
    candidate = raw_url.strip().rstrip(_URL_TRAILING_CHARS)
    if not candidate:
        return None

    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None

    normalized = urlunparse(
        (
            scheme,
            parsed.netloc,
            parsed.path or "/",
            "",
            parsed.query if preserve_query else "",
            parsed.fragment if preserve_query else "",
        )
    )
    return normalized or None


def _build_upload_title_from_url(url: str) -> str:
    normalized = _normalize_public_url(url, preserve_query=False) or "Video URL"
    return normalized if len(normalized) <= 255 else normalized[:255]


def _build_upload_title_from_filename(file_name: str | None) -> str:
    if not file_name:
        return "Telegram upload"
    stem = Path(file_name).stem.strip()
    return stem[:255] if stem else "Telegram upload"


async def _require_authorized_or_start_auth(
    message: Message,
    state: FSMContext,
    *,
    telegram_id: int,
) -> bool:
    try:
        await asyncio.to_thread(ensure_authorized_telegram_user, telegram_id)
        return True
    except BotAuthError:
        await request_auth_method(message, state, telegram_id=telegram_id)
        return False


async def _authorized_token_for_telegram_user(telegram_id: int) -> str:
    linked_user = await asyncio.to_thread(ensure_authorized_telegram_user, telegram_id)
    return linked_user.jwt_token


def _raise_api_error(response: requests.Response, *, context: str) -> None:
    raw_body = response.text.strip()
    logger.error(
        "Backend API request failed context=%s status=%s body=%s",
        context,
        response.status_code,
        raw_body,
    )
    if response.status_code in (401, 403):
        raise BotAuthError(f"Authentication failed (HTTP {response.status_code})")
    raise BotAuthError(f"Request failed (HTTP {response.status_code})")


def _api_post_lecture_url(token: str, *, title: str, mode: str, source_url: str) -> dict[str, Any]:
    response = requests.post(
        f"{_api_base_url().rstrip('/')}/lectures",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "title": title,
            "mode": mode,
            "source_type": "url",
            "source_url": source_url,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        _raise_api_error(response, context="post_lecture_url")
    return response.json()


def _api_post_lecture_file(
    token: str,
    *,
    title: str,
    mode: str,
    file_name: str,
    content_type: str,
    file_path: str,
) -> dict[str, Any]:
    with open(file_path, "rb") as stream:
        response = requests.post(
            f"{_api_base_url().rstrip('/')}/lectures",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "title": title,
                "mode": mode,
                "source_type": "file",
            },
            files={"file": (file_name, stream, content_type)},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    if response.status_code >= 400:
        _raise_api_error(response, context="post_lecture_file")
    return response.json()


def _api_get_lecture(token: str, lecture_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{_api_base_url().rstrip('/')}/lectures/{lecture_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        _raise_api_error(response, context="get_lecture")
    return response.json()


def _api_get_summary(token: str, lecture_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{_api_base_url().rstrip('/')}/lectures/{lecture_id}/summary",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        _raise_api_error(response, context="get_summary")
    return response.json()


def _api_get_graph(token: str, lecture_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{_api_base_url().rstrip('/')}/lectures/{lecture_id}/graph",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        _raise_api_error(response, context="get_graph")
    return response.json()


def _api_export_markdown(token: str, lecture_id: str) -> bytes:
    response = requests.get(
        f"{_api_base_url().rstrip('/')}/lectures/{lecture_id}/export?format=md",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        _raise_api_error(response, context="export_markdown")
    return response.content


def _api_add_favourite(token: str, lecture_id: str) -> None:
    response = requests.post(
        f"{_api_base_url().rstrip('/')}/favourites/{lecture_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        _raise_api_error(response, context="add_favourite")


def _bot_background_tasks(bot: Bot) -> set[asyncio.Task[Any]]:
    tasks = getattr(bot, _BOT_TASKS_ATTR, None)
    if isinstance(tasks, set):
        return tasks
    tasks = set()
    setattr(bot, _BOT_TASKS_ATTR, tasks)
    return tasks


def _track_background_task(bot: Bot, task: asyncio.Task[Any], *, task_key: str) -> None:
    tasks = _bot_background_tasks(bot)
    tasks.add(task)

    def _on_done(done_task: asyncio.Task[Any]) -> None:
        tasks.discard(done_task)
        if done_task.cancelled():
            return
        exc = done_task.exception()
        if exc is not None:
            logger.exception("Background upload task failed task_key=%s", task_key, exc_info=exc)

    task.add_done_callback(_on_done)


async def cancel_background_tasks(bot: Bot) -> None:
    tasks = list(_bot_background_tasks(bot))
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _bot_background_tasks(bot).clear()


def _status_text(status: str, progress: int) -> str:
    return (
        "Обрабатывается... ⏳\n"
        f"Статус: {status}\n"
        f"Прогресс: {progress}%"
    )


async def _safe_edit_message(
    bot,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


async def _monitor_lecture_progress(
    bot,
    *,
    chat_id: int,
    message_id: int,
    telegram_id: int,
    lecture_id: str,
) -> None:
    last_snapshot: tuple[str, int] | None = None

    for _attempt in range(1, MAX_PROGRESS_POLLS + 1):
        try:
            token = await _authorized_token_for_telegram_user(telegram_id)
            payload = await asyncio.to_thread(_api_get_lecture, token, lecture_id)
            status = str(payload.get("status", "processing"))
            progress = int(payload.get("processing_progress", 0))
        except Exception:
            await _safe_edit_message(
                bot,
                chat_id,
                message_id,
                "Ошибка при получении прогресса. Попробуйте /start и проверьте лекцию позже.",
            )
            return

        snapshot = (status, progress)
        if snapshot != last_snapshot:
            await _safe_edit_message(
                bot,
                chat_id,
                message_id,
                _status_text(status, progress),
            )
            last_snapshot = snapshot

        if status == "done":
            await _safe_edit_message(
                bot,
                chat_id,
                message_id,
                "Готово! ✅ Выберите действие:",
                reply_markup=READY_ACTIONS_KEYBOARD(lecture_id),
            )
            return
        if status == "error":
            await _safe_edit_message(
                bot,
                chat_id,
                message_id,
                "Обработка завершилась с ошибкой. Откройте лекцию в веб-приложении для деталей.",
            )
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    await _safe_edit_message(
        bot,
        chat_id,
        message_id,
        "Превышено время ожидания обработки. Проверьте статус лекции позже через /start.",
    )


async def _start_upload_flow(
    message: Message,
    state: FSMContext,
    *,
    telegram_id: int,
) -> None:
    if not await _require_authorized_or_start_auth(
        message,
        state,
        telegram_id=telegram_id,
    ):
        return
    await state.set_state(UploadStates.waiting_source)
    await state.update_data(upload_payload=None)
    await message.answer(
        "Отправьте видеофайл (video/document) или сообщение со ссылкой на видео."
    )


@router.message(Command("upload"))
async def handle_upload_command(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    await _start_upload_flow(message, state, telegram_id=message.from_user.id)


@router.message(F.text == "📤 Загрузить")
async def handle_upload_button(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    await _start_upload_flow(message, state, telegram_id=message.from_user.id)


@router.callback_query(F.data == "menu:upload")
async def handle_upload_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    await _start_upload_flow(
        callback.message,
        state,
        telegram_id=callback.from_user.id,
    )


@router.message(UploadStates.waiting_source, F.video)
async def handle_upload_video(message: Message, state: FSMContext) -> None:
    if message.video is None:
        return
    await state.update_data(
        upload_payload={
            "kind": "file",
            "file_id": message.video.file_id,
            "file_name": message.video.file_name or f"{message.video.file_id}.mp4",
            "content_type": message.video.mime_type or "video/mp4",
            "title": _build_upload_title_from_filename(message.video.file_name),
        }
    )
    await state.set_state(UploadStates.waiting_mode)
    await message.answer("Выберите режим обработки:", reply_markup=UPLOAD_MODE_KEYBOARD)


@router.message(UploadStates.waiting_source, F.document)
async def handle_upload_document(message: Message, state: FSMContext) -> None:
    if message.document is None:
        return
    await state.update_data(
        upload_payload={
            "kind": "file",
            "file_id": message.document.file_id,
            "file_name": message.document.file_name or f"{message.document.file_id}.bin",
            "content_type": message.document.mime_type or "application/octet-stream",
            "title": _build_upload_title_from_filename(message.document.file_name),
        }
    )
    await state.set_state(UploadStates.waiting_mode)
    await message.answer("Выберите режим обработки:", reply_markup=UPLOAD_MODE_KEYBOARD)


@router.message(UploadStates.waiting_source, F.text)
async def handle_upload_url(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    url = _extract_first_url(text)
    if not url:
        await message.answer("Не вижу ссылки. Отправьте URL вида http(s)://...")
        return

    await state.update_data(
        upload_payload={
            "kind": "url",
            "url": url,
            "title": _build_upload_title_from_url(url),
        }
    )
    await state.set_state(UploadStates.waiting_mode)
    await message.answer("Выберите режим обработки:", reply_markup=UPLOAD_MODE_KEYBOARD)


@router.message(UploadStates.waiting_source)
async def handle_upload_unknown_source(message: Message) -> None:
    await message.answer("Отправьте видеофайл или URL-ссылку.")


@router.callback_query(UploadStates.creating_lecture, F.data.startswith("upload:mode:"))
async def handle_upload_mode_in_progress(callback: CallbackQuery) -> None:
    await callback.answer("Лекция уже создается, подождите...", show_alert=True)


@router.callback_query(UploadStates.waiting_mode, F.data.startswith("upload:mode:"))
async def handle_upload_mode(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return

    mode = callback.data.rsplit(":", maxsplit=1)[-1] if callback.data else ""
    if mode not in {"instant", "realtime"}:
        await callback.answer("Неизвестный режим.", show_alert=True)
        return

    data = await state.get_data()
    payload = data.get("upload_payload")
    if not isinstance(payload, dict):
        await callback.answer("Нет данных загрузки. Нажмите 📤 Загрузить снова.", show_alert=True)
        await state.set_state(UploadStates.waiting_source)
        return

    await state.set_state(UploadStates.creating_lecture)
    await callback.answer("Запускаю обработку...")

    try:
        token = await _authorized_token_for_telegram_user(callback.from_user.id)
    except BotAuthError as exc:
        await state.clear()
        await callback.message.answer(f"Авторизация недействительна: {exc}\nИспользуйте /start")
        return

    try:
        if payload.get("kind") == "url":
            lecture_response = await asyncio.to_thread(
                _api_post_lecture_url,
                token,
                title=str(payload.get("title", "URL lecture")),
                mode=mode,
                source_url=str(payload.get("url", "")),
            )
        else:
            file_id = str(payload.get("file_id", ""))
            if not file_id:
                raise BotAuthError("Отсутствует file_id для загрузки")
            telegram_file = await callback.bot.get_file(file_id)
            suffix = Path(str(payload.get("file_name", ""))).suffix or ".bin"
            temp_path = ""
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                    temp_path = temp_file.name

                with open(temp_path, "wb") as temp_stream:
                    await callback.bot.download_file(
                        telegram_file.file_path,
                        destination=temp_stream,
                    )

                lecture_response = await asyncio.to_thread(
                    _api_post_lecture_file,
                    token,
                    title=str(payload.get("title", "Telegram upload")),
                    mode=mode,
                    file_name=str(payload.get("file_name", "upload.bin")),
                    content_type=str(payload.get("content_type", "application/octet-stream")),
                    file_path=temp_path,
                )
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
    except Exception as exc:
        await callback.message.answer(f"Не удалось создать лекцию: {exc}")
        await state.set_state(UploadStates.waiting_source)
        return

    lecture_id = str(lecture_response.get("id", "")).strip()
    if not lecture_id:
        await callback.message.answer("API не вернул lecture_id. Попробуйте снова.")
        await state.set_state(UploadStates.waiting_source)
        return

    await state.clear()
    progress_message = await callback.message.answer("Обрабатывается... ⏳\nСтатус: pending\nПрогресс: 0%")
    task = asyncio.create_task(
        _monitor_lecture_progress(
            callback.bot,
            chat_id=progress_message.chat.id,
            message_id=progress_message.message_id,
            telegram_id=callback.from_user.id,
            lecture_id=lecture_id,
        ),
        name=f"upload-progress:{lecture_id}:{progress_message.message_id}",
    )
    _track_background_task(
        callback.bot,
        task,
        task_key=f"{lecture_id}:{progress_message.message_id}",
    )


@router.callback_query(F.data.startswith("lecture:graph:"))
async def callback_graph(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    lecture_id = callback.data.split(":")[-1]
    await callback.answer()

    try:
        token = await _authorized_token_for_telegram_user(callback.from_user.id)
        payload = await asyncio.to_thread(_api_get_graph, token, lecture_id)
    except Exception as exc:
        await callback.message.answer(f"Не удалось получить граф: {exc}")
        return

    nodes = payload.get("nodes") if isinstance(payload, dict) else []
    edges = payload.get("edges") if isinstance(payload, dict) else []
    node_count = len(nodes) if isinstance(nodes, list) else 0
    edge_count = len(edges) if isinstance(edges, list) else 0
    preview_nodes = []
    if isinstance(nodes, list):
        for node in nodes[:5]:
            if isinstance(node, dict):
                preview_nodes.append(str(node.get("label", "")).strip())

    preview_text = ", ".join([item for item in preview_nodes if item]) or "нет данных"
    await callback.message.answer(
        f"Граф готов.\nУзлов: {node_count}\nСвязей: {edge_count}\nПримеры узлов: {preview_text}"
    )


@router.callback_query(F.data.startswith("lecture:save:"))
async def callback_save(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    lecture_id = callback.data.split(":")[-1]
    await callback.answer("Готовлю файл...")

    try:
        token = await _authorized_token_for_telegram_user(callback.from_user.id)
        content = await asyncio.to_thread(_api_export_markdown, token, lecture_id)
    except Exception as exc:
        await callback.message.answer(f"Не удалось экспортировать конспект: {exc}")
        return

    file_name = f"lecture-{quote(lecture_id, safe='')}-summary.md"
    await callback.message.answer_document(
        BufferedInputFile(content, filename=file_name),
        caption="Конспект сохранен в Markdown.",
    )


@router.callback_query(F.data.startswith("lecture:favourite:"))
async def callback_favourite(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    lecture_id = callback.data.split(":")[-1]

    try:
        UUID(lecture_id)
    except ValueError:
        await callback.answer("Некорректный ID лекции.", show_alert=True)
        return

    try:
        token = await _authorized_token_for_telegram_user(callback.from_user.id)
        await asyncio.to_thread(_api_add_favourite, token, lecture_id)
    except Exception as exc:
        await callback.answer(f"Не удалось добавить в избранное: {exc}", show_alert=True)
        return

    await callback.answer("Добавлено в избранное ✅")
