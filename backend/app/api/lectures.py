from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, WebSocket, status
from fastapi.websockets import WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_db
from app.core.security import decode_token
from app.models.entity_graph import EntityGraph
from app.models.favourite import Favourite
from app.models.history import History
from app.models.lecture import Lecture, LectureMode, LectureSourceType, LectureStatus
from app.models.summary import Summary
from app.models.transcript import Transcript
from app.models.user import User
from app.schemas.lecture import CreateLectureRequest, LLMRequestConfig, LectureListResponse, LectureResponse
from app.services.file_service import delete_lecture_media, save_uploaded_file
from app.services.history_service import record_history_visit
from app.services.llm_service import LLMServiceError, enrich_graph, merge_graph_data
from app.services.progress_service import (
    broadcast_progress,
    register_subscription,
    unregister_subscription,
)
from app.tasks.process_lecture import process_lecture_chain

router = APIRouter(prefix="/lectures", tags=["lectures"])
ws_router = APIRouter(tags=["lectures"])
logger = logging.getLogger(__name__)


def _to_lecture_response(lecture: Lecture) -> LectureResponse:
    return LectureResponse(
        id=lecture.id,
        title=lecture.title,
        status=str(lecture.status.value if hasattr(lecture.status, "value") else lecture.status),
        processing_progress=lecture.processing_progress,
        created_at=lecture.created_at,
    )


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

    try:
        process_lecture_chain.delay(str(lecture.id), payload.selected_entities)
    except Exception:
        lecture.status = LectureStatus.ERROR
        lecture.error_message = "Failed to schedule lecture processing"
        await db.commit()
        await broadcast_progress(
            lecture.id,
            lecture.processing_progress,
            lecture.status.value if hasattr(lecture.status, "value") else str(lecture.status),
        )
        logger.exception("Failed to enqueue lecture processing chain lecture_id=%s", lecture.id)

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

    response = _to_lecture_response(lecture)

    try:
        history_updated = await record_history_visit(db, user.id, lecture.id)
        if history_updated:
            await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to record lecture history user_id=%s lecture_id=%s", user.id, lecture.id)

    return response


@router.post("/{lecture_id}/graph/enrich", status_code=status.HTTP_200_OK)
async def enrich_lecture_graph(
    lecture_id: uuid.UUID,
    llm_config: LLMRequestConfig | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    lecture = await db.get(Lecture, lecture_id)
    if lecture is None or lecture.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    graph = (
        await db.execute(
            select(EntityGraph).where(EntityGraph.lecture_id == lecture_id)
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
                    .where(EntityGraph.lecture_id == lecture_id)
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

    return {
        "nodes": locked_graph.nodes if locked_graph else [],
        "edges": locked_graph.edges if locked_graph else [],
        "enriched": bool(locked_graph.enriched) if locked_graph else False,
    }


@router.delete("/{lecture_id}", status_code=status.HTTP_200_OK)
async def delete_lecture(
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
