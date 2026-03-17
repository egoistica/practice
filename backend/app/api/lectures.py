from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Literal

import redis.asyncio as redis
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, status
from fastapi.responses import Response, StreamingResponse
from fastapi.websockets import WebSocketDisconnect
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import (
    CACHE_TTL_LIST_SECONDS,
    CACHE_TTL_ONE_DAY_SECONDS,
    cache_add_to_index,
    cache_get_json,
    cache_get_index_version,
    cache_invalidate_index,
    cache_set_json,
    graph_cache_index,
    graph_cache_key,
    history_list_cache_index,
    lecture_list_cache_index,
    lecture_list_cache_key,
    summary_cache_index,
    summary_cache_key,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_db
from app.core.limiter import limiter
from app.core.security import decode_token
from app.models.entity_graph import EntityGraph
from app.models.favourite import Favourite
from app.models.history import History
from app.models.lecture import Lecture, LectureMode, LectureSourceType, LectureStatus
from app.models.summary import Summary
from app.models.transcript import Transcript
from app.models.user import User
from app.schemas.graph import GraphResponse
from app.schemas.lecture import CreateLectureRequest, LLMRequestConfig, LectureListResponse, LectureResponse
from app.schemas.summary import SummaryResponse, TranscriptResponse, TranscriptSegment
from app.services.export_service import (
    export_graph_to_image,
    export_graph_to_json,
    export_summary_to_json,
    export_summary_to_markdown,
    export_summary_to_pdf,
)
from app.services.file_service import delete_lecture_media, save_uploaded_file
from app.services.history_service import record_history_visit
from app.services.llm_service import LLMServiceError, enrich_graph, merge_graph_data, summarize_segment
from app.services.progress_service import (
    PROGRESS_CHANNEL,
    broadcast_progress,
    register_subscription,
    unregister_subscription,
)
from app.services.summary_utils import normalize_summary_blocks, to_non_negative_float
from app.tasks.process_lecture import process_lecture_chain

router = APIRouter(prefix="/lectures", tags=["lectures"])
ws_router = APIRouter(tags=["lectures"])
logger = logging.getLogger(__name__)
SUMMARY_ENRICH_PROMPT = (
    "Расширь существующий конспект лекции: добавь полезные детали, примеры и уточнения. "
    "Не дублируй уже имеющиеся блоки и верни только JSON формата blocks."
)
TERMINAL_LECTURE_STATUSES = {"done", "error"}


async def _invalidate_lecture_list_cache(user_id: uuid.UUID) -> None:
    await cache_invalidate_index(lecture_list_cache_index(user_id))


async def _invalidate_history_list_cache(user_id: uuid.UUID) -> None:
    await cache_invalidate_index(history_list_cache_index(user_id))


async def _invalidate_summary_cache(lecture_id: uuid.UUID) -> None:
    await cache_invalidate_index(summary_cache_index(lecture_id))


async def _invalidate_graph_cache(lecture_id: uuid.UUID) -> None:
    await cache_invalidate_index(graph_cache_index(lecture_id))


def _is_terminal_lecture_status(status_value: str | None) -> bool:
    return str(status_value or "").strip().lower() in TERMINAL_LECTURE_STATUSES


def _is_non_terminal_lecture_response(item: LectureResponse) -> bool:
    return not _is_terminal_lecture_status(item.status)


def _to_lecture_response(lecture: Lecture) -> LectureResponse:
    return LectureResponse(
        id=lecture.id,
        title=lecture.title,
        status=str(lecture.status.value if hasattr(lecture.status, "value") else lecture.status),
        processing_progress=lecture.processing_progress,
        created_at=lecture.created_at,
    )


def _status_to_string(status_value: LectureStatus | str | None) -> str:
    if status_value is None:
        return LectureStatus.PENDING.value
    return str(status_value.value if hasattr(status_value, "value") else status_value)


def _lecture_progress_payload(lecture_id: uuid.UUID, progress: int, status_value: LectureStatus | str | None) -> dict[str, Any]:
    return {
        "lecture_id": str(lecture_id),
        "processing_progress": max(0, min(100, int(progress))),
        "status": _status_to_string(status_value),
    }


async def _get_owned_lecture(
    db: AsyncSession,
    lecture_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Lecture:
    lecture = await db.get(Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")
    if lecture.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return lecture


def _collapse_spaces(value: str) -> str:
    return " ".join(value.split())


def _to_non_negative_float(raw_value: Any) -> float | None:
    return to_non_negative_float(raw_value)


def _normalize_summary_blocks(raw_blocks: list[Any], *, default_enriched: bool) -> list[dict[str, Any]]:
    return normalize_summary_blocks(
        raw_blocks,
        default_enriched=default_enriched,
        default_title="Блок",
        default_type="thought",
    )


def _summary_block_key(block: dict[str, Any]) -> tuple[str, str, str]:
    title_key = _collapse_spaces(str(block.get("title", "")).strip().lower())
    text_key = _collapse_spaces(str(block.get("text", "")).strip().lower())
    type_key = _collapse_spaces(str(block.get("type", "")).strip().lower())
    return title_key, text_key, type_key


def _merge_summary_blocks(
    existing_blocks: list[Any],
    incoming_blocks: list[Any],
) -> list[dict[str, Any]]:
    merged = _normalize_summary_blocks(existing_blocks, default_enriched=False)
    seen = {_summary_block_key(block) for block in merged}

    for block in _normalize_summary_blocks(incoming_blocks, default_enriched=True):
        key = _summary_block_key(block)
        if key in seen:
            continue
        merged.append(block)
        seen.add(key)
    return merged


def _summary_timecode_range(blocks: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    starts = [float(block["timecode_start"]) for block in blocks if block.get("timecode_start") is not None]
    ends = [float(block["timecode_end"]) for block in blocks if block.get("timecode_end") is not None]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _to_summary_response(summary: Summary) -> SummaryResponse:
    normalized = _normalize_summary_blocks(list(summary.content or []), default_enriched=False)
    blocks = [
        {
            "title": block["title"],
            "text": block["text"],
            "type": block["type"],
            "timecode_start": block["timecode_start"],
            "timecode_end": block["timecode_end"],
        }
        for block in normalized
    ]
    enriched = any(bool(block.get("enriched")) for block in normalized)
    return SummaryResponse(id=summary.id, blocks=blocks, enriched=enriched)


def _normalize_transcript_segments(raw_segments: list[Any]) -> list[TranscriptSegment]:
    normalized: list[TranscriptSegment] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue

        start_raw = item.get("start") if item.get("start") is not None else item.get("timecode_start")
        end_raw = item.get("end") if item.get("end") is not None else item.get("timecode_end")
        start = _to_non_negative_float(start_raw)
        end = _to_non_negative_float(end_raw)
        if start is not None and end is not None and end < start:
            end = start

        speaker_raw = item.get("speaker")
        speaker = str(speaker_raw).strip() if speaker_raw is not None else None
        if speaker == "":
            speaker = None

        normalized.append(TranscriptSegment(start=start, end=end, text=text, speaker=speaker))
    return normalized


def _build_summary_enrich_prompt(existing_blocks: list[dict[str, Any]], custom_prompt: str | None) -> str:
    base_prompt = custom_prompt.strip() if custom_prompt and custom_prompt.strip() else SUMMARY_ENRICH_PROMPT
    if not existing_blocks:
        return base_prompt

    compact_blocks = [
        {
            "title": block["title"],
            "text": block["text"],
            "type": block["type"],
        }
        for block in existing_blocks
    ]
    summary_json = json.dumps(compact_blocks, ensure_ascii=False)
    return f"{base_prompt}\n\nТекущий конспект:\n{summary_json}"


def _normalize_graph_mentions(raw_mentions: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_mentions, list):
        return []

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[int, float | None]] = set()
    for item in raw_mentions:
        if not isinstance(item, dict):
            continue

        raw_position = item.get("position")
        if raw_position is None:
            raw_position = item.get("position_in_text")
        if isinstance(raw_position, bool):
            continue
        try:
            position = int(raw_position)
        except (TypeError, ValueError):
            continue
        if position < 0:
            continue

        raw_timecode = item.get("timecode")
        timecode: float | None = None
        if raw_timecode is not None:
            parsed_timecode = _to_non_negative_float(raw_timecode)
            if parsed_timecode is not None:
                timecode = round(parsed_timecode, 3)

        key = (position, timecode)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"position": position, "timecode": timecode})

    return normalized


def _normalize_graph_nodes(raw_nodes: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_nodes, list):
        return []

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id", "")).strip()
        label = str(item.get("label", "")).strip()
        if not node_id or not label or node_id in seen_ids:
            continue

        node_type = str(item.get("type", "entity")).strip() or "entity"
        enriched = bool(item.get("enriched", False))
        mentions = _normalize_graph_mentions(item.get("mentions"))

        normalized.append(
            {
                "id": node_id,
                "label": label,
                "type": node_type,
                "enriched": enriched,
                "mentions": mentions,
            }
        )
        seen_ids.add(node_id)

    return normalized


def _normalize_graph_edges(raw_edges: Any, allowed_node_ids: set[str]) -> list[dict[str, str]]:
    if not isinstance(raw_edges, list):
        return []

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_edges:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "")).strip()
        target = str(item.get("target", "")).strip()
        label = str(item.get("label", "related_to")).strip() or "related_to"
        if not source or not target:
            continue
        if source not in allowed_node_ids or target not in allowed_node_ids:
            continue

        key = (source, target, label)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"source": source, "target": target, "label": label})
    return normalized


def _to_graph_response(graph: EntityGraph) -> GraphResponse:
    nodes = _normalize_graph_nodes(list(graph.nodes or []))
    allowed_node_ids = {node["id"] for node in nodes}
    edges = _normalize_graph_edges(list(graph.edges or []), allowed_node_ids)
    return GraphResponse(nodes=nodes, edges=edges, enriched=bool(graph.enriched))


def _normalize_bearer_token(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    token = raw_value.strip()
    if not token:
        return None
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None


def _extract_websocket_token(websocket: WebSocket) -> str | None:
    token = _normalize_bearer_token(websocket.headers.get("authorization"))
    if token:
        return token

    for cookie_name in ("access_token", "token", "authorization"):
        token = _normalize_bearer_token(websocket.cookies.get(cookie_name))
        if token:
            return token

    return _normalize_bearer_token(websocket.query_params.get("token"))


def _extract_request_token(request: Request) -> str | None:
    token = request.headers.get("authorization")
    if token:
        return token

    for cookie_name in ("access_token", "token", "authorization"):
        token = request.cookies.get(cookie_name)
        if token:
            return token

    return request.query_params.get("token")


def _is_websocket_origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    if settings.cors_allow_all:
        return True
    return origin in settings.cors_origins_list


async def _receive_token_from_first_message(websocket: WebSocket) -> str:
    try:
        payload = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated") from None

    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = _normalize_bearer_token(payload.get("token"))
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return token


async def _resolve_websocket_user(
    websocket: WebSocket,
    db: AsyncSession,
    token_override: str | None = None,
) -> User:
    token = _normalize_bearer_token(token_override) or _extract_websocket_token(websocket)
    if not token:
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
    enrichment_enabled: bool = Form(default=False),
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
            enrichment_enabled=enrichment_enabled,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("", response_model=LectureResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_lecture(
    request: Request,
    payload: CreateLectureRequest = Depends(parse_create_lecture_request),
    file: UploadFile = File(default=None),
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

    await _invalidate_lecture_list_cache(user.id)

    if payload.selected_entities:
        logger.info(
            "lecture_selected_entities lecture_id=%s user_id=%s entities=%s",
            lecture.id,
            user.id,
            payload.selected_entities,
        )
    logger.info(
        "lecture_processing_options lecture_id=%s user_id=%s enrichment_enabled=%s",
        lecture.id,
        user.id,
        payload.enrichment_enabled,
    )

    await broadcast_progress(
        lecture.id,
        lecture.processing_progress,
        lecture.status.value if hasattr(lecture.status, "value") else str(lecture.status),
    )

    try:
        await asyncio.to_thread(
            process_lecture_chain.delay,
            str(lecture.id),
            payload.selected_entities,
            payload.enrichment_enabled,
        )
    except Exception:
        lecture.status = LectureStatus.ERROR
        lecture.error_message = "Failed to schedule lecture processing"
        await db.commit()
        logger.exception("Failed to enqueue lecture processing chain lecture_id=%s", lecture.id)
        try:
            await broadcast_progress(
                lecture.id,
                lecture.processing_progress,
                lecture.status.value if hasattr(lecture.status, "value") else str(lecture.status),
            )
        except Exception:
            logger.exception("Failed to broadcast enqueue error state lecture_id=%s", lecture.id)

    return _to_lecture_response(lecture)


@router.get("", response_model=LectureListResponse)
@limiter.limit("10/minute")
async def list_lectures(
    request: Request,
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

    list_index_key = lecture_list_cache_index(user.id)
    observed_cache_version = await cache_get_index_version(list_index_key)
    cache_key = lecture_list_cache_key(user.id, skip, limit, sort_order)
    cached_payload = await cache_get_json(cache_key, index_key=list_index_key)
    if isinstance(cached_payload, dict):
        try:
            cached_response = LectureListResponse.model_validate(cached_payload)
            if not any(_is_non_terminal_lecture_response(item) for item in cached_response.items):
                return cached_response
        except ValidationError:
            logger.warning("Invalid cached lecture list payload for key=%s", cache_key)

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

    response = LectureListResponse(
        items=[_to_lecture_response(item) for item in lectures],
        total=total,
        skip=skip,
        limit=limit,
    )
    has_non_terminal = any(_is_non_terminal_lecture_response(item) for item in response.items)
    if not has_non_terminal:
        await cache_set_json(
            cache_key,
            response.model_dump(mode="json"),
            CACHE_TTL_LIST_SECONDS,
            index_key=list_index_key,
            expected_version=observed_cache_version,
        )
        await cache_add_to_index(
            list_index_key,
            cache_key,
            CACHE_TTL_LIST_SECONDS,
            expected_version=observed_cache_version,
        )
    return response


@router.get("/{lecture_id}/progress")
@limiter.limit("60/minute")
async def get_lecture_progress(
    request: Request,
    lecture_id: uuid.UUID,
    stream: bool = Query(default=False),
) -> Response | dict[str, Any]:
    token = _normalize_bearer_token(_extract_request_token(request))
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        token_payload = decode_token(token)
        user_id = uuid.UUID(str(token_payload.get("user_id")))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from None

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
        lecture = await _get_owned_lecture(db, lecture_id, user.id)
        owner_user_id = user.id

    initial_payload = _lecture_progress_payload(lecture.id, lecture.processing_progress, lecture.status)

    if not stream:
        return initial_payload

    async def sse_event_stream():
        last_progress = initial_payload["processing_progress"]
        last_status = initial_payload["status"]
        yield f"event: progress\ndata: {json.dumps(initial_payload, ensure_ascii=False)}\n\n"

        terminal_statuses = {LectureStatus.DONE.value, LectureStatus.ERROR.value}
        if last_status in terminal_statuses:
            return

        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
        try:
            try:
                await pubsub.subscribe(PROGRESS_CHANNEL)
            except RedisError:
                logger.exception("SSE progress redis subscribe failed lecture_id=%s", lecture_id)
                payload = {
                    "lecture_id": str(lecture_id),
                    "status": "error",
                    "detail": "Progress stream unavailable",
                }
                yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return

            async with AsyncSessionLocal() as db_after_subscribe:
                current_lecture = await db_after_subscribe.get(Lecture, lecture_id)
                if current_lecture is None or current_lecture.user_id != owner_user_id:
                    return
                current_payload = _lecture_progress_payload(
                    current_lecture.id,
                    current_lecture.processing_progress,
                    current_lecture.status,
                )
                current_progress = current_payload["processing_progress"]
                current_status = current_payload["status"]
                if current_progress != last_progress or current_status != last_status:
                    yield f"event: progress\ndata: {json.dumps(current_payload, ensure_ascii=False)}\n\n"
                    last_progress = current_progress
                    last_status = current_status
                if current_status in terminal_statuses:
                    return

            while True:
                if await request.is_disconnected():
                    return

                try:
                    message = await pubsub.get_message(timeout=1.0)
                except RedisError:
                    logger.exception("SSE progress redis read failed lecture_id=%s", lecture_id)
                    payload = {
                        "lecture_id": str(lecture_id),
                        "status": "error",
                        "detail": "Progress stream unavailable",
                    }
                    yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    return

                if not message:
                    await asyncio.sleep(0.05)
                    continue

                try:
                    if not isinstance(message, dict):
                        continue
                    raw_data = message.get("data")
                    if not raw_data:
                        continue
                    if isinstance(raw_data, bytes):
                        raw_data = raw_data.decode("utf-8", errors="ignore")
                    payload = json.loads(raw_data)
                    if not isinstance(payload, dict):
                        continue
                    if str(payload.get("lecture_id", "")).strip() != str(lecture_id):
                        continue

                    current_payload = _lecture_progress_payload(
                        lecture_id=lecture_id,
                        progress=payload.get("progress", payload.get("processing_progress", last_progress)),
                        status_value=payload.get("status", last_status),
                    )
                    current_progress = current_payload["processing_progress"]
                    current_status = current_payload["status"]
                except Exception:
                    logger.exception("Failed to parse SSE progress payload lecture_id=%s", lecture_id)
                    continue

                if current_progress != last_progress or current_status != last_status:
                    yield f"event: progress\ndata: {json.dumps(current_payload, ensure_ascii=False)}\n\n"
                    last_progress = current_progress
                    last_status = current_status

                if current_status in terminal_statuses:
                    return
        finally:
            close_pubsub = getattr(pubsub, "aclose", None) or pubsub.close
            close_client = getattr(redis_client, "aclose", None) or redis_client.close
            close_pubsub_result = close_pubsub()
            if asyncio.iscoroutine(close_pubsub_result):
                await close_pubsub_result
            close_client_result = close_client()
            if asyncio.iscoroutine(close_client_result):
                await close_client_result

    return StreamingResponse(
        sse_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{lecture_id}", response_model=LectureResponse)
@limiter.limit("30/minute")
async def get_lecture(
    request: Request,
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LectureResponse:
    lecture = await db.get(Lecture, lecture_id)
    if lecture is None or lecture.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    response = _to_lecture_response(lecture)

    try:
        history_updated = await record_history_visit(db, user.id, lecture.id)
        if history_updated:
            await db.commit()
            await _invalidate_history_list_cache(user.id)
    except Exception:
        await db.rollback()
        logger.exception("Failed to record lecture history user_id=%s lecture_id=%s", user.id, lecture.id)

    return response


@router.get("/{lecture_id}/summary", response_model=SummaryResponse)
async def get_lecture_summary(
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SummaryResponse:
    lecture = await _get_owned_lecture(db, lecture_id, user.id)
    summary_index_key = summary_cache_index(lecture.id)
    observed_cache_version = await cache_get_index_version(summary_index_key)
    cache_key = summary_cache_key(user.id, lecture.id, lecture.updated_at)
    cached_payload = await cache_get_json(cache_key, index_key=summary_index_key)
    if isinstance(cached_payload, dict):
        try:
            return SummaryResponse.model_validate(cached_payload)
        except ValidationError:
            logger.warning("Invalid cached summary payload for key=%s", cache_key)

    summary = (
        await db.execute(select(Summary).where(Summary.lecture_id == lecture.id))
    ).scalar_one_or_none()
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Summary not found")
    response = _to_summary_response(summary)
    await cache_set_json(
        cache_key,
        response.model_dump(mode="json"),
        CACHE_TTL_ONE_DAY_SECONDS,
        index_key=summary_index_key,
        expected_version=observed_cache_version,
    )
    await cache_add_to_index(
        summary_index_key,
        cache_key,
        CACHE_TTL_ONE_DAY_SECONDS,
        expected_version=observed_cache_version,
    )
    return response


@router.get("/{lecture_id}/transcript", response_model=TranscriptResponse)
async def get_lecture_transcript(
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TranscriptResponse:
    lecture = await _get_owned_lecture(db, lecture_id, user.id)
    transcript = (
        await db.execute(select(Transcript).where(Transcript.lecture_id == lecture.id))
    ).scalar_one_or_none()
    if transcript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript not found")

    return TranscriptResponse(
        lecture_id=lecture.id,
        full_text=str(transcript.full_text or ""),
        segments=_normalize_transcript_segments(list(transcript.segments or [])),
    )


@router.get("/{lecture_id}/export")
async def export_lecture_summary(
    lecture_id: uuid.UUID,
    export_format: Literal["md", "pdf", "json"] = Query(alias="format"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    lecture = await _get_owned_lecture(db, lecture_id, user.id)
    summary = (
        await db.execute(select(Summary).where(Summary.lecture_id == lecture.id))
    ).scalar_one_or_none()
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Summary not found")

    base_filename = f"lecture-{lecture.id}-summary"
    if export_format == "md":
        content = export_summary_to_markdown(summary)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.md"'},
        )
    if export_format == "json":
        content = export_summary_to_json(summary)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.json"'},
        )

    try:
        content = export_summary_to_pdf(summary)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{base_filename}.pdf"'},
    )


@router.get("/{lecture_id}/graph/export")
async def export_lecture_graph(
    lecture_id: uuid.UUID,
    export_format: Literal["json", "png"] = Query(alias="format"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    lecture = await _get_owned_lecture(db, lecture_id, user.id)
    graph = (
        await db.execute(select(EntityGraph).where(EntityGraph.lecture_id == lecture.id))
    ).scalar_one_or_none()
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity graph not found")

    base_filename = f"lecture-{lecture.id}-graph"
    if export_format == "json":
        content = export_graph_to_json(graph)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.json"'},
        )

    try:
        content = await asyncio.to_thread(export_graph_to_image, graph)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{base_filename}.png"'},
    )


@router.post("/{lecture_id}/summary/enrich", response_model=SummaryResponse, status_code=status.HTTP_200_OK)
async def enrich_lecture_summary(
    lecture_id: uuid.UUID,
    llm_config: LLMRequestConfig | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SummaryResponse:
    lecture = await _get_owned_lecture(db, lecture_id, user.id)
    transcript = (
        await db.execute(select(Transcript).where(Transcript.lecture_id == lecture.id))
    ).scalar_one_or_none()
    if transcript is None or not str(transcript.full_text or "").strip():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript not found")

    summary = (
        await db.execute(select(Summary).where(Summary.lecture_id == lecture.id))
    ).scalar_one_or_none()
    existing_blocks = _normalize_summary_blocks(
        list(summary.content or []) if summary else [],
        default_enriched=False,
    )

    request_llm_config = llm_config.model_dump(exclude_none=True) if llm_config else {}
    custom_prompt_raw = request_llm_config.get("prompt")
    custom_prompt = str(custom_prompt_raw) if custom_prompt_raw is not None else None
    request_llm_config["prompt"] = _build_summary_enrich_prompt(existing_blocks, custom_prompt)

    try:
        enriched_payload = await asyncio.to_thread(
            summarize_segment,
            str(transcript.full_text),
            request_llm_config,
        )
    except (LLMServiceError, ValueError):
        logger.exception("Summary enrichment failed lecture_id=%s user_id=%s", lecture_id, user.id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Summary enrichment failed") from None

    incoming_blocks = _normalize_summary_blocks(
        list(enriched_payload.get("blocks", [])),
        default_enriched=True,
    )
    if not incoming_blocks:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Summary enrichment returned empty blocks",
        )

    merged_blocks = _merge_summary_blocks(existing_blocks, incoming_blocks)
    if not merged_blocks:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Summary enrichment returned no usable blocks",
        )

    range_start, range_end = _summary_timecode_range(merged_blocks)
    try:
        if summary is None:
            summary = Summary(
                lecture_id=lecture.id,
                content=merged_blocks,
                timecode_start=range_start,
                timecode_end=range_end,
            )
            db.add(summary)
        else:
            summary.content = merged_blocks
            summary.timecode_start = range_start
            summary.timecode_end = range_end
        await db.commit()
        await db.refresh(summary)
    except SQLAlchemyError:
        await db.rollback()
        logger.exception("Failed to persist enriched summary lecture_id=%s user_id=%s", lecture_id, user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist enriched summary",
        ) from None

    await _invalidate_summary_cache(lecture.id)
    return _to_summary_response(summary)


@router.get("/{lecture_id}/graph", response_model=GraphResponse)
async def get_lecture_graph(
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GraphResponse:
    lecture = await _get_owned_lecture(db, lecture_id, user.id)
    graph_index_key = graph_cache_index(lecture.id)
    observed_cache_version = await cache_get_index_version(graph_index_key)
    cache_key = graph_cache_key(user.id, lecture.id, lecture.updated_at)
    cached_payload = await cache_get_json(cache_key, index_key=graph_index_key)
    if isinstance(cached_payload, dict):
        try:
            return GraphResponse.model_validate(cached_payload)
        except ValidationError:
            logger.warning("Invalid cached graph payload for key=%s", cache_key)

    graph = (
        await db.execute(
            select(EntityGraph).where(EntityGraph.lecture_id == lecture.id)
        )
    ).scalar_one_or_none()
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity graph not found")
    response = _to_graph_response(graph)
    await cache_set_json(
        cache_key,
        response.model_dump(mode="json"),
        CACHE_TTL_ONE_DAY_SECONDS,
        index_key=graph_index_key,
        expected_version=observed_cache_version,
    )
    await cache_add_to_index(
        graph_index_key,
        cache_key,
        CACHE_TTL_ONE_DAY_SECONDS,
        expected_version=observed_cache_version,
    )
    return response


@router.post("/{lecture_id}/graph/enrich", response_model=GraphResponse, status_code=status.HTTP_200_OK)
async def enrich_lecture_graph(
    lecture_id: uuid.UUID,
    llm_config: LLMRequestConfig | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GraphResponse:
    lecture = await _get_owned_lecture(db, lecture_id, user.id)

    graph = (
        await db.execute(
            select(EntityGraph).where(EntityGraph.lecture_id == lecture.id)
        )
    ).scalar_one_or_none()
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity graph not found")

    request_llm_config = llm_config.model_dump(exclude_none=True) if llm_config else {}
    base_nodes = list(graph.nodes or [])
    base_edges = list(graph.edges or [])

    try:
        enriched_payload = await asyncio.to_thread(enrich_graph, base_nodes, base_edges, request_llm_config)
    except LLMServiceError:
        logger.exception("Graph enrichment failed for lecture_id=%s user_id=%s", lecture_id, user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Graph enrichment failed",
        ) from None

    max_retries = 3
    locked_graph: EntityGraph | None = None
    for attempt in range(1, max_retries + 1):
        try:
            locked_graph = (
                await db.execute(
                    select(EntityGraph)
                    .where(EntityGraph.lecture_id == lecture.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_graph is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity graph not found")

            merged_payload = merge_graph_data(
                list(locked_graph.nodes or []),
                list(locked_graph.edges or []),
                list(enriched_payload.get("nodes", [])),
                list(enriched_payload.get("edges", [])),
            )
            locked_graph.nodes = list(merged_payload.get("nodes", []))
            locked_graph.edges = list(merged_payload.get("edges", []))
            locked_graph.enriched = True
            await db.commit()
            await db.refresh(locked_graph)
            break
        except HTTPException:
            await db.rollback()
            raise
        except OperationalError:
            await db.rollback()
            if attempt == max_retries:
                logger.exception(
                    "Concurrent graph update conflict lecture_id=%s user_id=%s",
                    lecture_id,
                    user.id,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Concurrent graph update conflict",
                ) from None
            await asyncio.sleep(0.1 * attempt)
        except SQLAlchemyError:
            await db.rollback()
            logger.exception(
                "Failed to persist enriched graph lecture_id=%s user_id=%s",
                lecture_id,
                user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to persist enriched graph",
            ) from None

    if locked_graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Entity graph not found")
    await _invalidate_graph_cache(lecture.id)
    return _to_graph_response(locked_graph)


@router.delete("/{lecture_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def delete_lecture(
    request: Request,
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    lecture = await db.get(Lecture, lecture_id)
    if lecture is None or lecture.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    await db.execute(delete(Favourite).where(Favourite.lecture_id == lecture_id))
    await db.execute(delete(History).where(History.lecture_id == lecture_id))
    await db.execute(delete(Transcript).where(Transcript.lecture_id == lecture_id))
    await db.execute(delete(Summary).where(Summary.lecture_id == lecture_id))
    await db.execute(delete(EntityGraph).where(EntityGraph.lecture_id == lecture_id))
    await db.delete(lecture)
    await db.commit()
    await _invalidate_lecture_list_cache(user.id)
    await _invalidate_history_list_cache(user.id)
    await _invalidate_summary_cache(lecture_id)
    await _invalidate_graph_cache(lecture_id)

    try:
        delete_lecture_media(settings.MEDIA_ROOT, lecture_id)
    except OSError:
        logger.exception("Failed at media deletion stage for lecture_id=%s", lecture_id)

    return {"status": "deleted", "lecture_id": str(lecture_id)}


@ws_router.websocket("/ws/{lecture_id}")
async def lecture_progress_ws(websocket: WebSocket, lecture_id: uuid.UUID) -> None:
    if not _is_websocket_origin_allowed(websocket):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    accepted = False
    subscribed = False
    token = _extract_websocket_token(websocket)

    if token is None:
        await websocket.accept()
        accepted = True
        try:
            token = await _receive_token_from_first_message(websocket)
        except HTTPException as exc:
            await websocket.close(code=4401, reason=str(exc.detail))
            return

    async with AsyncSessionLocal() as db:
        try:
            user = await _resolve_websocket_user(websocket, db, token_override=token)
        except HTTPException as exc:
            close_code = 4401 if exc.status_code == status.HTTP_401_UNAUTHORIZED else 4403
            await websocket.close(code=close_code, reason=str(exc.detail))
            return

        lecture = await db.get(Lecture, lecture_id)
        if lecture is None:
            await websocket.close(code=4403, reason="Lecture not found")
            return
        if lecture.user_id != user.id:
            await websocket.close(code=4403, reason="Forbidden")
            return

    if not accepted:
        await websocket.accept()
        accepted = True

    await register_subscription(lecture_id, websocket)
    subscribed = True
    await websocket.send_json({"type": "subscribed", "lecture_id": str(lecture_id)})

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if subscribed:
            await unregister_subscription(lecture_id, websocket)
