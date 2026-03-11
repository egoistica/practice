from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path

from app.models.summary import Summary
from app.services.summary_utils import normalize_summary_blocks


def _normalize_blocks(summary: Summary) -> list[dict[str, Any]]:
    return normalize_summary_blocks(
        list(summary.content or []),
        default_enriched=False,
        default_title="Блок",
        default_type="thought",
    )


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
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except Exception as exc:  # pragma: no cover - import dependency gate
        raise RuntimeError("PDF export dependency is not available. Install reportlab.") from exc

    def resolve_font_pair() -> tuple[Path, Path]:
        env_regular = os.getenv("SUMMARY_PDF_FONT_REGULAR")
        env_bold = os.getenv("SUMMARY_PDF_FONT_BOLD")
        candidates: list[tuple[Path, Path]] = []
        if env_regular and env_bold:
            candidates.append((Path(env_regular), Path(env_bold)))

        candidates.extend(
            [
                (
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                ),
                (
                    Path("/usr/local/share/fonts/dejavu/DejaVuSans.ttf"),
                    Path("/usr/local/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
                ),
                (
                    Path("C:/Windows/Fonts/DejaVuSans.ttf"),
                    Path("C:/Windows/Fonts/DejaVuSans-Bold.ttf"),
                ),
                (
                    Path("C:/Windows/Fonts/arial.ttf"),
                    Path("C:/Windows/Fonts/arialbd.ttf"),
                ),
            ]
        )

        for regular_path, bold_path in candidates:
            if regular_path.exists() and bold_path.exists():
                return regular_path, bold_path
        raise RuntimeError(
            "Unicode font files not found for PDF export. "
            "Set SUMMARY_PDF_FONT_REGULAR and SUMMARY_PDF_FONT_BOLD."
        )

    def register_unicode_fonts() -> tuple[str, str]:
        regular_path, bold_path = resolve_font_pair()
        regular_name = "SummaryUnicode"
        bold_name = "SummaryUnicodeBold"
        registered = set(pdfmetrics.getRegisteredFontNames())
        if regular_name not in registered:
            pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
        if bold_name not in registered:
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
        return regular_name, bold_name

    blocks = _normalize_blocks(summary)
    regular_font, bold_font = register_unicode_fonts()
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4
    left_margin = 40
    right_margin = 40
    top_margin = 40
    bottom_margin = 40
    content_width = page_width - left_margin - right_margin
    y = page_height - top_margin

    def ensure_space(required: float) -> bool:
        nonlocal y
        if y - required < bottom_margin:
            pdf.showPage()
            y = page_height - top_margin
            return True
        return False

    def write_wrapped(text: str, *, font_name: str, font_size: int, spacing: float = 4.0) -> None:
        nonlocal y
        wrapped = simpleSplit(text, font_name, font_size, content_width) or [""]
        line_height = font_size + spacing
        pdf.setFont(font_name, font_size)
        for line in wrapped:
            if ensure_space(line_height):
                pdf.setFont(font_name, font_size)
            pdf.drawString(left_margin, y, line)
            y -= line_height

    write_wrapped("Lecture Summary", font_name=bold_font, font_size=16, spacing=6.0)
    write_wrapped(f"Summary ID: {summary.id}", font_name=regular_font, font_size=9, spacing=2.0)
    write_wrapped(f"Lecture ID: {summary.lecture_id}", font_name=regular_font, font_size=9, spacing=2.0)
    write_wrapped(f"Created At: {summary.created_at.isoformat()}", font_name=regular_font, font_size=9, spacing=2.0)
    y -= 8

    for idx, block in enumerate(blocks, start=1):
        start = _format_timecode(block["timecode_start"])
        end = _format_timecode(block["timecode_end"])
        write_wrapped(f"{idx}. {block['title']}", font_name=bold_font, font_size=12, spacing=4.0)
        write_wrapped(f"Type: {block['type']}", font_name=regular_font, font_size=10, spacing=2.0)
        write_wrapped(f"Timecode: {start} - {end}", font_name=regular_font, font_size=10, spacing=2.0)
        write_wrapped(block["text"], font_name=regular_font, font_size=11, spacing=4.0)
        y -= 8

    pdf.save()
    return buffer.getvalue()
