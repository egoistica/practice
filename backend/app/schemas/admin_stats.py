from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

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


class DailyVisitStat(BaseModel):
    date: date
    visits: int


class LectureVisitStat(BaseModel):
    lecture_id: UUID
    lecture_title: str
    visits: int
    last_visited_at: datetime


class AdminVisitsStatsResponse(BaseModel):
    start_date: date
    end_date: date
    user_id: UUID | None
    total_visits: int
    daily_visits: list[DailyVisitStat]
    lecture_visits: list[LectureVisitStat]


class AdminDbLectureStat(BaseModel):
    lecture_id: UUID
    title: str
    username: str
    status: str
    file_size_bytes: int


class AdminDbStatsResponse(BaseModel):
    users_count: int
    lectures_count: int
    files_size_bytes: int
    top_entities: list[TopEntityStat]
    lectures: list[AdminDbLectureStat]
