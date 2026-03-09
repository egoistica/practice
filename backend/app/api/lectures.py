from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timezone
from typing import Literal

import redis.asyncio as redis
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, status
from fastapi.websockets import WebSocketDisconnect
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_db
from app.core.security import decode_token
from app.models.lecture import Lecture, LectureMode, LectureSourceType, LectureStatus
from app.models.user import User
from app.schemas.lecture import CreateLectureRequest, LectureListResponse, LectureResponse
from app.services.file_service import delete_lecture_media, save_uploaded_file

router = APIRouter(prefix="/lectures", tags=["lectures"])
ws_router = APIRouter(tags=["lectures"])
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


def _to_lecture_response(lecture: Lecture) -> LectureResponse:
    return LectureResponse(
        id=lecture.id,
        title=lecture.title,
        status=str(lecture.status.value if hasattr(lecture.status, "value") else lecture.status),
        processing_progress=lecture.processing_progress,
        created_at=lecture.created_at,
    )


async def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


async def _register_subscription(lecture_id: uuid.UUID, websocket: WebSocket) -> None:
    async with _subscriptions_lock:
        _subscriptions[lecture_id].add(websocket)


async def _unregister_subscription(lecture_id: uuid.UUID, websocket: WebSocket) -> None:
    async with _subscriptions_lock:
        subscribers = _subscriptions.get(lecture_id)
        if not subscribers:
            return
        subscribers.discard(websocket)
        if not subscribers:
            _subscriptions.pop(lecture_id, None)


def _progress_payload(lecture_id: uuid.UUID, progress: int, status_value: str | None) -> dict[str, object]:
    normalized_progress = max(0, min(100, int(progress)))
    payload: dict[str, object] = {
        "type": "lecture_progress",
        "lecture_id": str(lecture_id),
        "progress": normalized_progress,
        "source": _INSTANCE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if status_value is not None:
        payload["status"] = status_value
    return payload


async def _resolve_websocket_user(websocket: WebSocket, db: AsyncSession) -> User:
    authorization = websocket.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    raw_user_id = payload.get("user_id")
    try:
        user_id = uuid.UUID(str(raw_user_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload") from None

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive")
    return user


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
    asyncio.run(broadcast_progress(lecture_id, progress, status_value))


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
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, REDIS_RETRY_MAX_SECONDS)
                continue
            except Exception:
                logger.exception("Unexpected redis listener error while reading message")
                await _close_redis_pubsub()
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, REDIS_RETRY_MAX_SECONDS)
                continue

            if not message:
                await asyncio.sleep(0.05)
                continue

            try:
                if not isinstance(message, dict):
                    logger.warning("Invalid redis message type for lecture progress: %s", type(message).__name__)
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


async def _close_redis_pubsub() -> None:
    global _redis_pubsub
    if _redis_pubsub is not None:
        close_fn = getattr(_redis_pubsub, "aclose", None) or _redis_pubsub.close
        close_result = close_fn()
        if asyncio.iscoroutine(close_result):
            await close_result
        _redis_pubsub = None


async def start_progress_listener() -> None:
    global _listener_task
    async with _listener_lock:
        if _listener_task and not _listener_task.done():
            return
        _listener_task = asyncio.create_task(_progress_listener_loop())


async def stop_progress_listener() -> None:
    global _listener_task, _redis_client
    async with _listener_lock:
        if _listener_task is not None:
            _listener_task.cancel()
            with suppress(asyncio.CancelledError):
                await _listener_task
            _listener_task = None

        if _redis_client is not None:
            close_fn = getattr(_redis_client, "aclose", None) or _redis_client.close
            close_result = close_fn()
            if asyncio.iscoroutine(close_result):
                await close_result
            _redis_client = None


def _parse_selected_entities(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = [part.strip() for part in value.split(",") if part.strip()]
        return decoded or None

    if decoded is None:
        return None
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="selected_entities must be a JSON array of strings or comma-separated string",
        )
    normalized = [item.strip() for item in decoded if item.strip()]
    return normalized or None


async def parse_create_lecture_request(
    title: str = Form(...),
    mode: LectureMode = Form(default=LectureMode.INSTANT),
    source_type: LectureSourceType = Form(...),
    source_url: str | None = Form(default=None),
    selected_entities: str | None = Form(default=None),
) -> CreateLectureRequest:
    normalized_title = title.strip()
    normalized_source_url = source_url.strip() if source_url else None
    entities = _parse_selected_entities(selected_entities)

    try:
        return CreateLectureRequest(
            title=normalized_title,
            mode=mode,
            source_type=source_type,
            source_url=normalized_source_url,
            selected_entities=entities,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("", response_model=LectureResponse, status_code=status.HTTP_201_CREATED)
async def create_lecture(
    payload: CreateLectureRequest = Depends(parse_create_lecture_request),
    file: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LectureResponse:
    lecture_id = uuid.uuid4()
    file_path: str | None = None
    source_url: str | None = str(payload.source_url) if payload.source_url else None
    if file is not None and not file.filename:
        file = None

    if payload.source_type == LectureSourceType.FILE:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File is required when source_type=file",
            )
        if source_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source_url must be empty when source_type=file",
            )
        try:
            file_path = await save_uploaded_file(file, settings.MEDIA_ROOT, lecture_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    elif payload.source_type == LectureSourceType.URL:
        if not source_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source_url is required when source_type=url",
            )
        if file is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must not be provided when source_type=url",
            )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported source_type")

    lecture = Lecture(
        id=lecture_id,
        user_id=user.id,
        title=payload.title,
        mode=payload.mode,
        source_type=payload.source_type,
        source_url=source_url,
        file_path=file_path,
        status=LectureStatus.PENDING,
        processing_progress=0,
    )
    db.add(lecture)
    try:
        await db.commit()
        await db.refresh(lecture)
    except Exception:
        await db.rollback()
        if file_path:
            try:
                delete_lecture_media(settings.MEDIA_ROOT, lecture_id)
            except OSError:
                logger.exception(
                    "Failed to cleanup lecture media after DB commit/refresh failure for lecture_id=%s",
                    lecture_id,
                )
        raise

    if payload.selected_entities:
        logger.info(
            "lecture_selected_entities lecture_id=%s user_id=%s entities=%s",
            lecture.id,
            user.id,
            payload.selected_entities,
        )

    await broadcast_progress(
        lecture.id,
        lecture.processing_progress,
        lecture.status.value if hasattr(lecture.status, "value") else str(lecture.status),
    )
    return _to_lecture_response(lecture)


@router.get("", response_model=LectureListResponse)
async def list_lectures(
    skip: int = 0,
    limit: int = 20,
    sort_order: Literal["asc", "desc"] = "desc",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LectureListResponse:
    if skip < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="skip must be >= 0")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 100")

    order_clause = Lecture.created_at.asc() if sort_order == "asc" else Lecture.created_at.desc()
    items_result = await db.execute(
        select(Lecture)
        .where(Lecture.user_id == user.id)
        .order_by(order_clause)
        .offset(skip)
        .limit(limit)
    )
    lectures = items_result.scalars().all()
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(Lecture).where(Lecture.user_id == user.id)
            )
        ).scalar_one()
    )

    return LectureListResponse(
        items=[_to_lecture_response(item) for item in lectures],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{lecture_id}", response_model=LectureResponse)
async def get_lecture(
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LectureResponse:
    lecture = await db.get(Lecture, lecture_id)
    if lecture is None or lecture.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")
    return _to_lecture_response(lecture)


@router.delete("/{lecture_id}", status_code=status.HTTP_200_OK)
async def delete_lecture(
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    lecture = await db.get(Lecture, lecture_id)
    if lecture is None or lecture.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    await db.delete(lecture)
    await db.commit()

    try:
        delete_lecture_media(settings.MEDIA_ROOT, lecture_id)
    except OSError:
        logger.exception("Failed at media deletion stage for lecture_id=%s", lecture_id)

    return {"status": "deleted", "lecture_id": str(lecture_id)}


@ws_router.websocket("/ws/{lecture_id}")
async def lecture_progress_ws(websocket: WebSocket, lecture_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        try:
            user = await _resolve_websocket_user(websocket, db)
        except HTTPException as exc:
            await websocket.close(code=4401, reason=str(exc.detail))
            return

        lecture = await db.get(Lecture, lecture_id)
        if lecture is None:
            await websocket.close(code=4403, reason="Lecture not found")
            return
        if lecture.user_id != user.id:
            await websocket.close(code=4403, reason="Forbidden")
            return

    await websocket.accept()
    await _register_subscription(lecture_id, websocket)
    await websocket.send_json({"type": "subscribed", "lecture_id": str(lecture_id)})

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        await _unregister_subscription(lecture_id, websocket)
