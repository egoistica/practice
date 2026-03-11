from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from app.models.summary import Summary


def _to_non_negative_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _normalize_blocks(summary: Summary) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    content = list(summary.content or [])
    for item in content:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue

        title = str(item.get("title", "")).strip() or "Block"
        block_type = str(item.get("type", "thought")).strip() or "thought"
        timecode_start = _to_non_negative_float(item.get("timecode_start"))
        timecode_end = _to_non_negative_float(item.get("timecode_end"))
        if timecode_start is not None and timecode_end is not None and timecode_end < timecode_start:
            timecode_end = timecode_start

        enriched = bool(item.get("enriched", False))
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


def _format_timecode(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    whole = int(seconds)
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def export_summary_to_markdown(summary: Summary) -> str:
    blocks = _normalize_blocks(summary)
    lines = [
        "# Lecture Summary",
        "",
        f"- Summary ID: `{summary.id}`",
        f"- Lecture ID: `{summary.lecture_id}`",
        f"- Created At: `{summary.created_at.isoformat()}`",
        f"- Blocks: `{len(blocks)}`",
        "",
    ]

    for idx, block in enumerate(blocks, start=1):
        start = _format_timecode(block["timecode_start"])
        end = _format_timecode(block["timecode_end"])
        lines.append(f"## {idx}. {block['title']}")
        lines.append(f"- Type: `{block['type']}`")
        lines.append(f"- Timecode: `{start} - {end}`")
        lines.append("")
        lines.append(block["text"])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_summary_to_json(summary: Summary) -> str:
    blocks = _normalize_blocks(summary)
    payload = {
        "id": str(summary.id),
        "lecture_id": str(summary.lecture_id),
        "created_at": summary.created_at.isoformat(),
        "enriched": any(bool(item.get("enriched")) for item in blocks),
        "blocks": blocks,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_summary_to_pdf(summary: Summary) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import simpleSplit
        from reportlab.pdfgen import canvas
    except Exception as exc:  # pragma: no cover - import dependency gate
        raise RuntimeError("PDF export dependency is not available. Install reportlab.") from exc

    blocks = _normalize_blocks(summary)
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4
    left_margin = 40
    right_margin = 40
    top_margin = 40
    bottom_margin = 40
    content_width = page_width - left_margin - right_margin
    y = page_height - top_margin

    def ensure_space(required: float) -> None:
        nonlocal y
        if y - required < bottom_margin:
            pdf.showPage()
            y = page_height - top_margin

    def write_wrapped(text: str, *, font_name: str, font_size: int, spacing: float = 4.0) -> None:
        nonlocal y
        wrapped = simpleSplit(text, font_name, font_size, content_width) or [""]
        line_height = font_size + spacing
        ensure_space(line_height * len(wrapped))
        pdf.setFont(font_name, font_size)
        for line in wrapped:
            pdf.drawString(left_margin, y, line)
            y -= line_height

    write_wrapped("Lecture Summary", font_name="Helvetica-Bold", font_size=16, spacing=6.0)
    write_wrapped(f"Summary ID: {summary.id}", font_name="Helvetica", font_size=9, spacing=2.0)
    write_wrapped(f"Lecture ID: {summary.lecture_id}", font_name="Helvetica", font_size=9, spacing=2.0)
    write_wrapped(f"Created At: {summary.created_at.isoformat()}", font_name="Helvetica", font_size=9, spacing=2.0)
    y -= 8

    for idx, block in enumerate(blocks, start=1):
        start = _format_timecode(block["timecode_start"])
        end = _format_timecode(block["timecode_end"])
        write_wrapped(f"{idx}. {block['title']}", font_name="Helvetica-Bold", font_size=12, spacing=4.0)
        write_wrapped(f"Type: {block['type']}", font_name="Helvetica", font_size=10, spacing=2.0)
        write_wrapped(f"Timecode: {start} - {end}", font_name="Helvetica", font_size=10, spacing=2.0)
        write_wrapped(block["text"], font_name="Helvetica", font_size=11, spacing=4.0)
        y -= 8

    pdf.save()
    return buffer.getvalue()
