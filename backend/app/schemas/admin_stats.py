from __future__ import annotations

from pydantic import BaseModel


class TopEntityStat(BaseModel):
    label: str
    mentions: int


class AdminOverviewStatsResponse(BaseModel):
    users_count: int
    lectures_count: int
    storage_size_bytes: int
    top_entities: list[TopEntityStat]
