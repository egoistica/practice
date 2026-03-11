from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SummaryBlock(BaseModel):
    title: str
    text: str
    type: str
    timecode_start: float | None = None
    timecode_end: float | None = None


class SummaryResponse(BaseModel):
    id: UUID
    blocks: list[SummaryBlock]
    enriched: bool


class TranscriptSegment(BaseModel):
    start: float | None = None
    end: float | None = None
    text: str
    speaker: str | None = None


class TranscriptResponse(BaseModel):
    lecture_id: UUID
    full_text: str
    segments: list[TranscriptSegment]
