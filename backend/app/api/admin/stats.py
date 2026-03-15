from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_db, require_admin
from app.models.entity_graph import EntityGraph
from app.models.lecture import Lecture
from app.models.user import User
from app.schemas.admin_stats import (
    AdminOverviewStatsResponse,
    AdminUsersStatsResponse,
    DailyUserRegistrationsStat,
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
