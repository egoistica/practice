from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

from app.models.lecture import LectureMode, LectureSourceType


class CreateLectureRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    mode: LectureMode = LectureMode.INSTANT
    source_type: LectureSourceType
    source_url: AnyHttpUrl | None = None
    selected_entities: list[str] | None = None

    @model_validator(mode="after")
    def validate_source_fields(self) -> "CreateLectureRequest":
        if self.source_type == LectureSourceType.URL and self.source_url is None:
            raise ValueError("source_url is required when source_type=url")
        return self


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
