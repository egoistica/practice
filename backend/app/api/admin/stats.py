from __future__ import annotations

from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_db, require_admin
from app.models.entity_graph import EntityGraph
from app.models.lecture import Lecture
from app.models.user import User
from app.schemas.admin_stats import AdminOverviewStatsResponse, TopEntityStat

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


@router.get("/overview", response_model=AdminOverviewStatsResponse)
async def get_admin_overview_stats(
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(require_admin),
) -> AdminOverviewStatsResponse:
    users_count = int((await db.execute(select(func.count()).select_from(User))).scalar_one())
    lectures_count = int((await db.execute(select(func.count()).select_from(Lecture))).scalar_one())

    nodes_rows = (await db.execute(select(EntityGraph.nodes))).scalars().all()
    entities_counter: Counter[str] = Counter()
    for nodes in nodes_rows:
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
    storage_size = _storage_size_bytes(media_path)

    return AdminOverviewStatsResponse(
        users_count=users_count,
        lectures_count=lectures_count,
        storage_size_bytes=storage_size,
        top_entities=top_entities,
    )
