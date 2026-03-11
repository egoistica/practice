from __future__ import annotations

from typing import Any


def to_non_negative_float(raw_value: Any) -> float | None:
    if raw_value is None:
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def normalize_summary_blocks(
    raw_blocks: list[Any],
    *,
    default_enriched: bool,
    default_title: str = "Блок",
    default_type: str = "thought",
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in raw_blocks:
        if not isinstance(item, dict):
            continue

        text = str(item.get("text", "")).strip()
        if not text:
            continue

        title = str(item.get("title", "")).strip() or default_title
        block_type = str(item.get("type", default_type)).strip() or default_type
        timecode_start = to_non_negative_float(item.get("timecode_start"))
        timecode_end = to_non_negative_float(item.get("timecode_end"))
        if timecode_start is not None and timecode_end is not None and timecode_end < timecode_start:
            timecode_end = timecode_start

        enriched_flag = item.get("enriched")
        if isinstance(enriched_flag, bool):
            enriched = enriched_flag
        else:
            enriched = default_enriched

        normalized.append(
            {
                "title": title,
                "text": text,
                "type": block_type,
                "timecode_start": timecode_start,
                "timecode_end": timecode_end,
                "enriched": enriched,
            }
        )
    return normalized
