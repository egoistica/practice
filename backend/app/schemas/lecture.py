from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.lecture import LectureMode, LectureSourceType


class CreateLectureRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    mode: LectureMode = LectureMode.INSTANT
    source_type: LectureSourceType
    source_url: str | None = None
    selected_entities: list[str] | None = None


class LectureResponse(BaseModel):
    id: UUID
    title: str
    status: str
    processing_progress: int
    created_at: datetime


class LectureListResponse(BaseModel):
    items: list[LectureResponse]
    total: int
    skip: int
    limit: int
