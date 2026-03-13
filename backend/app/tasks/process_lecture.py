from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, TypeVar

from celery import chain, shared_task
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models.entity_graph import EntityGraph
from app.models.lecture import Lecture, LectureMode, LectureSourceType, LectureStatus
from app.models.summary import Summary
from app.models.transcript import Transcript
from app.services.llm_service import (
    merge_graph_data,
    run_enrichment_agent,
    run_entity_graph_agent,
    run_final_summary_agent,
    run_summary_agent,
)
from app.services.progress_service import broadcast_lecture_event_sync, broadcast_progress_sync
from app.services.text_processing import segment_text
from app.services.transcription_service import transcribe_audio
from app.services.video_service import download_video, extract_audio, get_video_duration, get_video_thumbnail

logger = logging.getLogger(__name__)
_NO_VALUE = object()
SEGMENT_BLOCK_TYPE = "_segment"
_T = TypeVar("_T")
_WORKER_LOOP: asyncio.AbstractEventLoop | None = None
_raw_realtime_segment_seconds = os.getenv("REALTIME_SEGMENT_SECONDS")
try:
    _parsed_realtime_segment_seconds = int(_raw_realtime_segment_seconds) if _raw_realtime_segment_seconds is not None else 60
except (TypeError, ValueError):
    _parsed_realtime_segment_seconds = 60
REALTIME_SEGMENT_SECONDS = max(15, _parsed_realtime_segment_seconds)


def _run_async(coro: Awaitable[_T]) -> _T:
    global _WORKER_LOOP
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("_run_async cannot be called from a running event loop")

    if _WORKER_LOOP is None or _WORKER_LOOP.is_closed():
        _WORKER_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_WORKER_LOOP)
        # Reset inherited asyncpg pool state in prefork worker children.
        _WORKER_LOOP.run_until_complete(engine.dispose())
    return _WORKER_LOOP.run_until_complete(coro)


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


def _is_realtime_lecture(lecture: Lecture) -> bool:
    mode_value = lecture.mode.value if hasattr(lecture.mode, "value") else str(lecture.mode)
    return str(mode_value).strip().lower() == LectureMode.REALTIME.value


async def _get_lecture_async(lecture_uuid: uuid.UUID) -> Lecture:
    async with AsyncSessionLocal() as db:
        lecture = await db.get(Lecture, lecture_uuid)
        if lecture is None:
            raise ValueError(f"Lecture not found: {lecture_uuid}")
        return lecture


def _get_lecture_sync(lecture_uuid: uuid.UUID) -> Lecture:
    return _run_async(_get_lecture_async(lecture_uuid))


async def _reset_processing_artifacts_async(lecture_uuid: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Transcript).where(Transcript.lecture_id == lecture_uuid))
        await db.execute(delete(Summary).where(Summary.lecture_id == lecture_uuid))
        await db.execute(delete(EntityGraph).where(EntityGraph.lecture_id == lecture_uuid))
        await db.commit()


async def _claim_lecture_for_processing_async(lecture_uuid: uuid.UUID) -> tuple[bool, bool]:
    async with AsyncSessionLocal() as db:
        try:
            lecture = (
                await db.execute(
                    select(Lecture)
                    .where(Lecture.id == lecture_uuid)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if lecture is None:
                raise ValueError(f"Lecture not found: {lecture_uuid}")

            if lecture.status == LectureStatus.PROCESSING:
                await db.rollback()
                return False, _is_realtime_lecture(lecture)

            is_realtime = _is_realtime_lecture(lecture)
            lecture.status = LectureStatus.PROCESSING
            lecture.processing_progress = 5
            lecture.error_message = None
            lecture.realtime_mode = is_realtime
            await db.commit()
            return True, is_realtime
        except Exception:
            await db.rollback()
            raise


async def _update_lecture_state_async(
    lecture_uuid: uuid.UUID,
    *,
    status: LectureStatus | None = None,
    progress: int | None = None,
    error_message: str | None | object = _NO_VALUE,
    file_path: str | None = None,
    duration: float | None = None,
    thumbnail_path: str | None = None,
    realtime_mode: bool | object = _NO_VALUE,
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
        if realtime_mode is not _NO_VALUE:
            lecture.realtime_mode = bool(realtime_mode)

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
    realtime_mode: bool | object = _NO_VALUE,
    publish_progress: bool = True,
) -> tuple[LectureStatus, int]:
    current_status, current_progress = _run_async(
        _update_lecture_state_async(
            lecture_uuid,
            status=status,
            progress=progress,
            error_message=error_message,
            file_path=file_path,
            duration=duration,
            thumbnail_path=thumbnail_path,
            realtime_mode=realtime_mode,
        )
    )
    if publish_progress:
        try:
            broadcast_progress_sync(lecture_uuid, current_progress, current_status.value)
        except Exception:
            logger.exception(
                "Failed to broadcast lecture progress lecture_id=%s progress=%s status=%s",
                lecture_uuid,
                current_progress,
                current_status.value,
            )
    return current_status, current_progress


def _mark_lecture_error(lecture_uuid: uuid.UUID, exc: Exception, step: str) -> None:
    message = f"{step}: {type(exc).__name__}: {exc}"
    logger.exception("Lecture processing failed lecture_id=%s step=%s", lecture_uuid, step)
    try:
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.ERROR,
            error_message=message[:2000],
            realtime_mode=False,
            publish_progress=True,
        )
    except Exception:
        logger.exception("Failed to persist lecture error state lecture_id=%s", lecture_uuid)


async def _upsert_transcript_async(
    lecture_uuid: uuid.UUID,
    *,
    segments: list[dict[str, Any]],
    full_text: str,
) -> None:
    async with AsyncSessionLocal() as db:
        transcript = (
            await db.execute(select(Transcript).where(Transcript.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if transcript is None:
            db.add(Transcript(lecture_id=lecture_uuid, segments=segments, full_text=full_text))
        else:
            transcript.segments = segments
            transcript.full_text = full_text
        await db.commit()


async def _get_transcript_async(lecture_uuid: uuid.UUID) -> tuple[list[dict[str, Any]], str]:
    async with AsyncSessionLocal() as db:
        transcript = (
            await db.execute(select(Transcript).where(Transcript.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if transcript is None:
            raise ValueError(f"Transcript not found for lecture: {lecture_uuid}")
        return list(transcript.segments or []), str(transcript.full_text or "")


async def _upsert_summary_async(
    lecture_uuid: uuid.UUID,
    *,
    content: list[dict[str, Any]],
    timecode_start: float | None,
    timecode_end: float | None,
) -> None:
    async with AsyncSessionLocal() as db:
        summary = (
            await db.execute(select(Summary).where(Summary.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if summary is None:
            db.add(
                Summary(
                    lecture_id=lecture_uuid,
                    content=content,
                    timecode_start=timecode_start,
                    timecode_end=timecode_end,
                )
            )
        else:
            summary.content = content
            summary.timecode_start = timecode_start
            summary.timecode_end = timecode_end
        await db.commit()


async def _append_summary_blocks_async(lecture_uuid: uuid.UUID, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not blocks:
        return []
    async with AsyncSessionLocal() as db:
        summary = (
            await db.execute(select(Summary).where(Summary.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if summary is None:
            merged = list(blocks)
            start, end = _timecode_range(merged)
            db.add(
                Summary(
                    lecture_id=lecture_uuid,
                    content=merged,
                    timecode_start=start,
                    timecode_end=end,
                )
            )
        else:
            existing = list(summary.content or [])
            merged = [*existing, *blocks]
            start, end = _timecode_range(merged)
            summary.content = merged
            summary.timecode_start = start
            summary.timecode_end = end
        await db.commit()
        return merged


async def _get_summary_content_async(lecture_uuid: uuid.UUID) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        summary = (
            await db.execute(select(Summary).where(Summary.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if summary is None:
            return []
        return list(summary.content or [])


async def _upsert_entity_graph_async(
    lecture_uuid: uuid.UUID,
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    async with AsyncSessionLocal() as db:
        graph = (
            await db.execute(select(EntityGraph).where(EntityGraph.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if graph is None:
            db.add(EntityGraph(lecture_id=lecture_uuid, nodes=nodes, edges=edges, enriched=False))
        else:
            graph.nodes = nodes
            graph.edges = edges
            graph.enriched = False
        await db.commit()


async def _merge_entity_graph_async(
    lecture_uuid: uuid.UUID,
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    async with AsyncSessionLocal() as db:
        graph = (
            await db.execute(select(EntityGraph).where(EntityGraph.lecture_id == lecture_uuid))
        ).scalar_one_or_none()
        if graph is None:
            merged = merge_graph_data([], [], nodes, edges)
            db.add(
                EntityGraph(
                    lecture_id=lecture_uuid,
                    nodes=list(merged.get("nodes", [])),
                    edges=list(merged.get("edges", [])),
                    enriched=False,
                )
            )
            await db.commit()
            return list(merged.get("nodes", [])), list(merged.get("edges", []))

        merged = merge_graph_data(
            list(graph.nodes or []),
            list(graph.edges or []),
            nodes,
            edges,
        )
        graph.nodes = list(merged.get("nodes", []))
        graph.edges = list(merged.get("edges", []))
        graph.enriched = False
        await db.commit()
        return list(graph.nodes or []), list(graph.edges or [])


def _timecode_range(blocks: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    starts = [
        float(block["timecode_start"])
        for block in blocks
        if isinstance(block, dict) and block.get("timecode_start") is not None
    ]
    ends = [
        float(block["timecode_end"])
        for block in blocks
        if isinstance(block, dict) and block.get("timecode_end") is not None
    ]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _segment_placeholders(segmented_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    placeholders: list[dict[str, Any]] = []
    for block in segmented_blocks:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text", "")).strip()
        if not text:
            continue
        placeholders.append(
            {
                "type": SEGMENT_BLOCK_TYPE,
                "text": text,
                "timecode_start": block.get("timecode_start"),
                "timecode_end": block.get("timecode_end"),
            }
        )
    return placeholders


def _aggregate_summary_blocks(
    segmented_blocks: list[dict[str, Any]],
    *,
    lecture_title: str,
    mode: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for block in segmented_blocks:
        if not isinstance(block, dict):
            continue
        block_text = str(block.get("text", "")).strip()
        if not block_text:
            continue

        timecode_start = block.get("timecode_start")
        timecode_end = block.get("timecode_end")
        summary_payload = run_summary_agent(
            lecture_text=block_text,
            lecture_title=lecture_title,
            mode=mode,
            llm_config={},
        )
        for item in summary_payload.get("blocks", []):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            result.append(
                {
                    "title": str(item.get("title", "")).strip() or "Блок",
                    "text": text,
                    "type": str(item.get("type", "thought")).strip() or "thought",
                    "timecode_start": timecode_start,
                    "timecode_end": timecode_end,
                }
            )
    return result


def _normalize_transcript_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        start_raw = item.get("start", item.get("timecode_start", 0.0))
        end_raw = item.get("end", item.get("timecode_end", start_raw))
        try:
            start = max(float(start_raw), 0.0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(end_raw)
        except (TypeError, ValueError):
            end = start
        if end < start:
            end = start
        normalized.append({"start": start, "end": end, "text": text})
    normalized.sort(key=lambda item: (item["start"], item["end"]))
    return normalized


def _build_realtime_chunks(
    segments: list[dict[str, Any]],
    window_seconds: int,
) -> list[dict[str, Any]]:
    normalized = _normalize_transcript_segments(segments)
    if not normalized:
        return []

    chunks_raw: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for segment in normalized:
        if not current:
            current = [segment]
            continue
        current_start = float(current[0]["start"])
        if float(segment["end"]) - current_start >= float(window_seconds):
            chunks_raw.append(current)
            current = [segment]
        else:
            current.append(segment)
    if current:
        chunks_raw.append(current)

    chunks: list[dict[str, Any]] = []
    for chunk in chunks_raw:
        if not chunk:
            continue
        timecode_start = float(chunk[0]["start"])
        timecode_end = float(chunk[-1]["end"])
        chunk_text = " ".join(str(item.get("text", "")).strip() for item in chunk if str(item.get("text", "")).strip())
        if not chunk_text:
            continue
        chunks.append(
            {
                "timecode_start": round(timecode_start, 3),
                "timecode_end": round(timecode_end, 3),
                "text": chunk_text,
            }
        )
    return chunks


def _has_usable_realtime_timestamps(segments: list[dict[str, Any]]) -> bool:
    normalized = _normalize_transcript_segments(segments)
    if not normalized:
        return False
    return all(
        float(segment.get("start", 0.0)) >= 0.0
        and float(segment.get("end", 0.0)) > float(segment.get("start", 0.0))
        for segment in normalized
    )


def _build_chunk_summary_blocks(
    chunk: dict[str, Any],
    *,
    lecture_title: str,
    mode: str,
) -> list[dict[str, Any]]:
    chunk_text = str(chunk.get("text", "")).strip()
    if not chunk_text:
        return []
    summary_payload = run_summary_agent(
        lecture_text=chunk_text,
        lecture_title=lecture_title,
        mode=mode,
        llm_config={},
    )
    blocks: list[dict[str, Any]] = []
    for item in summary_payload.get("blocks", []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        blocks.append(
            {
                "title": str(item.get("title", "")).strip() or "Блок",
                "text": text,
                "type": str(item.get("type", "thought")).strip() or "thought",
                "timecode_start": chunk.get("timecode_start"),
                "timecode_end": chunk.get("timecode_end"),
            }
        )
    return blocks


def _build_fallback_summary_blocks(
    source_text: str,
    *,
    timecode_start: float | None,
    timecode_end: float | None,
) -> list[dict[str, Any]]:
    normalized = " ".join(str(source_text or "").split())
    fallback_text = normalized[:300].rstrip()
    if normalized and len(normalized) > 300:
        fallback_text = f"{fallback_text}..."
    if not fallback_text:
        fallback_text = "Summary is temporarily unavailable for this fragment."

    return [
        {
            "title": "Fallback summary",
            "text": fallback_text,
            "type": "thought",
            "timecode_start": timecode_start,
            "timecode_end": timecode_end,
        }
    ]


def _summary_key(block: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(block.get("title", "")).strip().lower(),
        str(block.get("text", "")).strip().lower(),
        str(block.get("type", "")).strip().lower(),
    )


def _merge_summary_blocks(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = [item for item in existing if isinstance(item, dict)]
    seen = {_summary_key(item) for item in merged}
    for item in incoming:
        if not isinstance(item, dict):
            continue
        key = _summary_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _convert_extra_blocks_to_summary(extra_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for item in extra_blocks:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        text = str(item.get("text", "")).strip()
        related_to = str(item.get("related_to", "")).strip()
        if not title or not text:
            continue
        if related_to:
            text = f"{text}\n\nСвязано с: {related_to}"
        converted.append(
            {
                "title": title,
                "text": text,
                "type": "thought",
                "timecode_start": None,
                "timecode_end": None,
                "enriched": True,
            }
        )
    return converted


def _finalize_summary_blocks(
    current_blocks: list[dict[str, Any]],
    final_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for block in current_blocks:
        if not isinstance(block, dict):
            continue
        by_key[_summary_key(block)] = block

    finalized: list[dict[str, Any]] = []
    for block in final_blocks:
        if not isinstance(block, dict):
            continue
        base = by_key.get(_summary_key(block), {})
        finalized.append(
            {
                "title": str(block.get("title", "")).strip() or "Блок",
                "text": str(block.get("text", "")).strip(),
                "type": str(block.get("type", "thought")).strip() or "thought",
                "timecode_start": base.get("timecode_start"),
                "timecode_end": base.get("timecode_end"),
                "enriched": bool(base.get("enriched", False)),
            }
        )
    return [item for item in finalized if item.get("text")]


def _broadcast_realtime_event(lecture_uuid: uuid.UUID, event_type: str, payload: dict[str, object]) -> None:
    try:
        broadcast_lecture_event_sync(lecture_uuid, event_type, payload)
    except Exception:
        logger.exception(
            "Failed to broadcast realtime event lecture_id=%s event=%s",
            lecture_uuid,
            event_type,
        )


def _run_realtime_enrichment(
    lecture_uuid: uuid.UUID,
    lecture_title: str,
    mode: str,
    segments: list[dict[str, Any]],
    selected_entities: list[str] | None,
) -> None:
    chunks = _build_realtime_chunks(segments, REALTIME_SEGMENT_SECONDS)
    if not chunks:
        return

    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        summary_blocks = _build_chunk_summary_blocks(
            chunk,
            lecture_title=lecture_title,
            mode=mode,
        )
        if not summary_blocks:
            logger.warning(
                "Empty realtime summary blocks, using fallback lecture_id=%s chunk=%s/%s",
                lecture_uuid,
                index,
                total,
            )
            summary_blocks = _build_fallback_summary_blocks(
                str(chunk.get("text", "")),
                timecode_start=chunk.get("timecode_start"),
                timecode_end=chunk.get("timecode_end"),
            )
        _run_async(_append_summary_blocks_async(lecture_uuid, summary_blocks))
        _broadcast_realtime_event(
            lecture_uuid,
            "lecture_realtime_summary",
            {
                "chunk_index": index,
                "chunks_total": total,
                "timecode_start": chunk.get("timecode_start"),
                "timecode_end": chunk.get("timecode_end"),
                "blocks": summary_blocks,
            },
        )

        entities_payload = run_entity_graph_agent(
            lecture_text=str(chunk.get("text", "")),
            selected_entities=selected_entities,
            enrichment_enabled=False,
            llm_config={},
        )
        merged_nodes, merged_edges = _run_async(
            _merge_entity_graph_async(
                lecture_uuid,
                nodes=list(entities_payload.get("nodes", [])),
                edges=list(entities_payload.get("edges", [])),
            )
        )
        _broadcast_realtime_event(
            lecture_uuid,
            "lecture_realtime_entities",
            {
                "chunk_index": index,
                "chunks_total": total,
                "timecode_start": chunk.get("timecode_start"),
                "timecode_end": chunk.get("timecode_end"),
                "nodes": list(entities_payload.get("nodes", [])),
                "edges": list(entities_payload.get("edges", [])),
                "graph_nodes_total": len(merged_nodes),
                "graph_edges_total": len(merged_edges),
            },
        )

        progress = min(90, 50 + int((index / total) * 40))
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=progress,
            realtime_mode=True,
            publish_progress=True,
        )


def _run_standard_enrichment_from_transcript(
    lecture_uuid: uuid.UUID,
    lecture_title: str,
    mode: str,
    transcript_segments: list[dict[str, Any]],
    full_text: str,
    selected_entities: list[str] | None,
    enrichment_enabled: bool,
) -> None:
    segmented_blocks = segment_text(full_text, transcript_segments)
    summary_blocks = _aggregate_summary_blocks(
        segmented_blocks,
        lecture_title=lecture_title,
        mode=mode,
    )
    if not summary_blocks:
        fallback = run_summary_agent(
            lecture_text=full_text,
            lecture_title=lecture_title,
            mode=mode,
            llm_config={},
        )
        summary_blocks = list(fallback.get("blocks", []))
    if not summary_blocks:
        normalized_segments = _normalize_transcript_segments(transcript_segments)
        fallback_end = float(normalized_segments[-1].get("end", 0.0)) if normalized_segments else None
        logger.warning("Empty summary blocks after fallback, using placeholder lecture_id=%s", lecture_uuid)
        summary_blocks = _build_fallback_summary_blocks(
            full_text,
            timecode_start=0.0,
            timecode_end=fallback_end,
        )

    if enrichment_enabled:
        enrichment_payload = run_enrichment_agent(
            lecture_text=full_text,
            summary_blocks=summary_blocks,
            llm_config={},
        )
        extra_summary_blocks = _convert_extra_blocks_to_summary(
            list(enrichment_payload.get("extra_blocks", []))
        )
        summary_blocks = _merge_summary_blocks(summary_blocks, extra_summary_blocks)

    final_payload = run_final_summary_agent(
        summary_blocks=summary_blocks,
        lecture_title=lecture_title,
        llm_config={},
    )
    final_blocks = list(final_payload.get("final_summary", {}).get("blocks", []))
    if final_blocks:
        summary_blocks = _finalize_summary_blocks(summary_blocks, final_blocks)

    summary_start, summary_end = _timecode_range(summary_blocks)
    _run_async(
        _upsert_summary_async(
            lecture_uuid,
            content=summary_blocks,
            timecode_start=summary_start,
            timecode_end=summary_end,
        )
    )
    entities_payload = run_entity_graph_agent(
        lecture_text=full_text,
        selected_entities=selected_entities,
        enrichment_enabled=enrichment_enabled,
        llm_config={},
    )
    _run_async(
        _upsert_entity_graph_async(
            lecture_uuid,
            nodes=list(entities_payload.get("nodes", [])),
            edges=list(entities_payload.get("edges", [])),
        )
    )
    _update_lecture_state(
        lecture_uuid,
        status=LectureStatus.PROCESSING,
        progress=90,
        realtime_mode=False,
        publish_progress=True,
    )

    synthetic_timecode_start = 0.0
    synthetic_timecode_end = max(float(summary_end or 0.0), 0.0)
    _broadcast_realtime_event(
        lecture_uuid,
        "lecture_realtime_summary",
        {
            "chunk_index": 1,
            "chunks_total": 1,
            "timecode_start": synthetic_timecode_start,
            "timecode_end": synthetic_timecode_end,
            "blocks": summary_blocks,
            "fallback": True,
            "enrichment_enabled": enrichment_enabled,
        },
    )
    _broadcast_realtime_event(
        lecture_uuid,
        "lecture_realtime_entities",
        {
            "chunk_index": 1,
            "chunks_total": 1,
            "timecode_start": synthetic_timecode_start,
            "timecode_end": synthetic_timecode_end,
            "nodes": list(entities_payload.get("nodes", [])),
            "edges": list(entities_payload.get("edges", [])),
            "graph_nodes_total": len(list(entities_payload.get("nodes", []))),
            "graph_edges_total": len(list(entities_payload.get("edges", []))),
            "fallback": True,
        },
    )


@shared_task(bind=True, name="lectures.process_lecture_chain")
def process_lecture_chain(
    self,
    lecture_id: str,
    selected_entities: list[str] | None = None,
    enrichment_enabled: bool = False,
) -> dict[str, Any]:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        claimed, _is_realtime = _run_async(_claim_lecture_for_processing_async(lecture_uuid))
        if not claimed:
            logger.info("Lecture is already processing, skipping duplicate start lecture_id=%s", lecture_uuid)
            return {"lecture_id": str(lecture_uuid), "status": "already_processing"}

        try:
            broadcast_progress_sync(lecture_uuid, 5, LectureStatus.PROCESSING.value)
        except Exception:
            logger.exception("Failed to broadcast claimed processing state lecture_id=%s", lecture_uuid)

        _run_async(_reset_processing_artifacts_async(lecture_uuid))
        workflow_steps = [
            download_video_task.s(str(lecture_uuid)),
            extract_audio_task.s(),
            transcribe_task.s(selected_entities, enrichment_enabled),
            segment_text_task.s(),
            summary_agent_task.s(),
            entity_graph_agent_task.s(selected_entities),
        ]
        if enrichment_enabled:
            workflow_steps.append(enrichment_agent_task.s())
        workflow_steps.extend(
            [
                final_summary_agent_task.s(),
                save_results_task.s(),
            ]
        )
        workflow = chain(*workflow_steps)
        chain_result = workflow.apply_async()
        return {"lecture_id": str(lecture_uuid), "chain_id": chain_result.id, "status": "scheduled"}
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "process_lecture_chain")
        raise


@shared_task(bind=True, name="lectures.download_video")
def download_video_task(self, lecture_id: str) -> str:
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
            return str(lecture_uuid)

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
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "download_video_task")
        raise


@shared_task(bind=True, name="lectures.extract_audio")
def extract_audio_task(self, lecture_id: str) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        lecture = _get_lecture_sync(lecture_uuid)
        video_path = _to_abs_media_path(lecture_uuid, lecture.file_path)
        if not video_path:
            raise ValueError("Lecture video path is missing")
        extract_audio(video_path, str(_lecture_dir(lecture_uuid) / "audio.wav"))
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=30,
            publish_progress=True,
        )
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "extract_audio_task")
        raise


@shared_task(bind=True, name="lectures.transcribe")
def transcribe_task(
    self,
    lecture_id: str,
    selected_entities: list[str] | None = None,
    enrichment_enabled: bool = False,
) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        lecture = _get_lecture_sync(lecture_uuid)
        audio_path = _lecture_dir(lecture_uuid) / "audio.wav"
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        transcription = transcribe_audio(str(audio_path), language="ru")
        segments = list(transcription.get("segments", []))
        full_text = str(transcription.get("full_text", "")).strip()
        if not full_text:
            raise ValueError("Transcription returned empty text")

        _run_async(_upsert_transcript_async(lecture_uuid, segments=segments, full_text=full_text))
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=50,
            publish_progress=True,
        )

        if _is_realtime_lecture(lecture):
            if _has_usable_realtime_timestamps(segments):
                _run_realtime_enrichment(
                    lecture_uuid,
                    lecture_title=lecture.title,
                    mode=LectureMode.REALTIME.value,
                    segments=segments,
                    selected_entities=selected_entities,
                )
            else:
                logger.warning(
                    "Realtime fallback to standard enrichment due to missing/invalid timestamps lecture_id=%s",
                    lecture_uuid,
                )
                _run_standard_enrichment_from_transcript(
                    lecture_uuid=lecture_uuid,
                    lecture_title=lecture.title,
                    mode=LectureMode.INSTANT.value,
                    transcript_segments=segments,
                    full_text=full_text,
                    selected_entities=selected_entities,
                    enrichment_enabled=enrichment_enabled,
                )

        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "transcribe_task")
        raise


@shared_task(bind=True, name="lectures.segment_text")
def segment_text_task(self, lecture_id: str) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        lecture = _get_lecture_sync(lecture_uuid)
        if _is_realtime_lecture(lecture):
            return str(lecture_uuid)

        transcript_segments, transcript_full_text = _run_async(_get_transcript_async(lecture_uuid))
        segmented_blocks = segment_text(transcript_full_text, transcript_segments)
        placeholders = _segment_placeholders(segmented_blocks)
        summary_start, summary_end = _timecode_range(placeholders)
        _run_async(
            _upsert_summary_async(
                lecture_uuid,
                content=placeholders,
                timecode_start=summary_start,
                timecode_end=summary_end,
            )
        )
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=65,
            publish_progress=True,
        )
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "segment_text_task")
        raise


@shared_task(bind=True, name="lectures.summary_agent")
def summary_agent_task(self, lecture_id: str) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        lecture = _get_lecture_sync(lecture_uuid)
        if _is_realtime_lecture(lecture):
            return str(lecture_uuid)

        transcript_segments, transcript_full_text = _run_async(_get_transcript_async(lecture_uuid))
        summary_content = _run_async(_get_summary_content_async(lecture_uuid))
        segmented_blocks = [
            item for item in summary_content if isinstance(item, dict) and item.get("type") == SEGMENT_BLOCK_TYPE
        ]

        if not segmented_blocks:
            segmented_blocks = segment_text(transcript_full_text, transcript_segments)

        summary_blocks = _aggregate_summary_blocks(
            segmented_blocks,
            lecture_title=lecture.title,
            mode=LectureMode.INSTANT.value,
        )
        if not summary_blocks:
            fallback = run_summary_agent(
                lecture_text=transcript_full_text,
                lecture_title=lecture.title,
                mode=LectureMode.INSTANT.value,
                llm_config={},
            )
            summary_blocks = list(fallback.get("blocks", []))
        if not summary_blocks:
            logger.warning(
                "Empty summary blocks in summary_agent_task after fallback, using placeholder lecture_id=%s",
                lecture_uuid,
            )
            summary_blocks = _build_fallback_summary_blocks(
                transcript_full_text,
                timecode_start=0.0,
                timecode_end=None,
            )

        summary_start, summary_end = _timecode_range(summary_blocks)
        _run_async(
            _upsert_summary_async(
                lecture_uuid,
                content=summary_blocks,
                timecode_start=summary_start,
                timecode_end=summary_end,
            )
        )
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=75,
            publish_progress=True,
        )
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "summary_agent_task")
        raise


@shared_task(bind=True, name="lectures.entity_graph_agent")
def entity_graph_agent_task(self, lecture_id: str, selected_entities: list[str] | None = None) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        lecture = _get_lecture_sync(lecture_uuid)
        if _is_realtime_lecture(lecture):
            return str(lecture_uuid)

        _segments, transcript_full_text = _run_async(_get_transcript_async(lecture_uuid))
        if not transcript_full_text:
            raise ValueError("Transcript text is empty")

        entities_payload = run_entity_graph_agent(
            lecture_text=transcript_full_text,
            selected_entities=selected_entities,
            enrichment_enabled=False,
            llm_config={},
        )
        _run_async(
            _upsert_entity_graph_async(
                lecture_uuid,
                nodes=list(entities_payload.get("nodes", [])),
                edges=list(entities_payload.get("edges", [])),
            )
        )
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=85,
            publish_progress=True,
        )
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "entity_graph_agent_task")
        raise


@shared_task(bind=True, name="lectures.enrichment_agent")
def enrichment_agent_task(self, lecture_id: str) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        lecture = _get_lecture_sync(lecture_uuid)
        if _is_realtime_lecture(lecture):
            return str(lecture_uuid)

        _segments, transcript_full_text = _run_async(_get_transcript_async(lecture_uuid))
        summary_content = _run_async(_get_summary_content_async(lecture_uuid))
        summary_blocks = [
            item
            for item in summary_content
            if isinstance(item, dict) and item.get("type") != SEGMENT_BLOCK_TYPE
        ]
        if not summary_blocks:
            return str(lecture_uuid)

        enrichment_payload = run_enrichment_agent(
            lecture_text=transcript_full_text,
            summary_blocks=summary_blocks,
            llm_config={},
        )
        extra_summary_blocks = _convert_extra_blocks_to_summary(
            list(enrichment_payload.get("extra_blocks", []))
        )
        merged_blocks = _merge_summary_blocks(summary_blocks, extra_summary_blocks)
        summary_start, summary_end = _timecode_range(merged_blocks)
        _run_async(
            _upsert_summary_async(
                lecture_uuid,
                content=merged_blocks,
                timecode_start=summary_start,
                timecode_end=summary_end,
            )
        )
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=90,
            publish_progress=True,
        )
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "enrichment_agent_task")
        raise


@shared_task(bind=True, name="lectures.final_summary_agent")
def final_summary_agent_task(self, lecture_id: str) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        lecture = _get_lecture_sync(lecture_uuid)
        if _is_realtime_lecture(lecture):
            return str(lecture_uuid)

        summary_content = _run_async(_get_summary_content_async(lecture_uuid))
        summary_blocks = [
            item
            for item in summary_content
            if isinstance(item, dict) and item.get("type") != SEGMENT_BLOCK_TYPE
        ]
        if not summary_blocks:
            raise ValueError("Summary blocks are empty before final summary agent")

        final_payload = run_final_summary_agent(
            summary_blocks=summary_blocks,
            lecture_title=lecture.title,
            llm_config={},
        )
        final_blocks = list(final_payload.get("final_summary", {}).get("blocks", []))
        finalized_blocks = _finalize_summary_blocks(summary_blocks, final_blocks)
        if not finalized_blocks:
            raise ValueError("Final summary agent returned empty blocks")

        summary_start, summary_end = _timecode_range(finalized_blocks)
        _run_async(
            _upsert_summary_async(
                lecture_uuid,
                content=finalized_blocks,
                timecode_start=summary_start,
                timecode_end=summary_end,
            )
        )
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=95,
            publish_progress=True,
        )
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "final_summary_agent_task")
        raise


@shared_task(bind=True, name="lectures.summarize")
def summarize_task(self, lecture_id: str) -> str:
    return summary_agent_task.run(lecture_id)


@shared_task(bind=True, name="lectures.extract_entities")
def extract_entities_task(self, lecture_id: str, selected_entities: list[str] | None = None) -> str:
    return entity_graph_agent_task.run(lecture_id, selected_entities)


@shared_task(bind=True, name="lectures.finalize_enrichment")
def finalize_enrichment_task(self, lecture_id: str) -> str:
    return enrichment_agent_task.run(lecture_id)


@shared_task(bind=True, name="lectures.finalize_summary")
def finalize_summary_task(self, lecture_id: str) -> str:
    return final_summary_agent_task.run(lecture_id)


@shared_task(bind=True, name="lectures.save_results")
def save_results_task(self, lecture_id: str) -> dict[str, Any]:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        _segments, transcript_full_text = _run_async(_get_transcript_async(lecture_uuid))
        if not transcript_full_text:
            raise ValueError("transcript_full_text is required")

        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.DONE,
            progress=100,
            error_message=None,
            realtime_mode=False,
            publish_progress=True,
        )
        return {"lecture_id": str(lecture_uuid), "status": "done"}
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "save_results_task")
        raise

