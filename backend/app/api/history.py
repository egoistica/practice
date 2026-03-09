from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.history import History
from app.models.lecture import Lecture
from app.models.user import User
from app.schemas.engagement import HistoryLectureResponse, HistoryListResponse

router = APIRouter(prefix="/history", tags=["history"])


def _to_history_response(history: History, lecture: Lecture) -> HistoryLectureResponse:
    return HistoryLectureResponse(
        lecture_id=lecture.id,
        title=lecture.title,
        status=lecture.status.value if hasattr(lecture.status, "value") else str(lecture.status),
        processing_progress=lecture.processing_progress,
        created_at=lecture.created_at,
        visited_at=history.visited_at,
    )


@router.get("", response_model=HistoryListResponse)
async def list_history(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> HistoryListResponse:
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
    return HistoryListResponse(
        items=[_to_history_response(history, lecture) for history, lecture in rows],
        total=total,
        skip=skip,
        limit=limit,
    )
