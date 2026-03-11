from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any

from app.models.entity_graph import EntityGraph
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


def _normalize_graph_nodes(graph: EntityGraph) -> list[dict[str, Any]]:
    raw_nodes = list(graph.nodes or [])
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue

        node_id = str(item.get("id", "")).strip()
        label = str(item.get("label", "")).strip()
        if not node_id or not label or node_id in seen_ids:
            continue

        node_type = str(item.get("type", "entity")).strip() or "entity"
        enriched = bool(item.get("enriched", False))
        mentions: list[dict[str, Any]] = []
        seen_mentions: set[tuple[int, float | None]] = set()
        for mention in item.get("mentions", []) if isinstance(item.get("mentions"), list) else []:
            if not isinstance(mention, dict):
                continue
            raw_position = mention.get("position")
            if raw_position is None:
                raw_position = mention.get("position_in_text")
            if isinstance(raw_position, bool):
                continue
            try:
                position = int(raw_position)
            except (TypeError, ValueError):
                continue
            if position < 0:
                continue

            raw_timecode = mention.get("timecode")
            timecode = None
            if raw_timecode is not None:
                try:
                    parsed = float(raw_timecode)
                except (TypeError, ValueError):
                    parsed = None
                if parsed is not None and parsed >= 0:
                    timecode = round(parsed, 3)

            key = (position, timecode)
            if key in seen_mentions:
                continue
            seen_mentions.add(key)
            mentions.append({"position": position, "timecode": timecode})

        normalized.append(
            {
                "id": node_id,
                "label": label,
                "type": node_type,
                "enriched": enriched,
                "mentions": mentions,
            }
        )
        seen_ids.add(node_id)
    return normalized


def _normalize_graph_edges(graph: EntityGraph, allowed_node_ids: set[str]) -> list[dict[str, str]]:
    raw_edges = list(graph.edges or [])
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_edges:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "")).strip()
        target = str(item.get("target", "")).strip()
        label = str(item.get("label", "related_to")).strip() or "related_to"
        if not source or not target:
            continue
        if source not in allowed_node_ids or target not in allowed_node_ids:
            continue
        key = (source, target, label)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"source": source, "target": target, "label": label})
    return normalized


def export_graph_to_json(graph: EntityGraph) -> str:
    nodes = _normalize_graph_nodes(graph)
    allowed_node_ids = {node["id"] for node in nodes}
    edges = _normalize_graph_edges(graph, allowed_node_ids)
    payload = {
        "id": str(graph.id),
        "lecture_id": str(graph.lecture_id),
        "created_at": graph.created_at.isoformat(),
        "enriched": bool(graph.enriched),
        "nodes": nodes,
        "edges": edges,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_graph_to_image(graph: EntityGraph) -> bytes:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import networkx as nx
    except Exception as exc:  # pragma: no cover - import dependency gate
        raise RuntimeError(
            "PNG export dependencies are not available. Install matplotlib and networkx."
        ) from exc

    nodes = _normalize_graph_nodes(graph)
    allowed_node_ids = {node["id"] for node in nodes}
    edges = _normalize_graph_edges(graph, allowed_node_ids)

    graph_nx = nx.DiGraph()
    for node in nodes:
        graph_nx.add_node(
            node["id"],
            label=node["label"],
            enriched=bool(node.get("enriched", False)),
        )
    for edge in edges:
        graph_nx.add_edge(edge["source"], edge["target"], label=edge["label"])

    fig, ax = plt.subplots(figsize=(12, 8), dpi=150)
    ax.axis("off")

    if graph_nx.number_of_nodes() == 0:
        ax.text(0.5, 0.5, "Graph is empty", ha="center", va="center", fontsize=14)
    else:
        positions = nx.spring_layout(graph_nx, seed=42)
        node_colors = [
            "#ffcc80" if graph_nx.nodes[node_id].get("enriched") else "#90caf9"
            for node_id in graph_nx.nodes()
        ]
        nx.draw_networkx_nodes(
            graph_nx,
            positions,
            node_size=1100,
            node_color=node_colors,
            linewidths=1.0,
            edgecolors="#263238",
            ax=ax,
        )
        nx.draw_networkx_edges(
            graph_nx,
            positions,
            width=1.1,
            alpha=0.85,
            arrows=True,
            arrowsize=15,
            arrowstyle="-|>",
            ax=ax,
        )
        labels = {
            node_id: str(graph_nx.nodes[node_id].get("label", node_id))
            for node_id in graph_nx.nodes()
        }
        nx.draw_networkx_labels(graph_nx, positions, labels=labels, font_size=8, ax=ax)
        edge_labels = nx.get_edge_attributes(graph_nx, "label")
        if edge_labels:
            nx.draw_networkx_edge_labels(
                graph_nx,
                positions,
                edge_labels=edge_labels,
                font_size=7,
                rotate=False,
                ax=ax,
            )

    fig.tight_layout()
    output = BytesIO()
    fig.savefig(output, format="png", bbox_inches="tight")
    plt.close(fig)
    return output.getvalue()
