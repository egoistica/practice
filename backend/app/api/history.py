from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import (
    CACHE_TTL_LIST_SECONDS,
    cache_add_to_index,
    cache_get_json,
    cache_get_index_version,
    cache_invalidate_index,
    cache_set_json,
    history_list_cache_index,
    history_list_cache_key,
)
from app.core.dependencies import get_current_user, get_db
from app.models.history import History
from app.models.lecture import Lecture
from app.models.user import User
from app.schemas.engagement import HistoryLectureResponse, HistoryListResponse

router = APIRouter(prefix="/history", tags=["history"])
TERMINAL_HISTORY_STATUSES = {"done", "error", "completed", "failed"}
SHORT_LIVED_NON_TERMINAL_CACHE_TTL_SECONDS = 10


def _to_history_response(history: History, lecture: Lecture) -> HistoryLectureResponse:
    return HistoryLectureResponse(
        lecture_id=lecture.id,
        title=lecture.title,
        status=lecture.status.value if hasattr(lecture.status, "value") else str(lecture.status),
        processing_progress=lecture.processing_progress,
        created_at=lecture.created_at,
        visited_at=history.visited_at,
    )


def _is_non_terminal_history_item(item: HistoryLectureResponse) -> bool:
    status_value = str(item.status or "").strip().lower()
    if status_value not in TERMINAL_HISTORY_STATUSES:
        return True
    return int(item.processing_progress) < 100


@router.get("", response_model=HistoryListResponse)
async def list_history(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> HistoryListResponse:
    index_key = history_list_cache_index(user.id)
    observed_cache_version = await cache_get_index_version(index_key)
    cache_key = history_list_cache_key(user.id, skip, limit, sort_order)
    cached_payload = await cache_get_json(cache_key, index_key=index_key)
    if isinstance(cached_payload, dict):
        try:
            cached_response = HistoryListResponse.model_validate(cached_payload)
            return cached_response
        except ValidationError:
            pass

    order_clause = History.visited_at.asc() if sort_order == "asc" else History.visited_at.desc()
    rows = (
        await db.execute(
            select(History, Lecture)
            .join(Lecture, History.lecture_id == Lecture.id)
            .where(History.user_id == user.id, Lecture.user_id == user.id)
            .order_by(order_clause)
            .offset(skip)
            .limit(limit)
        )
    ).all()
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(History)
                .where(History.user_id == user.id)
            )
        ).scalar_one()
    )
    response = HistoryListResponse(
        items=[_to_history_response(history, lecture) for history, lecture in rows],
        total=total,
        skip=skip,
        limit=limit,
    )
    has_non_terminal = any(_is_non_terminal_history_item(item) for item in response.items)
    if has_non_terminal:
        await cache_set_json(
            cache_key,
            response.model_dump(mode="json"),
            SHORT_LIVED_NON_TERMINAL_CACHE_TTL_SECONDS,
            index_key=index_key,
            expected_version=observed_cache_version,
        )
        await cache_add_to_index(
            index_key,
            cache_key,
            SHORT_LIVED_NON_TERMINAL_CACHE_TTL_SECONDS,
            expected_version=observed_cache_version,
        )
    else:
        await cache_set_json(
            cache_key,
            response.model_dump(mode="json"),
            CACHE_TTL_LIST_SECONDS,
            index_key=index_key,
            expected_version=observed_cache_version,
        )
        await cache_add_to_index(
            index_key,
            cache_key,
            CACHE_TTL_LIST_SECONDS,
            expected_version=observed_cache_version,
        )
    return response


@router.delete("/{lecture_id}", status_code=status.HTTP_200_OK)
async def remove_from_history(
    lecture_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    result = await db.execute(
        delete(History).where(
            History.user_id == user.id,
            History.lecture_id == lecture_id,
        )
    )
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="History entry not found")

    await db.commit()
    await cache_invalidate_index(history_list_cache_index(user.id))
    return {"status": "deleted", "lecture_id": str(lecture_id)}
