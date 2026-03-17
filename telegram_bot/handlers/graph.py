from __future__ import annotations

import asyncio
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

router = Router(name="graph")

REQUEST_TIMEOUT_SECONDS = 40


def _graph_actions_keyboard(lecture_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="JSON", callback_data=f"graph:json:{lecture_id}")]
        ]
    )


def _api_get_graph_json(token: str, lecture_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_api_base_url().rstrip('/')}/lectures/{lecture_id}/graph",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise BotAuthError(f"Failed to load graph (HTTP {response.status_code})")
    return response.json()


def _api_export_graph_png(token: str, lecture_id: str) -> bytes:
    response = requests.get(
        f"{get_api_base_url().rstrip('/')}/lectures/{lecture_id}/graph/export?format=png",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise BotAuthError(f"Failed to export graph PNG (HTTP {response.status_code})")
    return response.content


def _api_export_graph_json(token: str, lecture_id: str) -> bytes:
    response = requests.get(
        f"{get_api_base_url().rstrip('/')}/lectures/{lecture_id}/graph/export?format=json",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise BotAuthError(f"Failed to export graph JSON (HTTP {response.status_code})")
    return response.content


async def _authorized_token(telegram_id: int) -> str:
    linked_user = await asyncio.to_thread(ensure_authorized_telegram_user, telegram_id)
    return linked_user.jwt_token


@router.callback_query(lambda c: bool(c.data and c.data.startswith("lecture:graph:")))
async def callback_graph(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    lecture_id = callback.data.split(":")[-1]
    await callback.answer("Готовлю граф...")

    try:
        token = await _authorized_token(callback.from_user.id)
        graph_payload = await asyncio.to_thread(_api_get_graph_json, token, lecture_id)
        graph_png = await asyncio.to_thread(_api_export_graph_png, token, lecture_id)
    except BotAuthError as exc:
        await callback.message.answer(f"Не удалось получить граф: {exc}")
        return
    except Exception:
        await callback.message.answer("Не удалось получить граф.")
        return

    nodes = graph_payload.get("nodes") if isinstance(graph_payload, dict) else []
    edges = graph_payload.get("edges") if isinstance(graph_payload, dict) else []
    node_count = len(nodes) if isinstance(nodes, list) else 0
    edge_count = len(edges) if isinstance(edges, list) else 0
    caption = (
        "Граф сущностей\n"
        f"Узлов: {node_count}\n"
        f"Рёбер: {edge_count}"
    )

    await callback.message.answer_photo(
        BufferedInputFile(graph_png, filename=f"lecture-{lecture_id}-graph.png"),
        caption=caption,
        reply_markup=_graph_actions_keyboard(lecture_id),
    )


@router.callback_query(lambda c: bool(c.data and c.data.startswith("graph:json:")))
async def callback_graph_json(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка контекста.", show_alert=True)
        return
    lecture_id = callback.data.split(":")[-1]
    await callback.answer("Готовлю JSON...")

    try:
        token = await _authorized_token(callback.from_user.id)
        graph_json = await asyncio.to_thread(_api_export_graph_json, token, lecture_id)
    except BotAuthError as exc:
        await callback.message.answer(f"Не удалось скачать JSON: {exc}")
        return
    except Exception:
        await callback.message.answer("Не удалось скачать JSON.")
        return

    await callback.message.answer_document(
        BufferedInputFile(graph_json, filename=f"lecture-{lecture_id}-graph.json"),
        caption="JSON графа сущностей",
    )
