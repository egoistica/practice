from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timezone

import redis as redis_sync
import redis.asyncio as redis
from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

PROGRESS_CHANNEL = "lecture_progress"
REDIS_RETRY_BASE_SECONDS = 0.5
REDIS_RETRY_MAX_SECONDS = 5.0

_INSTANCE_ID = uuid.uuid4().hex
_subscriptions: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
_subscriptions_lock = asyncio.Lock()
_listener_lock = asyncio.Lock()
_redis_client: redis.Redis | None = None
_redis_pubsub = None
_listener_task: asyncio.Task[None] | None = None
_redis_sync_client: redis_sync.Redis | None = None


def _base_payload(lecture_id: uuid.UUID, event_type: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": event_type,
        "lecture_id": str(lecture_id),
        "source": _INSTANCE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return payload


def _progress_payload(lecture_id: uuid.UUID, progress: int, status_value: str | None) -> dict[str, object]:
    normalized_progress = max(0, min(100, int(progress)))
    payload = _base_payload(lecture_id, "lecture_progress")
    payload["progress"] = normalized_progress
    if status_value is not None:
        payload["status"] = status_value
    return payload


async def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


async def register_subscription(lecture_id: uuid.UUID, websocket: WebSocket) -> None:
    async with _subscriptions_lock:
        _subscriptions[lecture_id].add(websocket)


async def unregister_subscription(lecture_id: uuid.UUID, websocket: WebSocket) -> None:
    async with _subscriptions_lock:
        subscribers = _subscriptions.get(lecture_id)
        if not subscribers:
            return
        subscribers.discard(websocket)
        if not subscribers:
            _subscriptions.pop(lecture_id, None)


async def _broadcast_local(lecture_id: uuid.UUID, payload: dict[str, object]) -> None:
    async with _subscriptions_lock:
        subscribers = list(_subscriptions.get(lecture_id, set()))
    if not subscribers:
        return

    stale: list[WebSocket] = []
    for websocket in subscribers:
        try:
            await websocket.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            stale.append(websocket)
        except Exception:
            logger.exception("Failed to send progress via websocket for lecture_id=%s", lecture_id)
            stale.append(websocket)

    if stale:
        async with _subscriptions_lock:
            current = _subscriptions.get(lecture_id)
            if current:
                for websocket in stale:
                    current.discard(websocket)
                if not current:
                    _subscriptions.pop(lecture_id, None)


async def _publish_redis(payload: dict[str, object]) -> None:
    try:
        client = await _get_redis_client()
        await client.publish(PROGRESS_CHANNEL, json.dumps(payload))
    except Exception:
        logger.exception(
            "Failed to publish lecture progress to redis for lecture_id=%s",
            payload.get("lecture_id"),
        )
        raise


def _get_redis_sync_client() -> redis_sync.Redis:
    global _redis_sync_client
    if _redis_sync_client is None:
        _redis_sync_client = redis_sync.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_sync_client


def _publish_redis_sync(payload: dict[str, object]) -> None:
    try:
        client = _get_redis_sync_client()
        client.publish(PROGRESS_CHANNEL, json.dumps(payload))
    except Exception:
        logger.exception(
            "Failed to publish lecture progress to redis (sync) for lecture_id=%s",
            payload.get("lecture_id"),
        )
        raise


async def broadcast_progress(
    lecture_id: uuid.UUID,
    progress: int,
    status_value: str | None = None,
) -> None:
    payload = _progress_payload(lecture_id, progress, status_value)
    await _broadcast_local(lecture_id, payload)
    await _publish_redis(payload)


def broadcast_progress_sync(
    lecture_id: uuid.UUID,
    progress: int,
    status_value: str | None = None,
) -> None:
    payload = _progress_payload(lecture_id, progress, status_value)
    _publish_redis_sync(payload)


async def broadcast_lecture_event(
    lecture_id: uuid.UUID,
    event_type: str,
    event_payload: dict[str, object],
) -> None:
    normalized_type = str(event_type).strip()
    if not normalized_type:
        raise ValueError("event_type must not be empty")
    payload = _base_payload(lecture_id, normalized_type)
    payload.update(event_payload)
    await _broadcast_local(lecture_id, payload)
    await _publish_redis(payload)


def broadcast_lecture_event_sync(
    lecture_id: uuid.UUID,
    event_type: str,
    event_payload: dict[str, object],
) -> None:
    normalized_type = str(event_type).strip()
    if not normalized_type:
        raise ValueError("event_type must not be empty")
    payload = _base_payload(lecture_id, normalized_type)
    payload.update(event_payload)
    _publish_redis_sync(payload)


async def _close_redis_pubsub() -> None:
    global _redis_pubsub
    if _redis_pubsub is not None:
        close_fn = getattr(_redis_pubsub, "aclose", None) or _redis_pubsub.close
        close_result = close_fn()
        if asyncio.iscoroutine(close_result):
            await close_result
        _redis_pubsub = None


async def _close_redis_client() -> None:
    global _redis_client
    if _redis_client is not None:
        close_fn = getattr(_redis_client, "aclose", None) or _redis_client.close
        close_result = close_fn()
        if asyncio.iscoroutine(close_result):
            await close_result
        _redis_client = None


async def _progress_listener_loop() -> None:
    global _redis_pubsub
    retry_delay = REDIS_RETRY_BASE_SECONDS

    try:
        while True:
            if _redis_pubsub is None:
                try:
                    client = await _get_redis_client()
                    _redis_pubsub = client.pubsub(ignore_subscribe_messages=True)
                    await _redis_pubsub.subscribe(PROGRESS_CHANNEL)
                    retry_delay = REDIS_RETRY_BASE_SECONDS
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Failed to initialize redis pubsub for lecture progress listener")
                    await _close_redis_pubsub()
                    await _close_redis_client()
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, REDIS_RETRY_MAX_SECONDS)
                    continue

            try:
                message = await _redis_pubsub.get_message(timeout=1.0)
            except asyncio.CancelledError:
                raise
            except RedisError:
                logger.exception("Redis get_message failed in lecture progress listener")
                await _close_redis_pubsub()
                await _close_redis_client()
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, REDIS_RETRY_MAX_SECONDS)
                continue
            except Exception:
                logger.exception("Unexpected redis listener error while reading message")
                await _close_redis_pubsub()
                await _close_redis_client()
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, REDIS_RETRY_MAX_SECONDS)
                continue

            if not message:
                await asyncio.sleep(0.05)
                continue

            try:
                if not isinstance(message, dict):
                    logger.warning(
                        "Invalid redis message type for lecture progress: %s",
                        type(message).__name__,
                    )
                    continue

                raw_data = message.get("data")
                if not raw_data:
                    continue

                if isinstance(raw_data, bytes):
                    raw_data = raw_data.decode("utf-8", errors="ignore")

                payload = json.loads(raw_data)
                if not isinstance(payload, dict):
                    logger.warning("Invalid redis payload shape for lecture progress")
                    continue
            except Exception:
                logger.exception("Failed to parse lecture progress payload from redis; skipping message")
                continue

            if str(payload.get("source", "")) == _INSTANCE_ID:
                continue

            raw_lecture_id = payload.get("lecture_id")
            try:
                lecture_id = uuid.UUID(str(raw_lecture_id))
            except (ValueError, TypeError):
                logger.warning("Invalid lecture_id in progress payload: %s", raw_lecture_id)
                continue

            try:
                await _broadcast_local(lecture_id, payload)
            except Exception:
                logger.exception("Failed to broadcast lecture progress locally for lecture_id=%s", lecture_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Lecture progress redis listener stopped unexpectedly")
    finally:
        await _close_redis_pubsub()


async def start_progress_listener() -> None:
    global _listener_task
    async with _listener_lock:
        if _listener_task and not _listener_task.done():
            return
        _listener_task = asyncio.create_task(_progress_listener_loop())


async def stop_progress_listener() -> None:
    global _listener_task
    async with _listener_lock:
        if _listener_task is not None:
            _listener_task.cancel()
            with suppress(asyncio.CancelledError):
                await _listener_task
            _listener_task = None

        await _close_redis_pubsub()
        await _close_redis_client()
