from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from celery import chain, shared_task
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.entity_graph import EntityGraph
from app.models.lecture import Lecture, LectureSourceType, LectureStatus
from app.models.summary import Summary
from app.models.transcript import Transcript
from app.services.llm_service import extract_entities, summarize_segment
from app.services.progress_service import broadcast_progress_sync
from app.services.text_processing import segment_text
from app.services.transcription_service import transcribe_audio
from app.services.video_service import (
    download_video,
    extract_audio,
    get_video_duration,
    get_video_thumbnail,
)

logger = logging.getLogger(__name__)
_NO_VALUE = object()


def _parse_lecture_uuid(lecture_id: str | uuid.UUID) -> uuid.UUID:
    try:
        return uuid.UUID(str(lecture_id))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid lecture_id: {lecture_id}") from exc


def _lecture_dir(lecture_uuid: uuid.UUID) -> Path:
    return Path(settings.MEDIA_ROOT) / str(lecture_uuid)


def _to_abs_media_path(lecture_uuid: uuid.UUID, raw_path: str | None) -> str | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return str(path)
    return str(_lecture_dir(lecture_uuid) / path.name)


def _to_rel_media_path(lecture_uuid: uuid.UUID, abs_path: str | None) -> str | None:
    if not abs_path:
        return None
    path = Path(abs_path)
    if not path.is_absolute():
        return str(path)
    return str(Path(str(lecture_uuid)) / path.name)


async def _get_lecture_async(lecture_uuid: uuid.UUID) -> Lecture:
    async with AsyncSessionLocal() as db:
        lecture = await db.get(Lecture, lecture_uuid)
        if lecture is None:
            raise ValueError(f"Lecture not found: {lecture_uuid}")
        return lecture


def _get_lecture_sync(lecture_uuid: uuid.UUID) -> Lecture:
    return asyncio.run(_get_lecture_async(lecture_uuid))


async def _update_lecture_state_async(
    lecture_uuid: uuid.UUID,
    *,
    status: LectureStatus | None = None,
    progress: int | None = None,
    error_message: str | None | object = _NO_VALUE,
    file_path: str | None = None,
    duration: float | None = None,
    thumbnail_path: str | None = None,
) -> tuple[LectureStatus, int]:
    async with AsyncSessionLocal() as db:
        lecture = await db.get(Lecture, lecture_uuid)
        if lecture is None:
            raise ValueError(f"Lecture not found: {lecture_uuid}")

        if status is not None:
            lecture.status = status
        if progress is not None:
            lecture.processing_progress = max(0, min(100, int(progress)))
        if error_message is not _NO_VALUE:
            lecture.error_message = error_message if isinstance(error_message, str) else None
        if file_path is not None:
            lecture.file_path = file_path
        if duration is not None:
            lecture.duration = max(float(duration), 0.0)
        if thumbnail_path is not None:
            lecture.thumbnail_path = thumbnail_path

        await db.commit()
        await db.refresh(lecture)
        return lecture.status, int(lecture.processing_progress)


def _update_lecture_state(
    lecture_uuid: uuid.UUID,
    *,
    status: LectureStatus | None = None,
    progress: int | None = None,
    error_message: str | None | object = _NO_VALUE,
    file_path: str | None = None,
    duration: float | None = None,
    thumbnail_path: str | None = None,
    publish_progress: bool = True,
) -> tuple[LectureStatus, int]:
    current_status, current_progress = asyncio.run(
        _update_lecture_state_async(
            lecture_uuid,
            status=status,
            progress=progress,
            error_message=error_message,
            file_path=file_path,
            duration=duration,
            thumbnail_path=thumbnail_path,
        )
    )

    if publish_progress:
        broadcast_progress_sync(lecture_uuid, current_progress, current_status.value)
    return current_status, current_progress


def _mark_lecture_error(lecture_uuid: uuid.UUID, exc: Exception, step: str) -> None:
    message = f"{step}: {type(exc).__name__}: {exc}"
    logger.exception("Lecture processing failed lecture_id=%s step=%s", lecture_uuid, step)
    try:
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.ERROR,
            error_message=message[:2000],
            publish_progress=True,
        )
    except Exception:
        logger.exception("Failed to persist lecture error state lecture_id=%s", lecture_uuid)


def _ensure_payload(payload: dict[str, Any]) -> tuple[uuid.UUID, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError("Task payload must be a dict")
    lecture_uuid = _parse_lecture_uuid(payload.get("lecture_id"))
    return lecture_uuid, payload


def _aggregate_summary_blocks(segmented_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for block in segmented_blocks:
        block_text = str(block.get("text", "")).strip()
        if not block_text:
            continue

        timecode_start = block.get("timecode_start")
        timecode_end = block.get("timecode_end")
        summary_payload = summarize_segment(block_text, llm_config={})
        for item in summary_payload.get("blocks", []):
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "title": str(item.get("title", "")).strip() or "Блок",
                    "text": str(item.get("text", "")).strip(),
                    "type": str(item.get("type", "thought")).strip() or "thought",
                    "timecode_start": timecode_start,
                    "timecode_end": timecode_end,
                }
            )
    return [item for item in result if item["text"]]


async def _save_results_async(
    lecture_uuid: uuid.UUID,
    *,
    transcript_segments: list[dict[str, Any]],
    transcript_full_text: str,
    summary_blocks: list[dict[str, Any]],
    summary_start: float | None,
    summary_end: float | None,
    entity_nodes: list[dict[str, Any]],
    entity_edges: list[dict[str, Any]],
) -> None:
    async with AsyncSessionLocal() as db:
        lecture = await db.get(Lecture, lecture_uuid)
        if lecture is None:
            raise ValueError(f"Lecture not found: {lecture_uuid}")

        transcript = (
            await db.execute(select(Transcript).where(Transcript.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if transcript is None:
            db.add(
                Transcript(
                    lecture_id=lecture_uuid,
                    segments=transcript_segments,
                    full_text=transcript_full_text,
                )
            )
        else:
            transcript.segments = transcript_segments
            transcript.full_text = transcript_full_text

        summary = (
            await db.execute(select(Summary).where(Summary.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if summary is None:
            db.add(
                Summary(
                    lecture_id=lecture_uuid,
                    content=summary_blocks,
                    timecode_start=summary_start,
                    timecode_end=summary_end,
                )
            )
        else:
            summary.content = summary_blocks
            summary.timecode_start = summary_start
            summary.timecode_end = summary_end

        graph = (
            await db.execute(select(EntityGraph).where(EntityGraph.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if graph is None:
            db.add(
                EntityGraph(
                    lecture_id=lecture_uuid,
                    nodes=entity_nodes,
                    edges=entity_edges,
                    enriched=False,
                )
            )
        else:
            graph.nodes = entity_nodes
            graph.edges = entity_edges
            graph.enriched = False

        lecture.status = LectureStatus.DONE
        lecture.processing_progress = 100
        lecture.error_message = None
        await db.commit()


@shared_task(bind=True, name="lectures.process_lecture_chain")
def process_lecture_chain(self, lecture_id: str, selected_entities: list[str] | None = None) -> dict[str, Any]:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=5,
            error_message=None,
            publish_progress=True,
        )

        workflow = chain(
            download_video_task.s(str(lecture_uuid)),
            extract_audio_task.s(),
            transcribe_task.s(),
            segment_text_task.s(),
            summarize_task.s(),
            extract_entities_task.s(selected_entities),
            save_results_task.s(),
        )
        chain_result = workflow.apply_async()
        return {
            "lecture_id": str(lecture_uuid),
            "chain_id": chain_result.id,
            "status": "scheduled",
        }
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "process_lecture_chain")
        raise


@shared_task(bind=True, name="lectures.download_video")
def download_video_task(self, lecture_id: str) -> dict[str, Any]:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        lecture = _get_lecture_sync(lecture_uuid)
        lecture_dir = _lecture_dir(lecture_uuid)
        lecture_dir.mkdir(parents=True, exist_ok=True)

        if lecture.source_type == LectureSourceType.URL:
            if not lecture.source_url:
                raise ValueError("source_url is required for URL lecture")
            abs_video_path = download_video(lecture.source_url, str(lecture_dir / "source.mp4"))
            rel_video_path = _to_rel_media_path(lecture_uuid, abs_video_path)
            thumb_abs = get_video_thumbnail(abs_video_path, str(lecture_dir / "thumb.jpg"))
            thumb_rel = _to_rel_media_path(lecture_uuid, thumb_abs)
            duration = get_video_duration(abs_video_path)
            _update_lecture_state(
                lecture_uuid,
                status=LectureStatus.PROCESSING,
                progress=15,
                file_path=rel_video_path,
                thumbnail_path=thumb_rel,
                duration=duration,
                publish_progress=True,
            )
            return {"lecture_id": str(lecture_uuid), "video_path": abs_video_path}

        abs_video_path = _to_abs_media_path(lecture_uuid, lecture.file_path)
        if not abs_video_path or not Path(abs_video_path).exists():
            raise FileNotFoundError("Uploaded lecture video file not found")
        thumb_abs = get_video_thumbnail(abs_video_path, str(lecture_dir / "thumb.jpg"))
        thumb_rel = _to_rel_media_path(lecture_uuid, thumb_abs)
        duration = get_video_duration(abs_video_path)
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=15,
            thumbnail_path=thumb_rel,
            duration=duration,
            publish_progress=True,
        )
        return {"lecture_id": str(lecture_uuid), "video_path": abs_video_path}
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "download_video_task")
        raise


@shared_task(bind=True, name="lectures.extract_audio")
def extract_audio_task(self, payload: dict[str, Any]) -> dict[str, Any]:
    lecture_uuid, data = _ensure_payload(payload)
    try:
        video_path = str(data.get("video_path", "")).strip()
        if not video_path:
            raise ValueError("video_path is required")
        audio_path = extract_audio(video_path, str(_lecture_dir(lecture_uuid) / "audio.wav"))
        data["audio_path"] = audio_path
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=30,
            publish_progress=True,
        )
        return data
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "extract_audio_task")
        raise


@shared_task(bind=True, name="lectures.transcribe")
def transcribe_task(self, payload: dict[str, Any]) -> dict[str, Any]:
    lecture_uuid, data = _ensure_payload(payload)
    try:
        audio_path = str(data.get("audio_path", "")).strip()
        if not audio_path:
            raise ValueError("audio_path is required")
        transcription = transcribe_audio(audio_path, language="ru")
        segments = list(transcription.get("segments", []))
        full_text = str(transcription.get("full_text", "")).strip()
        if not full_text:
            raise ValueError("Transcription returned empty text")

        data["transcript_segments"] = segments
        data["transcript_full_text"] = full_text
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=50,
            publish_progress=True,
        )
        return data
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "transcribe_task")
        raise


@shared_task(bind=True, name="lectures.segment_text")
def segment_text_task(self, payload: dict[str, Any]) -> dict[str, Any]:
    lecture_uuid, data = _ensure_payload(payload)
    try:
        full_text = str(data.get("transcript_full_text", "")).strip()
        transcript_segments = data.get("transcript_segments") or []
        if not full_text:
            raise ValueError("transcript_full_text is required")
        blocks = segment_text(full_text, transcript_segments)
        data["segmented_blocks"] = blocks
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=65,
            publish_progress=True,
        )
        return data
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "segment_text_task")
        raise


@shared_task(bind=True, name="lectures.summarize")
def summarize_task(self, payload: dict[str, Any]) -> dict[str, Any]:
    lecture_uuid, data = _ensure_payload(payload)
    try:
        segmented_blocks = data.get("segmented_blocks") or []
        summary_blocks = _aggregate_summary_blocks(segmented_blocks)
        if not summary_blocks:
            fallback = summarize_segment(str(data.get("transcript_full_text", "")), llm_config={})
            summary_blocks = list(fallback.get("blocks", []))

        timecodes = [item for item in segmented_blocks if isinstance(item, dict)]
        summary_start = None
        summary_end = None
        if timecodes:
            starts = [float(item["timecode_start"]) for item in timecodes if item.get("timecode_start") is not None]
            ends = [float(item["timecode_end"]) for item in timecodes if item.get("timecode_end") is not None]
            summary_start = min(starts) if starts else None
            summary_end = max(ends) if ends else None

        data["summary_blocks"] = summary_blocks
        data["summary_start"] = summary_start
        data["summary_end"] = summary_end
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=80,
            publish_progress=True,
        )
        return data
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "summarize_task")
        raise


@shared_task(bind=True, name="lectures.extract_entities")
def extract_entities_task(
    self,
    payload: dict[str, Any],
    selected_entities: list[str] | None = None,
) -> dict[str, Any]:
    lecture_uuid, data = _ensure_payload(payload)
    try:
        text = str(data.get("transcript_full_text", "")).strip()
        if not text:
            raise ValueError("transcript_full_text is required")
        entities_payload = extract_entities(text, selected_entities=selected_entities, llm_config={})
        data["entity_nodes"] = list(entities_payload.get("nodes", []))
        data["entity_edges"] = list(entities_payload.get("edges", []))
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=90,
            publish_progress=True,
        )
        return data
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "extract_entities_task")
        raise


@shared_task(bind=True, name="lectures.save_results")
def save_results_task(self, payload: dict[str, Any]) -> dict[str, Any]:
    lecture_uuid, data = _ensure_payload(payload)
    try:
        transcript_segments = list(data.get("transcript_segments", []))
        transcript_full_text = str(data.get("transcript_full_text", "")).strip()
        summary_blocks = list(data.get("summary_blocks", []))
        summary_start = data.get("summary_start")
        summary_end = data.get("summary_end")
        entity_nodes = list(data.get("entity_nodes", []))
        entity_edges = list(data.get("entity_edges", []))

        if not transcript_full_text:
            raise ValueError("transcript_full_text is required")

        asyncio.run(
            _save_results_async(
                lecture_uuid,
                transcript_segments=transcript_segments,
                transcript_full_text=transcript_full_text,
                summary_blocks=summary_blocks,
                summary_start=summary_start,
                summary_end=summary_end,
                entity_nodes=entity_nodes,
                entity_edges=entity_edges,
            )
        )
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.DONE,
            progress=100,
            error_message=None,
            publish_progress=True,
        )
        return {"lecture_id": str(lecture_uuid), "status": "done"}
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "save_results_task")
        raise
