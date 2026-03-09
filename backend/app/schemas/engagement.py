from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class FavouriteLectureResponse(BaseModel):
    lecture_id: UUID
    title: str
    status: str
    processing_progress: int
    created_at: datetime
    favourited_at: datetime


class FavouritesListResponse(BaseModel):
    items: list[FavouriteLectureResponse]
    total: int
    skip: int
    limit: int


class HistoryLectureResponse(BaseModel):
    lecture_id: UUID
    title: str
    status: str
    processing_progress: int
    created_at: datetime
    visited_at: datetime


class HistoryListResponse(BaseModel):
    items: list[HistoryLectureResponse]
    total: int
    skip: int
    limit: int
