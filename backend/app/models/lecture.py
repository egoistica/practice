from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LectureSourceType(str, enum.Enum):
    FILE = "file"
    URL = "url"


class LectureStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


class LectureMode(str, enum.Enum):
    INSTANT = "instant"
    REALTIME = "realtime"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [item.value for item in enum_cls]


class Lecture(Base):
    __tablename__ = "lecture"
    __table_args__ = (
        CheckConstraint("processing_progress >= 0 AND processing_progress <= 100", name="ck_lecture_processing_progress_range"),
        CheckConstraint(
            "(source_type != 'url' OR source_url IS NOT NULL) AND (source_type != 'file' OR file_path IS NOT NULL)",
            name="ck_lecture_source_type_payload",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[LectureSourceType] = mapped_column(
        Enum(LectureSourceType, name="lecture_source_type", values_callable=_enum_values),
        nullable=False,
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[LectureStatus] = mapped_column(
        Enum(LectureStatus, name="lecture_status", values_callable=_enum_values),
        nullable=False,
        default=LectureStatus.PENDING,
        server_default=LectureStatus.PENDING.value,
    )
    mode: Mapped[LectureMode] = mapped_column(
        Enum(LectureMode, name="lecture_mode", values_callable=_enum_values),
        nullable=False,
        default=LectureMode.INSTANT,
        server_default=LectureMode.INSTANT.value,
    )
    processing_progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    realtime_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

