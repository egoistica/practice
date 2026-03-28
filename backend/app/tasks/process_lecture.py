from __future__ import annotations

import asyncio
import logging
import os
import re
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
    LLMResponseParseError,
    LLMServiceError,
    merge_graph_data,
    run_enrichment_agent,
    run_entity_graph_agent,
    run_final_summary_agent,
    run_summary_agent,
)
from app.services.progress_service import broadcast_lecture_event_sync, broadcast_progress_sync
from app.services.text_processing import segment_text
from app.services.token_service import (
    InsufficientTokenBalanceError,
    check_balance,
    deduct_tokens,
)
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
SUMMARY_MIN_WORDS_PER_REQUEST = 220
SUMMARY_MAX_WORDS_PER_REQUEST = 700
SUMMARY_INTERMEDIATE_MAX_BLOCKS = 48
SUMMARY_FINAL_MAX_BLOCKS = 30
SUMMARY_MIN_BLOCKS = 8
SUMMARY_BLOCK_MAX_WORDS = 95
SUMMARY_BLOCK_TARGET_WORDS = 65
SECTION_MIN_WORDS = 180
SECTION_TARGET_WORDS = 320
SECTION_MAX_WORDS = 620
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9']+")
_SUMMARY_TEXT_KEY_RE = re.compile(r"[^A-Za-zА-Яа-яЁё0-9]+")
_SECTION_BOUNDARY_MARKER_RE = re.compile(
    r"^\s*(итак|теперь|важн\w+\s+момент|главн\w+\s+иде\w+|например|с другой стороны|другими словами|подвед[её]м итог|в итоге|таким образом)\b",
    flags=re.IGNORECASE,
)
_TRANSCRIPT_FILLER_RE = re.compile(r"\b(ээ+|эм+|мм+)\b", flags=re.IGNORECASE)
_DATE_SIGNAL_RE = re.compile(
    r"(\b\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?\b|\b\d{4}\b|\b(год|года|году|век|века|январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*)",
    flags=re.IGNORECASE,
)
_DEFINITION_SIGNAL_RE = re.compile(
    r"\b(это|называется|означает|подразумевает|определя\w+|термин|поняти\w+)\b",
    flags=re.IGNORECASE,
)
_CONCLUSION_SIGNAL_RE = re.compile(
    r"\b(вывод|итог|главн\w*|таким образом|следовательн\w*|в итоге|поэтому|значит)\b",
    flags=re.IGNORECASE,
)
_SUMMARY_SIMILARITY_STOPWORDS = {
    "это",
    "этот",
    "эта",
    "эти",
    "как",
    "или",
    "для",
    "при",
    "после",
    "перед",
    "между",
    "когда",
    "чтобы",
    "только",
    "более",
    "менее",
    "очень",
    "который",
    "которая",
    "которые",
    "также",
    "тоже",
    "если",
    "тогда",
    "здесь",
    "потому",
    "поэтому",
    "быть",
    "был",
    "была",
    "были",
    "есть",
    "его",
    "ее",
    "них",
    "них",
    "from",
    "that",
    "with",
    "this",
    "have",
}


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


def _resolve_processing_run_id(task: Any) -> str:
    request = getattr(task, "request", None)
    root_id = str(getattr(request, "root_id", "") or "").strip()
    if root_id:
        return root_id
    request_id = str(getattr(request, "id", "") or "").strip()
    if request_id:
        return request_id
    return str(uuid.uuid4())


def _validate_step_amount(lecture: Lecture, *, amount: int, reason: str) -> int:
    if not isinstance(amount, int):
        raise ValueError(
            f"Invalid token amount for lecture={lecture.id} reason={reason}: expected int, got {type(amount).__name__}"
        )
    if amount < 0:
        raise ValueError(
            f"Invalid token amount for lecture={lecture.id} reason={reason}: {amount}"
        )
    return amount


def _ensure_tokens_before_step(
    lecture: Lecture,
    *,
    amount: int,
    reason: str,
    step_name: str,
    processing_run_id: str,
) -> None:
    required_amount = _validate_step_amount(lecture, amount=amount, reason=reason)
    if required_amount == 0:
        return
    run_scope = (processing_run_id or "").strip() or "manual"
    has_balance = _run_async(check_balance(lecture.user_id, required_amount))
    if not has_balance:
        raise ValueError(
            "Insufficient token balance for "
            f"lecture={lecture.id} run={run_scope} step={step_name} reason={reason}: "
            f"required {required_amount} tokens"
        )


def _charge_tokens_for_step(
    lecture: Lecture,
    *,
    amount: int,
    reason: str,
    step_name: str,
    processing_run_id: str,
) -> None:
    charge_amount = _validate_step_amount(lecture, amount=amount, reason=reason)
    if charge_amount == 0:
        return
    run_scope = (processing_run_id or "").strip() or "manual"
    idempotency_key = f"lecture:{lecture.id}:run:{run_scope}:step:{step_name}"
    try:
        _run_async(
            deduct_tokens(
                lecture.user_id,
                charge_amount,
                reason,
                idempotency_key=idempotency_key,
            )
        )
    except InsufficientTokenBalanceError as exc:
        raise ValueError(
            f"Insufficient token balance for {reason}: required {charge_amount} tokens"
        ) from exc


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
    for idx, block in enumerate(segmented_blocks, start=1):
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
                "segment_id": str(block.get("segment_id") or f"seg_{idx}"),
            }
        )
    return placeholders


def _count_words(text: str) -> int:
    return len(_WORD_RE.findall(str(text or "")))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        text = str(value).strip()
        if not text:
            return default
        return int(text)
    except (TypeError, ValueError):
        return default


def _coalesce_segmented_blocks(
    segmented_blocks: list[dict[str, Any]],
    *,
    min_words: int = SUMMARY_MIN_WORDS_PER_REQUEST,
    max_words: int = SUMMARY_MAX_WORDS_PER_REQUEST,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, block in enumerate(segmented_blocks, start=1):
        if not isinstance(block, dict):
            continue
        text = str(block.get("text", "")).strip()
        if not text:
            continue
        normalized.append(
            {
                "text": text,
                "timecode_start": block.get("timecode_start"),
                "timecode_end": block.get("timecode_end"),
                "_words": _count_words(text),
                "segment_id": str(block.get("segment_id") or f"seg_{idx}"),
            }
        )

    if not normalized:
        return []

    merged: list[dict[str, Any]] = []
    current_texts: list[str] = []
    current_source_segment_ids: list[str] = []
    current_start: Any = None
    current_end: Any = None
    current_words = 0

    def flush_current() -> None:
        nonlocal current_texts, current_source_segment_ids, current_start, current_end, current_words
        if not current_texts:
            return
        merged.append(
            {
                "text": " ".join(current_texts).strip(),
                "timecode_start": current_start,
                "timecode_end": current_end,
                "source_segment_ids": list(current_source_segment_ids),
            }
        )
        current_texts = []
        current_source_segment_ids = []
        current_start = None
        current_end = None
        current_words = 0

    for block in normalized:
        block_words = int(block["_words"])
        block_text = str(block["text"])

        if not current_texts:
            current_texts = [block_text]
            current_source_segment_ids = [str(block.get("segment_id"))]
            current_start = block.get("timecode_start")
            current_end = block.get("timecode_end")
            current_words = block_words
            continue

        projected_words = current_words + block_words
        if current_words < int(min_words) or projected_words <= int(max_words):
            current_texts.append(block_text)
            current_source_segment_ids.append(str(block.get("segment_id")))
            current_end = block.get("timecode_end")
            current_words = projected_words
            continue

        flush_current()
        current_texts = [block_text]
        current_source_segment_ids = [str(block.get("segment_id"))]
        current_start = block.get("timecode_start")
        current_end = block.get("timecode_end")
        current_words = block_words

    flush_current()
    for idx, item in enumerate(merged, start=1):
        item["chunk_id"] = f"c{idx}"
        item["order"] = idx
        item["word_count"] = _count_words(str(item.get("text", "")))
    return merged


def _normalize_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = str(value or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _has_section_boundary_signal(text: str) -> bool:
    return bool(_SECTION_BOUNDARY_MARKER_RE.search(str(text or "").strip()))


def _build_section_blocks(coalesced_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not coalesced_blocks:
        return []

    sections_raw: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_words = 0
    for block in coalesced_blocks:
        if not isinstance(block, dict):
            continue
        block_text = str(block.get("text", "")).strip()
        if not block_text:
            continue
        block_words = int(block.get("word_count", _count_words(block_text)))
        if not current:
            current = [block]
            current_words = block_words
            continue

        projected_words = current_words + block_words
        prev_text = str(current[-1].get("text", ""))
        should_split = False
        if projected_words > SECTION_MAX_WORDS:
            should_split = True
        elif current_words >= SECTION_TARGET_WORDS and _has_section_boundary_signal(block_text):
            should_split = True
        elif current_words >= SECTION_MIN_WORDS and _has_section_boundary_signal(prev_text):
            should_split = True

        if should_split:
            sections_raw.append(current)
            current = [block]
            current_words = block_words
            continue

        current.append(block)
        current_words = projected_words

    if current:
        sections_raw.append(current)

    sections: list[dict[str, Any]] = []
    for idx, section_blocks in enumerate(sections_raw, start=1):
        text = " ".join(str(block.get("text", "")).strip() for block in section_blocks if str(block.get("text", "")).strip()).strip()
        if not text:
            continue
        starts = [float(block["timecode_start"]) for block in section_blocks if block.get("timecode_start") is not None]
        ends = [float(block["timecode_end"]) for block in section_blocks if block.get("timecode_end") is not None]
        source_segment_ids: list[str] = []
        source_chunk_ids: list[str] = []
        for block in section_blocks:
            source_segment_ids.extend(_normalize_str_list(block.get("source_segment_ids")))
            source_chunk_ids.extend(_normalize_str_list(block.get("source_chunk_ids", block.get("chunk_id"))))
        sections.append(
            {
                "section_id": f"s{idx}",
                "order": idx,
                "text": text,
                "word_count": _count_words(text),
                "timecode_start": min(starts) if starts else None,
                "timecode_end": max(ends) if ends else None,
                "source_segment_ids": _normalize_str_list(source_segment_ids),
                "source_chunk_ids": _normalize_str_list(source_chunk_ids),
            }
        )
    return sections


def _aggregate_summary_blocks(
    segmented_blocks: list[dict[str, Any]],
    *,
    lecture_title: str,
    mode: str,
) -> list[dict[str, Any]]:
    coalesced_blocks = _coalesce_segmented_blocks(segmented_blocks)
    section_blocks = _build_section_blocks(coalesced_blocks)
    section_by_chunk: dict[str, str] = {}
    for section in section_blocks:
        section_id = str(section.get("section_id") or "")
        for chunk_id in _normalize_str_list(section.get("source_chunk_ids")):
            section_by_chunk[chunk_id] = section_id

    result: list[dict[str, Any]] = []
    block_index = 0
    for block in coalesced_blocks:
        if not isinstance(block, dict):
            continue
        block_text = str(block.get("text", "")).strip()
        if not block_text:
            continue

        timecode_start = block.get("timecode_start")
        timecode_end = block.get("timecode_end")
        chunk_id = str(block.get("chunk_id") or "")
        section_id = section_by_chunk.get(chunk_id, "")
        source_segment_ids = _normalize_str_list(block.get("source_segment_ids"))
        block_order = _safe_int(block.get("order"), len(result) + 1)
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
            block_index += 1
            result.append(
                {
                    "id": f"lb{block_index}",
                    "section_id": section_id,
                    "source_segment_ids": source_segment_ids,
                    "source_chunk_ids": [chunk_id] if chunk_id else [],
                    "summary_level": "local",
                    "order": block_order * 10 + block_index,
                    "word_count": _count_words(text),
                    "title": str(item.get("title", "")).strip() or "Блок",
                    "text": text,
                    "type": str(item.get("type", "thought")).strip() or "thought",
                    "timecode_start": timecode_start,
                    "timecode_end": timecode_end,
                }
            )

    for section in section_blocks:
        section_text = str(section.get("text", "")).strip()
        if not section_text:
            continue
        summary_payload = run_summary_agent(
            lecture_text=section_text,
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
            block_index += 1
            result.append(
                {
                    "id": f"sb{block_index}",
                    "section_id": str(section.get("section_id") or ""),
                    "source_segment_ids": _normalize_str_list(section.get("source_segment_ids")),
                    "source_chunk_ids": _normalize_str_list(section.get("source_chunk_ids")),
                    "summary_level": "section",
                    "order": _safe_int(section.get("order"), 0) * 100 + block_index,
                    "word_count": _count_words(text),
                    "title": str(item.get("title", "")).strip() or "Секция",
                    "text": text,
                    "type": str(item.get("type", "thought")).strip() or "thought",
                    "timecode_start": section.get("timecode_start"),
                    "timecode_end": section.get("timecode_end"),
                }
            )

    result.sort(
        key=lambda item: (
            float(item["timecode_start"]) if item.get("timecode_start") is not None else 0.0,
            _safe_int(item.get("order"), 0),
        )
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


def _clean_transcript_text(raw_text: str) -> str:
    text = re.sub(r"\s+", " ", str(raw_text or "").strip())
    if not text:
        return ""
    text = _TRANSCRIPT_FILLER_RE.sub(" ", text)
    text = re.sub(r"(?:\bну\b[\s,]*){3,}", "ну ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:\bвот\b[\s,]*){3,}", "вот ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:\bтипа\b[\s,]*){2,}", "типа ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:\bкак бы\b[\s,]*){2,}", "как бы ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,.;:!?]){2,}", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.;:")


def _cleanup_transcript_payload(
    segments: list[dict[str, Any]],
    full_text: str,
) -> tuple[list[dict[str, Any]], str]:
    normalized = _normalize_transcript_segments(segments)
    if not normalized:
        cleaned_text = _clean_transcript_text(full_text)
        return segments, cleaned_text or str(full_text or "").strip()

    cleaned_segments: list[dict[str, Any]] = []
    last_text_key = ""
    for item in normalized:
        cleaned_text = _clean_transcript_text(str(item.get("text", "")))
        if not cleaned_text:
            continue
        text_key = cleaned_text.lower()
        if text_key == last_text_key:
            continue
        last_text_key = text_key
        cleaned_segments.append(
            {
                "start": float(item.get("start", 0.0)),
                "end": float(item.get("end", 0.0)),
                "text": cleaned_text,
            }
        )

    if not cleaned_segments:
        fallback_text = _clean_transcript_text(full_text)
        return segments, fallback_text or str(full_text or "").strip()

    cleaned_full_text = " ".join(item["text"] for item in cleaned_segments).strip()
    if not cleaned_full_text:
        return segments, str(full_text or "").strip()

    original_words = max(_count_words(full_text), 1)
    cleaned_words = _count_words(cleaned_full_text)
    if cleaned_words < max(20, int(original_words * 0.25)):
        # Safety: don't accept over-aggressive cleanup.
        return segments, str(full_text or "").strip()

    return cleaned_segments, cleaned_full_text


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


def _summary_key(block: dict[str, Any]) -> tuple[str, str]:
    return (
        str(block.get("title", "")).strip().lower(),
        str(block.get("text", "")).strip().lower(),
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


def _normalize_block_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _normalize_type_with_content(block_type: Any, *, title: str, text: str) -> str:
    candidate = str(block_type or "thought").strip().lower() or "thought"
    if candidate not in {"thought", "definition", "date", "conclusion"}:
        candidate = "thought"

    content = f"{title} {text}"
    if candidate == "date" and not _DATE_SIGNAL_RE.search(content):
        return "thought"
    if candidate == "definition" and not _DEFINITION_SIGNAL_RE.search(content):
        return "thought"
    if candidate == "conclusion" and not _CONCLUSION_SIGNAL_RE.search(content):
        return "thought"
    return candidate


def _summary_text_key(text: str) -> str:
    normalized = _SUMMARY_TEXT_KEY_RE.sub(" ", str(text or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _summary_token_set(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in _WORD_RE.findall(str(text or "").lower()):
        if len(token) < 4 or token in _SUMMARY_SIMILARITY_STOPWORDS:
            continue
        tokens.add(token)
    return tokens


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _merge_summary_pair(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_title = _normalize_block_text(left.get("title")) or "Блок"
    right_title = _normalize_block_text(right.get("title")) or "Блок"
    merged_title = left_title if left_title == right_title else left_title

    left_text = _normalize_block_text(left.get("text"))
    right_text = _normalize_block_text(right.get("text"))
    if left_text and right_text:
        if left_text in right_text:
            merged_text = right_text
        elif right_text in left_text:
            merged_text = left_text
        else:
            merged_text = f"{left_text} {right_text}"
    else:
        merged_text = left_text or right_text

    left_start = left.get("timecode_start")
    right_start = right.get("timecode_start")
    starts = [float(v) for v in (left_start, right_start) if v is not None]
    merged_start = min(starts) if starts else None

    left_end = left.get("timecode_end")
    right_end = right.get("timecode_end")
    ends = [float(v) for v in (left_end, right_end) if v is not None]
    merged_end = max(ends) if ends else None

    raw_type = left.get("type") if left.get("type") == right.get("type") else "thought"
    merged_type = _normalize_type_with_content(raw_type, title=merged_title, text=merged_text)
    merged_section_id = str(left.get("section_id") or right.get("section_id") or "").strip()
    merged_source_segment_ids = _normalize_str_list(
        _normalize_str_list(left.get("source_segment_ids")) + _normalize_str_list(right.get("source_segment_ids"))
    )
    merged_source_chunk_ids = _normalize_str_list(
        _normalize_str_list(left.get("source_chunk_ids")) + _normalize_str_list(right.get("source_chunk_ids"))
    )
    left_order = _safe_int(left.get("order"), 0)
    right_order = _safe_int(right.get("order"), 0)
    merged_order = min(value for value in (left_order, right_order) if value > 0) if (left_order or right_order) else 0
    merged_level = "section" if "section" in {str(left.get("summary_level", "")), str(right.get("summary_level", ""))} else "local"
    return {
        "title": merged_title,
        "text": merged_text,
        "type": merged_type,
        "timecode_start": merged_start,
        "timecode_end": merged_end,
        "enriched": bool(left.get("enriched", False) or right.get("enriched", False)),
        "section_id": merged_section_id,
        "source_segment_ids": merged_source_segment_ids,
        "source_chunk_ids": merged_source_chunk_ids,
        "order": merged_order,
        "summary_level": merged_level,
        "word_count": _count_words(merged_text),
    }


def _split_text_into_word_chunks(text: str, max_words: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    chunks: list[str] = []
    for idx in range(0, len(words), max_words):
        chunks.append(" ".join(words[idx : idx + max_words]).strip())
    return [item for item in chunks if item]


def _split_large_summary_block(block: dict[str, Any], *, max_words: int, target_words: int) -> list[dict[str, Any]]:
    text = _normalize_block_text(block.get("text"))
    if _count_words(text) <= max_words:
        return [block]

    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]
    if len(sentences) <= 1:
        chunks = _split_text_into_word_chunks(text, max_words)
    else:
        chunks = []
        current_parts: list[str] = []
        current_words = 0
        for sentence in sentences:
            sentence_words = _count_words(sentence)
            if sentence_words > max_words:
                if current_parts:
                    chunks.append(" ".join(current_parts).strip())
                    current_parts = []
                    current_words = 0
                chunks.extend(_split_text_into_word_chunks(sentence, max_words))
                continue

            projected = current_words + sentence_words
            if current_parts and projected > max_words:
                chunks.append(" ".join(current_parts).strip())
                current_parts = [sentence]
                current_words = sentence_words
                continue

            current_parts.append(sentence)
            current_words = projected
            if current_words >= target_words:
                chunks.append(" ".join(current_parts).strip())
                current_parts = []
                current_words = 0

        if current_parts:
            chunks.append(" ".join(current_parts).strip())

    if not chunks:
        return [block]

    result: list[dict[str, Any]] = []
    title_base = _normalize_block_text(block.get("title")) or "Блок"
    for idx, chunk in enumerate(chunks, start=1):
        result.append(
            {
                "title": title_base,
                "text": chunk,
                "type": _normalize_type_with_content(block.get("type"), title=title_base, text=chunk),
                "timecode_start": block.get("timecode_start"),
                "timecode_end": block.get("timecode_end"),
                "enriched": bool(block.get("enriched", False)),
                "id": str(block.get("id") or f"split_{idx}"),
                "section_id": str(block.get("section_id", "")).strip(),
                "source_segment_ids": _normalize_str_list(block.get("source_segment_ids")),
                "source_chunk_ids": _normalize_str_list(block.get("source_chunk_ids")),
                "summary_level": str(block.get("summary_level", "local")).strip() or "local",
                "order": _safe_int(block.get("order"), 0),
                "word_count": _count_words(chunk),
            }
        )
    return result


def _split_large_summary_blocks(
    blocks: list[dict[str, Any]],
    *,
    max_words: int = SUMMARY_BLOCK_MAX_WORDS,
    target_words: int = SUMMARY_BLOCK_TARGET_WORDS,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        result.extend(_split_large_summary_block(block, max_words=max_words, target_words=target_words))
    return result


def _target_summary_block_count(total_words: int, target_max_blocks: int | None) -> int:
    if target_max_blocks is not None:
        return max(SUMMARY_MIN_BLOCKS, int(target_max_blocks))

    if total_words < 900:
        target = 10
    elif total_words < 1800:
        target = 14
    elif total_words < 3200:
        target = 18
    elif total_words < 5200:
        target = 22
    elif total_words < 7600:
        target = 26
    else:
        target = SUMMARY_FINAL_MAX_BLOCKS
    return max(SUMMARY_MIN_BLOCKS, min(int(target), SUMMARY_FINAL_MAX_BLOCKS))


def _compact_summary_blocks(
    blocks: list[dict[str, Any]],
    *,
    target_max_blocks: int | None = None,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for item in blocks:
        if not isinstance(item, dict):
            continue
        text = _normalize_block_text(item.get("text"))
        if not text:
            continue
        title = _normalize_block_text(item.get("title")) or "Блок"
        block_type = _normalize_type_with_content(item.get("type"), title=title, text=text)
        prepared.append(
            {
                "id": str(item.get("id") or f"b{len(prepared) + 1}"),
                "title": title,
                "text": text,
                "type": block_type,
                "timecode_start": item.get("timecode_start"),
                "timecode_end": item.get("timecode_end"),
                "enriched": bool(item.get("enriched", False)),
                "section_id": str(item.get("section_id", "")).strip(),
                "source_segment_ids": _normalize_str_list(item.get("source_segment_ids")),
                "source_chunk_ids": _normalize_str_list(item.get("source_chunk_ids")),
                "summary_level": str(item.get("summary_level", "local")).strip() or "local",
                "order": _safe_int(item.get("order"), len(prepared) + 1),
                "word_count": _count_words(text),
            }
        )

    if not prepared:
        return []

    deduped: list[dict[str, Any]] = []
    seen_text_keys: set[str] = set()
    for item in prepared:
        key = _summary_text_key(str(item.get("text", "")))
        if key and key in seen_text_keys:
            continue
        if key:
            seen_text_keys.add(key)
        deduped.append(item)

    merged_adjacent: list[dict[str, Any]] = []
    idx = 0
    while idx < len(deduped):
        current = deduped[idx]
        if idx + 1 >= len(deduped):
            merged_adjacent.append(current)
            idx += 1
            continue

        nxt = deduped[idx + 1]
        similarity = _jaccard_similarity(
            _summary_token_set(str(current.get("text", ""))),
            _summary_token_set(str(nxt.get("text", ""))),
        )
        current_words = _count_words(str(current.get("text", "")))
        next_words = _count_words(str(nxt.get("text", "")))
        should_merge = similarity >= 0.62 or (similarity >= 0.42 and current_words <= 24 and next_words <= 24)
        if should_merge:
            merged_adjacent.append(_merge_summary_pair(current, nxt))
            idx += 2
            continue

        merged_adjacent.append(current)
        idx += 1

    compacted = merged_adjacent
    compacted = _split_large_summary_blocks(compacted)
    total_words = sum(_count_words(str(item.get("text", ""))) for item in compacted)
    target = _target_summary_block_count(total_words, target_max_blocks)
    while len(compacted) > target and len(compacted) > 1:
        best_idx = 0
        best_score = -1.0
        for merge_idx in range(len(compacted) - 1):
            left = compacted[merge_idx]
            right = compacted[merge_idx + 1]
            similarity = _jaccard_similarity(
                _summary_token_set(str(left.get("text", ""))),
                _summary_token_set(str(right.get("text", ""))),
            )
            combined_words = _count_words(str(left.get("text", ""))) + _count_words(str(right.get("text", "")))
            if combined_words > SUMMARY_BLOCK_MAX_WORDS:
                continue
            if similarity > 0:
                score = similarity + (0.2 if left.get("type") == right.get("type") else 0.0)
            else:
                score = 0.05 / max(combined_words, 1)
            if score > best_score:
                best_score = score
                best_idx = merge_idx

        if best_score < 0:
            break
        compacted[best_idx] = _merge_summary_pair(compacted[best_idx], compacted[best_idx + 1])
        del compacted[best_idx + 1]

    compacted = _split_large_summary_blocks(compacted)
    return compacted


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
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for block in current_blocks:
        if not isinstance(block, dict):
            continue
        by_key[_summary_key(block)] = block

    finalized: list[dict[str, Any]] = []
    for block in final_blocks:
        if not isinstance(block, dict):
            continue
        base = by_key.get(_summary_key(block), {})
        text_value = str(block.get("text", "")).strip()
        finalized.append(
            {
                "id": str(block.get("id") or base.get("id") or f"f{len(finalized) + 1}"),
                "section_id": str(block.get("section_id") or base.get("section_id") or "").strip(),
                "source_segment_ids": _normalize_str_list(
                    _normalize_str_list(block.get("source_segment_ids")) + _normalize_str_list(base.get("source_segment_ids"))
                ),
                "source_chunk_ids": _normalize_str_list(
                    _normalize_str_list(block.get("source_chunk_ids")) + _normalize_str_list(base.get("source_chunk_ids"))
                ),
                "summary_level": "final",
                "order": _safe_int(block.get("order"), _safe_int(base.get("order"), len(finalized) + 1)),
                "word_count": _count_words(text_value),
                "title": str(block.get("title", "")).strip() or "Блок",
                "text": text_value,
                "type": str(block.get("type", "thought")).strip() or "thought",
                "timecode_start": base.get("timecode_start"),
                "timecode_end": base.get("timecode_end"),
                "enriched": bool(base.get("enriched", False)),
            }
        )
    return _compact_summary_blocks([item for item in finalized if item.get("text")], target_max_blocks=SUMMARY_FINAL_MAX_BLOCKS)


def _broadcast_realtime_event(lecture_uuid: uuid.UUID, event_type: str, payload: dict[str, object]) -> None:
    try:
        broadcast_lecture_event_sync(lecture_uuid, event_type, payload)
    except Exception:
        logger.exception(
            "Failed to broadcast realtime event lecture_id=%s event=%s",
            lecture_uuid,
            event_type,
        )


def _run_entity_graph_agent_safe(
    *,
    lecture_uuid: uuid.UUID,
    lecture_text: str,
    selected_entities: list[str] | None,
    enrichment_enabled: bool,
    context: str,
) -> dict[str, Any]:
    try:
        return run_entity_graph_agent(
            lecture_text=lecture_text,
            selected_entities=selected_entities,
            enrichment_enabled=enrichment_enabled,
            llm_config={},
        )
    except (LLMServiceError, LLMResponseParseError, ValueError, TypeError):
        logger.exception(
            "Entity graph agent failed, fallback to empty graph lecture_id=%s context=%s",
            lecture_uuid,
            context,
        )
        return {"nodes": [], "edges": []}


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

        entities_payload = _run_entity_graph_agent_safe(
            lecture_uuid=lecture_uuid,
            lecture_text=str(chunk.get("text", "")),
            selected_entities=selected_entities,
            enrichment_enabled=False,
            context=f"realtime_chunk_{index}",
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
    summary_blocks = _compact_summary_blocks(
        summary_blocks,
        target_max_blocks=SUMMARY_INTERMEDIATE_MAX_BLOCKS,
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
        summary_blocks = _compact_summary_blocks(
            summary_blocks,
            target_max_blocks=SUMMARY_INTERMEDIATE_MAX_BLOCKS,
        )

    final_payload = run_final_summary_agent(
        summary_blocks=summary_blocks,
        lecture_title=lecture_title,
        llm_config={},
    )
    final_blocks = list(final_payload.get("final_summary", {}).get("blocks", []))
    if final_blocks:
        summary_blocks = _finalize_summary_blocks(summary_blocks, final_blocks)
    summary_blocks = _compact_summary_blocks(summary_blocks, target_max_blocks=SUMMARY_FINAL_MAX_BLOCKS)

    summary_start, summary_end = _timecode_range(summary_blocks)
    _run_async(
        _upsert_summary_async(
            lecture_uuid,
            content=summary_blocks,
            timecode_start=summary_start,
            timecode_end=summary_end,
        )
    )
    entities_payload = _run_entity_graph_agent_safe(
        lecture_uuid=lecture_uuid,
        lecture_text=full_text,
        selected_entities=selected_entities,
        enrichment_enabled=enrichment_enabled,
        context="standard_enrichment",
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
        processing_run_id = _resolve_processing_run_id(self)
        lecture = _get_lecture_sync(lecture_uuid)
        is_realtime = _is_realtime_lecture(lecture)
        if is_realtime:
            # Guard minimum required budget before expensive transcription.
            _ensure_tokens_before_step(
                lecture,
                amount=int(settings.COST_TRANSCRIBE),
                reason=f"realtime_pipeline lecture:{lecture_uuid}",
                step_name="realtime_pipeline",
                processing_run_id=processing_run_id,
            )
        else:
            _ensure_tokens_before_step(
                lecture,
                amount=int(settings.COST_TRANSCRIBE),
                reason=f"transcribe lecture:{lecture_uuid}",
                step_name="transcribe",
                processing_run_id=processing_run_id,
            )
        audio_path = _lecture_dir(lecture_uuid) / "audio.wav"
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        transcription = transcribe_audio(str(audio_path), language="ru")
        segments = list(transcription.get("segments", []))
        full_text = str(transcription.get("full_text", "")).strip()
        if not full_text:
            raise ValueError("Transcription returned empty text")
        segments, full_text = _cleanup_transcript_payload(segments, full_text)
        if not full_text:
            raise ValueError("Transcription cleanup returned empty text")

        has_realtime_timestamps = False
        realtime_billable_amount = 0
        if is_realtime:
            has_realtime_timestamps = _has_usable_realtime_timestamps(segments)
            realtime_billable_amount = (
                int(settings.COST_TRANSCRIBE)
                + int(settings.COST_SUMMARIZE)
                + int(settings.COST_EXTRACT_ENTITIES)
            )
            if not has_realtime_timestamps:
                # Fallback path executes final-summary and optional enrichment inside transcribe_task.
                realtime_billable_amount += int(settings.COST_SUMMARIZE)
                if enrichment_enabled:
                    realtime_billable_amount += int(settings.COST_ENRICH)
            # Verify full realtime budget before realtime post-processing branch.
            realtime_extra_amount = max(0, int(realtime_billable_amount) - int(settings.COST_TRANSCRIBE))
            _ensure_tokens_before_step(
                lecture,
                amount=realtime_extra_amount,
                reason=f"realtime_pipeline lecture:{lecture_uuid}",
                step_name="realtime_pipeline",
                processing_run_id=processing_run_id,
            )

        _run_async(_upsert_transcript_async(lecture_uuid, segments=segments, full_text=full_text))
        _update_lecture_state(
            lecture_uuid,
            status=LectureStatus.PROCESSING,
            progress=50,
            publish_progress=True,
        )

        if is_realtime:
            if has_realtime_timestamps:
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
            _charge_tokens_for_step(
                lecture,
                amount=realtime_billable_amount,
                reason=f"realtime_pipeline lecture:{lecture_uuid}",
                step_name="realtime_pipeline",
                processing_run_id=processing_run_id,
            )
        else:
            _charge_tokens_for_step(
                lecture,
                amount=settings.COST_TRANSCRIBE,
                reason=f"transcribe lecture:{lecture_uuid}",
                step_name="transcribe",
                processing_run_id=processing_run_id,
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
        processing_run_id = _resolve_processing_run_id(self)
        lecture = _get_lecture_sync(lecture_uuid)
        if _is_realtime_lecture(lecture):
            return str(lecture_uuid)
        _ensure_tokens_before_step(
            lecture,
            amount=int(settings.COST_SUMMARIZE),
            reason=f"summarize lecture:{lecture_uuid}",
            step_name="summary_agent",
            processing_run_id=processing_run_id,
        )

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
        summary_blocks = _compact_summary_blocks(
            summary_blocks,
            target_max_blocks=SUMMARY_INTERMEDIATE_MAX_BLOCKS,
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
        _charge_tokens_for_step(
            lecture,
            amount=settings.COST_SUMMARIZE,
            reason=f"summarize lecture:{lecture_uuid}",
            step_name="summary_agent",
            processing_run_id=processing_run_id,
        )
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "summary_agent_task")
        raise


@shared_task(bind=True, name="lectures.entity_graph_agent")
def entity_graph_agent_task(self, lecture_id: str, selected_entities: list[str] | None = None) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        processing_run_id = _resolve_processing_run_id(self)
        lecture = _get_lecture_sync(lecture_uuid)
        if _is_realtime_lecture(lecture):
            return str(lecture_uuid)
        _ensure_tokens_before_step(
            lecture,
            amount=int(settings.COST_EXTRACT_ENTITIES),
            reason=f"extract_entities lecture:{lecture_uuid}",
            step_name="entity_graph_agent",
            processing_run_id=processing_run_id,
        )

        _segments, transcript_full_text = _run_async(_get_transcript_async(lecture_uuid))
        if not transcript_full_text:
            raise ValueError("Transcript text is empty")

        entities_payload = _run_entity_graph_agent_safe(
            lecture_uuid=lecture_uuid,
            lecture_text=transcript_full_text,
            selected_entities=selected_entities,
            enrichment_enabled=False,
            context="entity_graph_agent_task",
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
        _charge_tokens_for_step(
            lecture,
            amount=settings.COST_EXTRACT_ENTITIES,
            reason=f"extract_entities lecture:{lecture_uuid}",
            step_name="entity_graph_agent",
            processing_run_id=processing_run_id,
        )
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "entity_graph_agent_task")
        raise


@shared_task(bind=True, name="lectures.enrichment_agent")
def enrichment_agent_task(self, lecture_id: str) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        processing_run_id = _resolve_processing_run_id(self)
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
        _ensure_tokens_before_step(
            lecture,
            amount=int(settings.COST_ENRICH),
            reason=f"enrich lecture:{lecture_uuid}",
            step_name="enrichment_agent",
            processing_run_id=processing_run_id,
        )

        enrichment_payload = run_enrichment_agent(
            lecture_text=transcript_full_text,
            summary_blocks=summary_blocks,
            llm_config={},
        )
        extra_summary_blocks = _convert_extra_blocks_to_summary(
            list(enrichment_payload.get("extra_blocks", []))
        )
        merged_blocks = _merge_summary_blocks(summary_blocks, extra_summary_blocks)
        merged_blocks = _compact_summary_blocks(
            merged_blocks,
            target_max_blocks=SUMMARY_INTERMEDIATE_MAX_BLOCKS,
        )
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
        _charge_tokens_for_step(
            lecture,
            amount=settings.COST_ENRICH,
            reason=f"enrich lecture:{lecture_uuid}",
            step_name="enrichment_agent",
            processing_run_id=processing_run_id,
        )
        return str(lecture_uuid)
    except Exception as exc:
        _mark_lecture_error(lecture_uuid, exc, "enrichment_agent_task")
        raise


@shared_task(bind=True, name="lectures.final_summary_agent")
def final_summary_agent_task(self, lecture_id: str) -> str:
    lecture_uuid = _parse_lecture_uuid(lecture_id)
    try:
        processing_run_id = _resolve_processing_run_id(self)
        lecture = _get_lecture_sync(lecture_uuid)
        if _is_realtime_lecture(lecture):
            return str(lecture_uuid)
        _ensure_tokens_before_step(
            lecture,
            amount=int(settings.COST_SUMMARIZE),
            reason=f"final_summary lecture:{lecture_uuid}",
            step_name="final_summary_agent",
            processing_run_id=processing_run_id,
        )

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
        _charge_tokens_for_step(
            lecture,
            amount=settings.COST_SUMMARIZE,
            reason=f"final_summary lecture:{lecture_uuid}",
            step_name="final_summary_agent",
            processing_run_id=processing_run_id,
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

