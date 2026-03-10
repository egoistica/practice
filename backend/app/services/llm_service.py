from __future__ import annotations

import json
import math
import re
from typing import Any, Optional

from litellm import completion

from app.core.config import settings


SUMMARY_PROMPT = (
    "Суммаризируй этот текст лекции, выделяя главные мысли, определения, даты, выводы. "
    "Структурируй как список логических блоков."
)
ENTITY_PROMPT_BASE = (
    "Найди именованные сущности в тексте: термины, персоналии, теории. "
    "Для каждой сущности найди связи с другими сущностями. "
    "Верни JSON с узлами {id, label, type, mentions:[{position_in_text, timecode}]} "
    "и рёбрами {source, target, label}."
)
SYSTEM_PROMPT = (
    "Ты помощник по созданию конспектов лекций. Верни только валидный JSON без markdown. "
    "Точный формат: "
    '{"blocks":[{"title":"...","text":"...","type":"thought|definition|date|conclusion"}]}.'
)
ENTITY_SYSTEM_PROMPT = (
    "Ты извлекаешь сущности и связи из учебного текста. "
    "Верни только валидный JSON без markdown. "
    "Формат: "
    '{"nodes":[{"id":"...","label":"...","type":"...","mentions":[{"position_in_text":0,"timecode":0.0}]}],'
    '"edges":[{"source":"...","target":"...","label":"..."}]}.'
)
ALLOWED_BLOCK_TYPES = {"thought", "definition", "date", "conclusion"}
DEFAULT_EDGE_LABEL = "related_to"
RESERVED_COMPLETION_KWARGS = {
    "messages",
    "model",
    "api_key",
    "api_base",
    "timeout",
    "temperature",
    "max_tokens",
    "openai_api_key",
    "anthropic_api_key",
    "azure_api_key",
}


class LLMServiceError(RuntimeError):
    """Base exception for LLM service failures."""


class LLMResponseParseError(LLMServiceError):
    """Raised when LLM response cannot be parsed to expected summary JSON."""


def summarize_segment(text: str, llm_config: Optional[dict[str, Any]]) -> dict[str, Any]:
    if llm_config is None:
        llm_config = {}
    if not isinstance(llm_config, dict):
        raise TypeError("llm_config must be a dict")

    normalized_text = text.strip()
    if not normalized_text:
        raise ValueError("text must not be empty")

    request_cfg = _resolve_request_config(llm_config)
    user_prompt = str(llm_config.get("prompt") or SUMMARY_PROMPT).strip()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{user_prompt}\n\nТекст лекции:\n{normalized_text}"},
    ]

    raw_content = _run_llm_completion(
        request_cfg=request_cfg,
        llm_config=llm_config,
        messages=messages,
        default_temperature=0.2,
        default_max_tokens=1200,
        default_timeout=60.0,
    )
    return _parse_summary_payload(raw_content)


def extract_entities(
    text: str,
    selected_entities: Optional[list[str] | str] = None,
    llm_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if llm_config is None:
        llm_config = {}
    if not isinstance(llm_config, dict):
        raise TypeError("llm_config must be a dict")

    normalized_text = text.strip()
    if not normalized_text:
        raise ValueError("text must not be empty")

    selected_clean = _normalize_selected_entities(selected_entities)
    request_cfg = _resolve_request_config(llm_config)
    user_prompt = str(llm_config.get("prompt") or _build_entity_prompt(selected_clean)).strip()

    messages = [
        {"role": "system", "content": ENTITY_SYSTEM_PROMPT},
        {"role": "user", "content": f"{user_prompt}\n\nТекст лекции:\n{normalized_text}"},
    ]
    raw_content = _run_llm_completion(
        request_cfg=request_cfg,
        llm_config=llm_config,
        messages=messages,
        default_temperature=0.1,
        default_max_tokens=1800,
        default_timeout=90.0,
    )
    return _parse_entities_payload(raw_content, selected_clean)


def _resolve_request_config(llm_config: dict[str, Any]) -> dict[str, str | None]:
    provider = str(llm_config.get("provider") or settings.LLM_PROVIDER).strip().lower()
    model = str(llm_config.get("model") or settings.LLM_MODEL).strip()
    api_base = str(llm_config.get("api_base") or "").strip() or None
    api_key = str(llm_config.get("api_key") or "").strip() or None

    if not provider:
        raise LLMServiceError("LLM provider is not configured")
    if not model:
        raise LLMServiceError("LLM model is not configured")

    if provider == "ollama":
        if not model.startswith("ollama/"):
            model = f"ollama/{model}"
        if not api_base:
            api_base = settings.OLLAMA_BASE_URL
    elif provider == "openai":
        if not api_key and settings.OPENAI_API_KEY and settings.OPENAI_API_KEY.strip():
            api_key = settings.OPENAI_API_KEY.strip()
    elif "/" not in model:
        model = f"{provider}/{model}"

    return {
        "provider": provider,
        "model": model,
        "api_base": api_base,
        "api_key": api_key,
    }


def _run_llm_completion(
    request_cfg: dict[str, str | None],
    llm_config: dict[str, Any],
    messages: list[dict[str, str]],
    default_temperature: float,
    default_max_tokens: int,
    default_timeout: float,
) -> str:
    request_kwargs: dict[str, Any] = {
        "model": request_cfg["model"],
        "messages": messages,
        "temperature": _resolve_float(llm_config.get("temperature"), default_temperature),
        "max_tokens": _resolve_int(llm_config.get("max_tokens"), default_max_tokens),
        "timeout": _resolve_timeout(llm_config.get("timeout"), default_timeout),
    }
    if request_cfg.get("api_base"):
        request_kwargs["api_base"] = request_cfg["api_base"]
    if request_cfg.get("api_key"):
        request_kwargs["api_key"] = request_cfg["api_key"]

    extra_kwargs = llm_config.get("completion_kwargs")
    if isinstance(extra_kwargs, dict):
        safe_extra_kwargs = {
            key: value
            for key, value in extra_kwargs.items()
            if str(key).strip().lower() not in RESERVED_COMPLETION_KWARGS
        }
        request_kwargs.update(safe_extra_kwargs)

    try:
        response = completion(**request_kwargs)
    except Exception as exc:  # pragma: no cover - runtime/provider specific
        raise LLMServiceError(
            f"LiteLLM request failed for provider={request_cfg['provider']} model={request_cfg['model']}"
        ) from exc
    return _extract_content(response)


def _extract_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        raise LLMResponseParseError("LLM response does not contain choices")

    first = choices[0]
    if isinstance(first, dict):
        message = first.get("message") or {}
        content = message.get("content")
    else:
        message = getattr(first, "message", None)
        content = getattr(message, "content", None) if message is not None else None

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text_part = item.get("text")
                if text_part:
                    parts.append(str(text_part))
            elif item:
                parts.append(str(item))
        content = "".join(parts)

    if not isinstance(content, str) or not content.strip():
        raise LLMResponseParseError("LLM response content is empty")
    return content.strip()


def _parse_summary_payload(raw_content: str) -> dict[str, Any]:
    payload = _load_json_payload(raw_content)
    if not isinstance(payload, dict):
        raise LLMResponseParseError("LLM response JSON must be an object")

    blocks_raw = payload.get("blocks")
    if not isinstance(blocks_raw, list):
        raise LLMResponseParseError("LLM response JSON must contain 'blocks' as a list")

    blocks: list[dict[str, str]] = []
    for index, item in enumerate(blocks_raw, start=1):
        if not isinstance(item, dict):
            continue

        text = str(item.get("text", "")).strip()
        if not text:
            continue

        block_type = _normalize_block_type(item.get("type"))
        title = str(item.get("title", "")).strip() or _default_title(block_type, index)
        blocks.append(
            {
                "title": title,
                "text": text,
                "type": block_type,
            }
        )

    if not blocks:
        raise LLMResponseParseError("LLM response JSON does not contain valid blocks")
    return {"blocks": blocks}


def _parse_entities_payload(raw_content: str, selected_entities: list[str]) -> dict[str, Any]:
    payload = _load_json_payload(raw_content)
    if not isinstance(payload, dict):
        raise LLMResponseParseError("Entity response JSON must be an object")

    nodes_raw = payload.get("nodes")
    edges_raw = payload.get("edges")
    if not isinstance(nodes_raw, list):
        raise LLMResponseParseError("Entity response must contain 'nodes' as a list")
    if not isinstance(edges_raw, list):
        raise LLMResponseParseError("Entity response must contain 'edges' as a list")

    nodes, ref_map = _normalize_nodes(nodes_raw)
    if selected_entities:
        nodes = _filter_nodes_by_selected(nodes, selected_entities)
        allowed_ids = {node["id"] for node in nodes}
        ref_map = {key: node_id for key, node_id in ref_map.items() if node_id in allowed_ids}

    edges = _normalize_edges(edges_raw, ref_map, {node["id"] for node in nodes})
    return {"nodes": nodes, "edges": edges}


def _load_json_payload(raw_content: str) -> Any:
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError:
        pass

    fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_content, flags=re.IGNORECASE)
    if fenced_match:
        try:
            return json.loads(fenced_match.group(1))
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for idx, char in enumerate(raw_content):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(raw_content[idx:])
            return payload
        except json.JSONDecodeError:
            continue

    raise LLMResponseParseError("Unable to parse JSON from LLM response")


def _normalize_block_type(raw_type: Any) -> str:
    value = str(raw_type or "").strip().lower()
    aliases = {
        "idea": "thought",
        "main_idea": "thought",
        "definition": "definition",
        "term": "definition",
        "date": "date",
        "fact": "date",
        "conclusion": "conclusion",
        "summary": "conclusion",
    }
    normalized = aliases.get(value, value)
    if normalized not in ALLOWED_BLOCK_TYPES:
        return "thought"
    return normalized


def _default_title(block_type: str, index: int) -> str:
    mapping = {
        "thought": "Ключевая мысль",
        "definition": "Определение",
        "date": "Дата/факт",
        "conclusion": "Вывод",
    }
    base = mapping.get(block_type, "Блок")
    return f"{base} {index}"


def _build_entity_prompt(selected_entities: list[str]) -> str:
    if not selected_entities:
        return ENTITY_PROMPT_BASE
    focused = ", ".join(selected_entities)
    return f"{ENTITY_PROMPT_BASE} Если указаны нужные сущности [{focused}], фокусируйся на них."


def _normalize_selected_entities(selected_entities: Optional[list[str] | str]) -> list[str]:
    if not selected_entities:
        return []
    values = [selected_entities] if isinstance(selected_entities, str) else list(selected_entities)

    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        candidate = _normalize_label_key(str(item))
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _normalize_nodes(nodes_raw: list[Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    dedup: dict[str, dict[str, Any]] = {}
    ref_map: dict[str, str] = {}
    used_ids: set[str] = set()

    for item in nodes_raw:
        if not isinstance(item, dict):
            continue

        label = str(item.get("label", "")).strip()
        if not label:
            continue

        dedupe_key = _normalize_label_key(label)
        if not dedupe_key:
            continue

        raw_id = str(item.get("id", "")).strip()
        node_type = str(item.get("type", "")).strip().lower() or "term"
        mentions = _normalize_mentions(item.get("mentions"))

        existing = dedup.get(dedupe_key)
        if existing is None:
            node_id = _make_unique_id(raw_id or _slugify(label), used_ids)
            node = {
                "id": node_id,
                "label": label,
                "type": node_type,
                "mentions": mentions,
                "_dedupe_key": dedupe_key,
            }
            dedup[dedupe_key] = node
            existing = node
        else:
            if existing.get("type") == "term" and node_type != "term":
                existing["type"] = node_type
            existing["mentions"] = _merge_mentions(existing.get("mentions", []), mentions)

        canonical_id = str(existing["id"])
        raw_id_ref = _normalize_ref(raw_id)
        if raw_id_ref:
            ref_map[raw_id_ref] = canonical_id

        label_ref = _normalize_ref(label)
        if label_ref:
            ref_map[label_ref] = canonical_id

        normalized_label_ref = _normalize_ref(dedupe_key)
        if normalized_label_ref:
            ref_map[normalized_label_ref] = canonical_id

    nodes: list[dict[str, Any]] = []
    for node in dedup.values():
        clean = dict(node)
        clean.pop("_dedupe_key", None)
        nodes.append(clean)
    return nodes, ref_map


def _filter_nodes_by_selected(nodes: list[dict[str, Any]], selected_entities: list[str]) -> list[dict[str, Any]]:
    selected_norm = {_normalize_label_key(item) for item in selected_entities if _normalize_label_key(item)}
    if not selected_norm:
        return nodes

    filtered: list[dict[str, Any]] = []
    for node in nodes:
        node_label = _normalize_label_key(str(node.get("label", "")))
        if not node_label:
            continue

        if node_label in selected_norm:
            filtered.append(node)
    return filtered


def _normalize_edges(
    edges_raw: list[Any],
    ref_map: dict[str, str],
    allowed_node_ids: set[str],
) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for item in edges_raw:
        if not isinstance(item, dict):
            continue

        source_id = ref_map.get(_normalize_ref(item.get("source")))
        target_id = ref_map.get(_normalize_ref(item.get("target")))
        if not source_id or not target_id:
            continue
        if source_id not in allowed_node_ids or target_id not in allowed_node_ids:
            continue
        if source_id == target_id:
            continue

        label = str(item.get("label", "")).strip() or DEFAULT_EDGE_LABEL
        key = (source_id, target_id, label.lower())
        if key in seen:
            continue
        seen.add(key)
        edges.append({"source": source_id, "target": target_id, "label": label})

    return edges


def _normalize_mentions(raw_mentions: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_mentions, list):
        return []

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[int, float | None]] = set()
    for item in raw_mentions:
        if not isinstance(item, dict):
            continue

        raw_position = item.get("position_in_text")
        position = _resolve_optional_non_negative_int(raw_position)
        if position is None:
            continue

        raw_timecode = item.get("timecode")
        timecode: float | None = None
        if raw_timecode is not None:
            parsed_timecode = _resolve_optional_float(raw_timecode)
            if parsed_timecode is not None and parsed_timecode >= 0:
                timecode = round(parsed_timecode, 3)

        key = (position, timecode)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"position_in_text": position, "timecode": timecode})
    return normalized


def _merge_mentions(base: list[dict[str, Any]], addition: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = list(base)
    seen = {
        (int(item.get("position_in_text", 0)), item.get("timecode"))
        for item in merged
        if isinstance(item, dict)
    }
    for item in addition:
        key = (int(item.get("position_in_text", 0)), item.get("timecode"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _normalize_ref(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _normalize_label(value: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _normalize_label_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^\w-]", "-", value.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "entity"


def _make_unique_id(base_id: str, used_ids: set[str]) -> str:
    candidate = base_id.strip() or "entity"
    if candidate not in used_ids:
        used_ids.add(candidate)
        return candidate

    suffix = 2
    while True:
        next_candidate = f"{candidate}_{suffix}"
        if next_candidate not in used_ids:
            used_ids.add(next_candidate)
            return next_candidate
        suffix += 1


def _resolve_timeout(raw_timeout: Any, default_timeout: float) -> float:
    try:
        timeout = float(raw_timeout) if raw_timeout is not None else default_timeout
    except (TypeError, ValueError, OverflowError):
        return default_timeout
    if not math.isfinite(timeout) or timeout <= 0:
        return default_timeout
    return timeout


def _resolve_optional_float(raw_value: Any) -> Optional[float]:
    if raw_value is None:
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _resolve_optional_non_negative_int(raw_value: Any) -> Optional[int]:
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if value < 0:
        return None
    return value


def _resolve_float(raw_value: Any, default_value: float) -> float:
    try:
        value = float(raw_value) if raw_value is not None else default_value
    except (TypeError, ValueError, OverflowError):
        return default_value
    if not math.isfinite(value) or value < 0:
        return default_value
    return value


def _resolve_int(raw_value: Any, default_value: int) -> int:
    try:
        value = int(raw_value) if raw_value is not None else default_value
    except (TypeError, ValueError, OverflowError):
        return default_value
    if not math.isfinite(float(value)) or value <= 0:
        return default_value
    return value
