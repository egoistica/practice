from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Summary(Base):
    __tablename__ = "summary"
    __table_args__ = (
        CheckConstraint(
            "(timecode_start IS NULL OR timecode_start >= 0) AND "
            "(timecode_end IS NULL OR timecode_end >= 0) AND "
            "(timecode_start IS NULL OR timecode_end IS NULL OR timecode_end >= timecode_start)",
            name="ck_summary_timecode_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lecture_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lecture.id"), nullable=False, unique=True, index=True)
    content: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    timecode_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    timecode_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

