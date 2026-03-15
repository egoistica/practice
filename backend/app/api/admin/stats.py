from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_db, require_admin
from app.models.entity_graph import EntityGraph
from app.models.history import History
from app.models.lecture import Lecture
from app.models.user import User
from app.schemas.admin_stats import (
    AdminDbLectureStat,
    AdminDbStatsResponse,
    AdminOverviewStatsResponse,
    AdminVisitsStatsResponse,
    AdminUsersStatsResponse,
    DailyVisitStat,
    DailyUserRegistrationsStat,
    LectureVisitStat,
    TopEntityStat,
)

router = APIRouter(prefix="/admin/stats", tags=["admin-stats"])


def _storage_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total_size = 0
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            total_size += file_path.stat().st_size
        except OSError:
            continue
    return total_size


def _normalize_entity_label(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.strip().split())


def _date_series(start_date: date, end_date: date) -> list[date]:
    days: list[date] = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


def _resolve_lecture_file_path(media_root: Path, raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    try:
        resolved_media_root = media_root.resolve(strict=False)
    except OSError:
        return None

    file_path = Path(raw_path)
    candidate = file_path if file_path.is_absolute() else (resolved_media_root / file_path)

    try:
        resolved_candidate = candidate.resolve(strict=False)
    except OSError:
        return None

    try:
        resolved_candidate.relative_to(resolved_media_root)
    except ValueError:
        return None

    return resolved_candidate


def _collect_lecture_file_sizes(
    media_root: Path,
    lecture_file_rows: list[tuple[UUID, str | None]],
) -> tuple[int, dict[UUID, int]]:
    total_size = 0
    size_by_lecture: dict[UUID, int] = {}

    for lecture_id, raw_file_path in lecture_file_rows:
        lecture_size = 0
        resolved = _resolve_lecture_file_path(media_root, raw_file_path)
        if resolved and resolved.is_file():
            try:
                lecture_size = int(resolved.stat().st_size)
            except OSError:
                lecture_size = 0
        size_by_lecture[lecture_id] = lecture_size
        total_size += lecture_size

    return total_size, size_by_lecture


@router.get("/overview", response_model=AdminOverviewStatsResponse)
async def get_admin_overview_stats(
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(require_admin),
) -> AdminOverviewStatsResponse:
    users_count = int((await db.execute(select(func.count()).select_from(User))).scalar_one())
    lectures_count = int((await db.execute(select(func.count()).select_from(Lecture))).scalar_one())

    entities_counter: Counter[str] = Counter()
    nodes_stream = await db.stream(select(EntityGraph.nodes))
    async for nodes in nodes_stream.scalars():
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            normalized_label = _normalize_entity_label(node.get("label"))
            if not normalized_label:
                continue
            entities_counter[normalized_label] += 1

    top_entities = [
        TopEntityStat(label=label, mentions=count)
        for label, count in entities_counter.most_common(5)
    ]

    media_path = Path(settings.MEDIA_ROOT)
    storage_size = await asyncio.to_thread(_storage_size_bytes, media_path)

    return AdminOverviewStatsResponse(
        users_count=users_count,
        lectures_count=lectures_count,
        storage_size_bytes=storage_size,
        top_entities=top_entities,
    )


@router.get("/users", response_model=AdminUsersStatsResponse)
async def get_admin_users_stats(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(require_admin),
) -> AdminUsersStatsResponse:
    today = date.today()
    resolved_end = end_date or today
    resolved_start = start_date or (resolved_end - timedelta(days=29))

    if resolved_start > resolved_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be less than or equal to end_date",
        )

    created_date = func.date(User.created_at)
    rows = (
        await db.execute(
            select(created_date.label("created_day"), func.count(User.id).label("new_users"))
            .where(created_date >= resolved_start, created_date <= resolved_end)
            .group_by(created_date)
            .order_by(created_date.asc())
        )
    ).all()

    counts_by_day = {
        row.created_day: int(row.new_users)
        for row in rows
        if row.created_day is not None
    }
    items = [
        DailyUserRegistrationsStat(date=day, new_users=counts_by_day.get(day, 0))
        for day in _date_series(resolved_start, resolved_end)
    ]
    total_new_users = sum(item.new_users for item in items)

    return AdminUsersStatsResponse(
        start_date=resolved_start,
        end_date=resolved_end,
        total_new_users=total_new_users,
        items=items,
    )


@router.get("/visits", response_model=AdminVisitsStatsResponse)
async def get_admin_visits_stats(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(require_admin),
) -> AdminVisitsStatsResponse:
    today = date.today()
    resolved_end = end_date or today
    resolved_start = start_date or (resolved_end - timedelta(days=29))

    if resolved_start > resolved_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be less than or equal to end_date",
        )

    visited_date = func.date(History.visited_at)
    filters = [visited_date >= resolved_start, visited_date <= resolved_end]
    if user_id is not None:
        filters.append(History.user_id == user_id)

    daily_rows = (
        await db.execute(
            select(visited_date.label("visited_day"), func.count(History.id).label("visits"))
            .where(*filters)
            .group_by(visited_date)
            .order_by(visited_date.asc())
        )
    ).all()

    counts_by_day = {
        row.visited_day: int(row.visits)
        for row in daily_rows
        if row.visited_day is not None
    }
    daily_visits = [
        DailyVisitStat(date=day, visits=counts_by_day.get(day, 0))
        for day in _date_series(resolved_start, resolved_end)
    ]

    lecture_rows = (
        await db.execute(
            select(
                History.lecture_id.label("lecture_id"),
                Lecture.title.label("lecture_title"),
                func.count(History.id).label("visits"),
                func.max(History.visited_at).label("last_visited_at"),
            )
            .join(Lecture, Lecture.id == History.lecture_id)
            .where(*filters)
            .group_by(History.lecture_id, Lecture.title)
            .order_by(func.count(History.id).desc(), func.max(History.visited_at).desc())
        )
    ).all()

    lecture_visits = [
        LectureVisitStat(
            lecture_id=row.lecture_id,
            lecture_title=row.lecture_title,
            visits=int(row.visits),
            last_visited_at=row.last_visited_at,
        )
        for row in lecture_rows
        if row.lecture_id is not None and row.last_visited_at is not None
    ]

    return AdminVisitsStatsResponse(
        start_date=resolved_start,
        end_date=resolved_end,
        user_id=user_id,
        total_visits=sum(item.visits for item in daily_visits),
        daily_visits=daily_visits,
        lecture_visits=lecture_visits,
    )


@router.get("/db", response_model=AdminDbStatsResponse)
async def get_admin_db_stats(
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(require_admin),
) -> AdminDbStatsResponse:
    users_count = int((await db.execute(select(func.count()).select_from(User))).scalar_one())
    lectures_count = int((await db.execute(select(func.count()).select_from(Lecture))).scalar_one())

    entities_counter: Counter[str] = Counter()
    nodes_stream = await db.stream(select(EntityGraph.nodes))
    async for nodes in nodes_stream.scalars():
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            normalized_label = _normalize_entity_label(node.get("label"))
            if not normalized_label:
                continue
            entities_counter[normalized_label] += 1

    top_entities = [
        TopEntityStat(label=label, mentions=count)
        for label, count in entities_counter.most_common(10)
    ]

    lecture_rows = (
        await db.execute(
            select(
                Lecture.id.label("lecture_id"),
                Lecture.title.label("title"),
                Lecture.status.label("status"),
                Lecture.file_path.label("file_path"),
                User.username.label("username"),
            )
            .join(User, User.id == Lecture.user_id)
            .order_by(Lecture.created_at.desc())
        )
    ).all()

    media_root = Path(settings.MEDIA_ROOT)
    lecture_file_rows = [(row.lecture_id, row.file_path) for row in lecture_rows if row.lecture_id is not None]
    files_size_bytes, size_by_lecture = await asyncio.to_thread(
        _collect_lecture_file_sizes,
        media_root,
        lecture_file_rows,
    )

    lectures = [
        AdminDbLectureStat(
            lecture_id=row.lecture_id,
            title=row.title,
            username=row.username or "unknown",
            status=row.status.value if hasattr(row.status, "value") else str(row.status),
            file_size_bytes=size_by_lecture.get(row.lecture_id, 0),
        )
        for row in lecture_rows
        if row.lecture_id is not None
    ]

    return AdminDbStatsResponse(
        users_count=users_count,
        lectures_count=lectures_count,
        files_size_bytes=files_size_bytes,
        top_entities=top_entities,
        lectures=lectures,
    )
