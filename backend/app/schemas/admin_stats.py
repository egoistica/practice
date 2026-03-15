from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class TopEntityStat(BaseModel):
    label: str
    mentions: int


class AdminOverviewStatsResponse(BaseModel):
    users_count: int
    lectures_count: int
    storage_size_bytes: int
    top_entities: list[TopEntityStat]


class DailyUserRegistrationsStat(BaseModel):
    date: date
    new_users: int


class AdminUsersStatsResponse(BaseModel):
    start_date: date
    end_date: date
    total_new_users: int
    items: list[DailyUserRegistrationsStat]
