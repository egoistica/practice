from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Optional

from litellm import completion

from app.core.config import settings

logger = logging.getLogger(__name__)


SUMMARY_PROMPT = (
    "Суммаризируй этот текст лекции, выделяя главные мысли, определения, даты, выводы. "
    "Структурируй как список логических блоков."
)
ENTITY_PROMPT_BASE = (
    "Найди именованные сущности в тексте: термины, персоналии, теории. "
    "Для каждой сущности найди связи с другими сущностями. "
    "Верни JSON с узлами {id, label, type, mentions:[{position_in_text, timecode?}]} "
    "(timecode может быть null или отсутствовать, если тайминг неизвестен). "
    "и рёбрами {source, target, label}."
)
ENRICH_PROMPT_BASE = (
    "Даны сущности и их связи. Добавь релевантные сущности и связи, которые логически связаны, "
    "но не упомянуты в исходном тексте. Пометь новые узлы флагом 'enriched': true. "
    "Верни расширенный JSON."
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
    "Если время упоминания неизвестно, указывай timecode: null или опускай поле timecode."
    "Если сущности не найдены, верни строго: {\"nodes\": [], \"edges\": []}."
)
ENRICH_SYSTEM_PROMPT = (
    "Ты расширяешь граф сущностей. Верни только валидный JSON без markdown. "
    "Формат: "
    '{"nodes":[{"id":"...","label":"...","type":"...","enriched":true,'
    '"mentions":[{"position_in_text":0,"timecode":null}]}],'
    '"edges":[{"source":"...","target":"...","label":"..."}]}.'
    "Добавляй только новые релевантные узлы и связи; существующие не дублируй."
)
ALLOWED_BLOCK_TYPES = {"thought", "definition", "date", "conclusion"}
ALLOWED_ENTITY_NODE_TYPES = {"term", "technology", "concept", "person"}
DEFAULT_EDGE_LABEL = "related_to"
RESERVED_COMPLETION_KWARGS = {
    "messages",
    "model",
    "api_key",
    "api_base",
    "timeout",
    "temperature",
    "top_p",
    "max_tokens",
    "openai_api_key",
    "anthropic_api_key",
    "azure_api_key",
    "custom_llm_provider",
    "project",
}
SUMMARY_AGENT_PROMPT = (
    "Ты помощник по созданию конспектов лекций.\n"
    "Верни только валидный JSON строго по заданной схеме.\n"
    "Не используй markdown.\n"
    "Не добавляй пояснений вне JSON.\n\n"
    "Каждый блок ОБЯЗАТЕЛЬНО должен содержать поля: title, text, type.\n"
    "Поле type ОБЯЗАТЕЛЬНО должно быть одним из: thought, definition, date, conclusion.\n"
    "Если не подходит ни один специальный тип, используй thought.\n"
    "Выделяй главные мысли, определения, даты и выводы.\n"
    "Разбивай результат на логические блоки.\n"
    "Если текст шумный после распознавания речи, убирай мусор и сохраняй только полезную информацию.\n"
)
ENTITY_GRAPH_AGENT_PROMPT = (
    "Ты помощник по извлечению сущностей из лекций.\n"
    "Верни только валидный JSON строго по заданной схеме.\n"
    "Не используй markdown.\n"
    "Не добавляй пояснений вне JSON.\n\n"
    "Нужно извлечь сущности и связи между сущностями.\n"
    "Возвращай результат только в формате nodes и edges.\n"
    "Не используй поля entities, relations, types или другие альтернативные структуры.\n"
    "Каждый узел должен иметь поля id, label, type.\n"
    "Тип узла должен быть одним из: term, technology, concept, person.\n"
    "Каждая связь должна иметь поля source, target, label.\n"
    "source и target должны ссылаться на id узлов.\n"
    "Если не уверен в типе сущности, используй concept.\n"
)
ENRICHMENT_AGENT_PROMPT = (
    "Ты помощник по расширению учебного материала лекции.\n"
    "Верни только валидный JSON строго по заданной схеме.\n"
    "Не используй markdown.\n"
    "Не добавляй пояснений вне JSON.\n\n"
    "Добавь только связанную и полезную информацию, которой не было напрямую в лекции.\n"
    "Дополняй конспект дополнительными блоками без повторов.\n"
    "Возвращай результат только в формате extra_blocks.\n"
    "Каждый блок extra_blocks должен иметь поля title, text, related_to.\n"
    "Если полезного расширения нет, верни пустой массив.\n"
)
FINAL_SUMMARY_AGENT_PROMPT = (
    "Ты помощник по финальной сборке конспекта лекции.\n"
    "Верни только валидный JSON строго по заданной схеме.\n"
    "Не используй markdown.\n"
    "Не добавляй пояснений вне JSON.\n\n"
    "Объедини готовые блоки конспекта в итоговый структурированный конспект.\n"
    "Сохрани основные мысли, определения, даты и выводы.\n"
    "Убери повторы и не теряй важную информацию.\n"
    "Возвращай результат только в формате final_summary.\n"
    "В final_summary обязательно должны быть поля title и blocks.\n"
    "Каждый блок должен содержать title, text, type.\n"
    "Поле type: thought, definition, date, conclusion.\n"
)


class LLMServiceError(RuntimeError):
    """Base exception for LLM service failures."""


class LLMResponseParseError(LLMServiceError):
    """Raised when LLM response cannot be parsed to expected summary JSON."""


def run_summary_agent(
    lecture_text: str,
    lecture_title: str | None = None,
    mode: str | None = None,
    llm_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if llm_config is None:
        llm_config = {}
    if not isinstance(llm_config, dict):
        raise TypeError("llm_config must be a dict")

    normalized_text = lecture_text.strip()
    if not normalized_text:
        raise ValueError("lecture_text must not be empty")

    request_cfg = _resolve_request_config(llm_config)
    user_prompt = str(llm_config.get("prompt") or SUMMARY_AGENT_PROMPT).strip()
    title_value = str(lecture_title or "").strip() or "Untitled lecture"
    mode_value = str(mode or "").strip() or "instant"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{user_prompt}\n\n"
                f"Название лекции: {title_value}\n"
                f"Режим: {mode_value}\n"
                f"Текст лекции:\n{normalized_text}"
            ),
        },
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


def summarize_segment(text: str, llm_config: Optional[dict[str, Any]]) -> dict[str, Any]:
    return run_summary_agent(text, llm_config=llm_config)


def run_entity_graph_agent(
    lecture_text: str,
    selected_entities: Optional[list[str] | str] = None,
    enrichment_enabled: bool = False,
    llm_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if llm_config is None:
        llm_config = {}
    if not isinstance(llm_config, dict):
        raise TypeError("llm_config must be a dict")

    normalized_text = lecture_text.strip()
    if not normalized_text:
        raise ValueError("lecture_text must not be empty")

    selected_clean = _normalize_selected_entities(selected_entities)
    request_cfg = _resolve_request_config(llm_config)
    prompt_base = str(llm_config.get("prompt") or ENTITY_GRAPH_AGENT_PROMPT).strip()
    selected_hint = ", ".join(selected_clean) if selected_clean else "not provided"
    user_prompt = (
        f"{prompt_base}\n"
        f"selected_entities={selected_hint}\n"
        f"enrichment_enabled={str(bool(enrichment_enabled)).lower()}"
    )

    messages = [
        {"role": "system", "content": ENTITY_SYSTEM_PROMPT},
        {"role": "user", "content": f"{user_prompt}\n\nТекст лекции:\n{normalized_text}"},
    ]
    raw_content = _run_llm_completion(
        request_cfg=request_cfg,
        llm_config=llm_config,
        messages=messages,
        default_temperature=0.1,
        default_max_tokens=800,
        default_timeout=90.0,
    )
    return _parse_entities_payload(raw_content, selected_clean)


def extract_entities(
    text: str,
    selected_entities: Optional[list[str] | str] = None,
    llm_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return run_entity_graph_agent(
        lecture_text=text,
        selected_entities=selected_entities,
        enrichment_enabled=False,
        llm_config=llm_config,
    )


def run_enrichment_agent(
    lecture_text: str,
    summary_blocks: list[dict[str, Any]],
    llm_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if llm_config is None:
        llm_config = {}
    if not isinstance(llm_config, dict):
        raise TypeError("llm_config must be a dict")
    if not isinstance(summary_blocks, list):
        raise TypeError("summary_blocks must be a list")

    normalized_text = lecture_text.strip()
    if not normalized_text:
        raise ValueError("lecture_text must not be empty")

    request_cfg = _resolve_request_config(llm_config)
    user_prompt = str(llm_config.get("prompt") or ENRICHMENT_AGENT_PROMPT).strip()
    blocks_json = json.dumps(summary_blocks, ensure_ascii=False)

    messages = [
        {
            "role": "system",
            "content": (
                "Ты расширяешь конспект лекции. "
                "Верни только валидный JSON без markdown. "
                'Формат: {"extra_blocks":[{"title":"...","text":"...","related_to":"..."}]}.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"{user_prompt}\n\n"
                f"Текст лекции:\n{normalized_text}\n\n"
                f"Текущие summary blocks:\n{blocks_json}"
            ),
        },
    ]
    raw_content = _run_llm_completion(
        request_cfg=request_cfg,
        llm_config=llm_config,
        messages=messages,
        default_temperature=0.3,
        default_max_tokens=800,
        default_timeout=90.0,
    )
    return _parse_extra_blocks_payload(raw_content)


def run_final_summary_agent(
    summary_blocks: list[dict[str, Any]],
    lecture_title: str | None,
    llm_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if llm_config is None:
        llm_config = {}
    if not isinstance(llm_config, dict):
        raise TypeError("llm_config must be a dict")
    if not isinstance(summary_blocks, list):
        raise TypeError("summary_blocks must be a list")

    request_cfg = _resolve_request_config(llm_config)
    user_prompt = str(llm_config.get("prompt") or FINAL_SUMMARY_AGENT_PROMPT).strip()
    title_value = str(lecture_title or "").strip() or "Итоговый конспект"
    blocks_json = json.dumps(summary_blocks, ensure_ascii=False)

    messages = [
        {
            "role": "system",
            "content": (
                "Ты финально собираешь конспект. "
                "Верни только валидный JSON без markdown. "
                'Формат: {"final_summary":{"title":"...","blocks":[{"title":"...","text":"...","type":"thought|definition|date|conclusion"}]}}.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"{user_prompt}\n\n"
                f"Название лекции: {title_value}\n"
                f"summary_blocks:\n{blocks_json}"
            ),
        },
    ]

    raw_content = _run_llm_completion(
        request_cfg=request_cfg,
        llm_config=llm_config,
        messages=messages,
        default_temperature=0.2,
        default_max_tokens=1400,
        default_timeout=120.0,
    )
    return _parse_final_summary_payload(raw_content)


def enrich_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    llm_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if llm_config is None:
        llm_config = {}
    if not isinstance(llm_config, dict):
        raise TypeError("llm_config must be a dict")
    if not isinstance(nodes, list):
        raise TypeError("nodes must be a list")
    if not isinstance(edges, list):
        raise TypeError("edges must be a list")

    request_cfg = _resolve_request_config(llm_config)
    user_prompt = str(llm_config.get("prompt") or ENRICH_PROMPT_BASE).strip()
    graph_json = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)

    messages = [
        {"role": "system", "content": ENRICH_SYSTEM_PROMPT},
        {"role": "user", "content": f"{user_prompt}\n\nТекущий граф:\n{graph_json}"},
    ]
    raw_content = _run_llm_completion(
        request_cfg=request_cfg,
        llm_config=llm_config,
        messages=messages,
        default_temperature=0.2,
        default_max_tokens=2000,
        default_timeout=120.0,
    )
    payload = _parse_enrichment_payload(raw_content)
    return merge_graph_data(nodes, edges, payload["nodes"], payload["edges"])


def merge_graph_data(
    base_nodes: list[dict[str, Any]],
    base_edges: list[dict[str, Any]],
    incoming_nodes: list[Any],
    incoming_edges: list[Any],
) -> dict[str, Any]:
    if not isinstance(base_nodes, list):
        raise TypeError("base_nodes must be a list")
    if not isinstance(base_edges, list):
        raise TypeError("base_edges must be a list")
    if not isinstance(incoming_nodes, list):
        raise TypeError("incoming_nodes must be a list")
    if not isinstance(incoming_edges, list):
        raise TypeError("incoming_edges must be a list")
    return _merge_graph_payload(base_nodes, base_edges, incoming_nodes, incoming_edges)


def _resolve_yandex_model_uri(model: str, folder_id: str | None) -> str:
    normalized_model = str(model).strip()
    if not normalized_model:
        normalized_model = "yandexgpt-lite/latest"

    if normalized_model.startswith("gpt://"):
        return normalized_model

    model_suffix = normalized_model.strip("/")
    if "/" not in model_suffix:
        model_suffix = f"{model_suffix}/latest"

    if not folder_id:
        raise LLMServiceError(
            "YANDEXGPT_FOLDER_ID is required when LLM_MODEL is not full URI "
            "(expected format: gpt://<folder_id>/<model>/latest)"
        )
    return f"gpt://{folder_id}/{model_suffix}"


def _resolve_request_config(llm_config: dict[str, Any]) -> dict[str, str | None]:
    provider = str(llm_config.get("provider") or settings.LLM_PROVIDER).strip().lower()
    model = str(llm_config.get("model") or settings.LLM_MODEL).strip()
    api_base = str(llm_config.get("api_base") or "").strip() or None
    api_key = str(llm_config.get("api_key") or "").strip() or None
    project = str(llm_config.get("project") or "").strip() or None

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
        if not api_key:
            raise LLMServiceError(
                "OPENAI_API_KEY is required when LLM provider is 'openai'. "
                "Set OPENAI_API_KEY in environment or pass api_key in llm_config."
            )
    elif provider == "yandex":
        if not api_key and settings.YANDEXGPT_API_KEY and settings.YANDEXGPT_API_KEY.strip():
            api_key = settings.YANDEXGPT_API_KEY.strip()
        if not api_key:
            raise LLMServiceError(
                "YANDEXGPT_API_KEY is required when LLM provider is 'yandex'. "
                "Set YANDEXGPT_API_KEY in environment or pass api_key in llm_config."
            )

        folder_id = str(llm_config.get("folder_id") or settings.YANDEXGPT_FOLDER_ID).strip() or None
        model = _resolve_yandex_model_uri(model, folder_id)
        project = project or folder_id
        if not api_base:
            api_base = str(settings.YANDEXGPT_API_BASE).strip() or None
        if not api_base:
            raise LLMServiceError("YANDEXGPT_API_BASE must be set when provider is 'yandex'")
    elif "/" not in model:
        model = f"{provider}/{model}"

    return {
        "provider": provider,
        "model": model,
        "api_base": api_base,
        "api_key": api_key,
        "project": project,
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
    raw_top_p = llm_config.get("top_p")
    if raw_top_p is not None:
        request_kwargs["top_p"] = _resolve_top_p(raw_top_p, 1.0)
    if request_cfg.get("api_base"):
        request_kwargs["api_base"] = request_cfg["api_base"]
    if request_cfg.get("api_key"):
        request_kwargs["api_key"] = request_cfg["api_key"]
    if request_cfg.get("provider") == "yandex":
        # YandexGPT is called via the OpenAI-compatible API surface.
        request_kwargs["custom_llm_provider"] = "openai"
        if request_cfg.get("project"):
            request_kwargs["project"] = request_cfg["project"]

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
    try:
        payload = _load_json_payload(raw_content)
        payload = _coerce_entity_graph_payload(payload)
        if not isinstance(payload, dict):
            raise LLMResponseParseError("Entity response JSON must be an object")

        nodes_raw, edges_raw = _extract_entity_lists(payload)
        if not isinstance(nodes_raw, list):
            raise LLMResponseParseError(
                f"Entity response must contain 'nodes' as a list (got {type(nodes_raw).__name__})"
            )
        if not isinstance(edges_raw, list):
            raise LLMResponseParseError(
                f"Entity response must contain 'edges' as a list (got {type(edges_raw).__name__})"
            )

        nodes, ref_map = _normalize_nodes(nodes_raw)
        if selected_entities:
            nodes = _filter_nodes_by_selected(nodes, selected_entities)
            allowed_ids = {node["id"] for node in nodes}
            ref_map = {key: node_id for key, node_id in ref_map.items() if node_id in allowed_ids}

        edges = _normalize_edges(edges_raw, ref_map, {node["id"] for node in nodes})
        return {"nodes": nodes, "edges": edges}
    except LLMResponseParseError:
        logger.exception(
            "Failed to parse entity graph payload. raw_content=%s",
            _truncate_for_log(raw_content, limit=2000),
        )
        raise


def _parse_enrichment_payload(raw_content: str) -> dict[str, list[Any]]:
    payload = _load_json_payload(raw_content)
    if not isinstance(payload, dict):
        raise LLMResponseParseError("Enrichment response JSON must be an object")

    nodes_raw = payload.get("nodes")
    edges_raw = payload.get("edges")
    if not isinstance(nodes_raw, list):
        raise LLMResponseParseError("Enrichment response must contain 'nodes' as a list")
    if not isinstance(edges_raw, list):
        raise LLMResponseParseError("Enrichment response must contain 'edges' as a list")

    return {"nodes": nodes_raw, "edges": edges_raw}


def _parse_extra_blocks_payload(raw_content: str) -> dict[str, list[dict[str, str]]]:
    payload = _load_json_payload(raw_content)
    if not isinstance(payload, dict):
        raise LLMResponseParseError("Enrichment agent response JSON must be an object")

    extra_blocks_raw = payload.get("extra_blocks")
    if not isinstance(extra_blocks_raw, list):
        raise LLMResponseParseError("Enrichment agent response must contain 'extra_blocks' as a list")

    extra_blocks: list[dict[str, str]] = []
    for item in extra_blocks_raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        text = str(item.get("text", "")).strip()
        related_to = str(item.get("related_to", "")).strip()
        if not title or not text or not related_to:
            continue
        extra_blocks.append({"title": title, "text": text, "related_to": related_to})

    return {"extra_blocks": extra_blocks}


def _parse_final_summary_payload(raw_content: str) -> dict[str, Any]:
    payload = _load_json_payload(raw_content)
    if not isinstance(payload, dict):
        raise LLMResponseParseError("Final summary response JSON must be an object")

    final_summary_raw = payload.get("final_summary")
    if not isinstance(final_summary_raw, dict):
        raise LLMResponseParseError("Final summary response must contain 'final_summary' object")

    title = str(final_summary_raw.get("title", "")).strip() or "Итоговый конспект"
    blocks = _parse_summary_payload(json.dumps({"blocks": final_summary_raw.get("blocks", [])}, ensure_ascii=False)).get("blocks", [])
    return {"final_summary": {"title": title, "blocks": blocks}}


def _coerce_entity_graph_payload(payload: Any) -> Any:
    current = payload
    wrapper_keys = ("result", "data", "graph", "entity_graph", "payload", "response")
    marker_keys = ("nodes", "edges", "entities", "relations", "links")

    for _ in range(5):
        if not isinstance(current, dict):
            return current
        if any(key in current for key in marker_keys):
            return current

        nested: Any = None
        for key in wrapper_keys:
            candidate = current.get(key)
            if isinstance(candidate, dict):
                nested = candidate
                break
            if isinstance(candidate, str) and "{" in candidate:
                try:
                    candidate_payload = _load_json_payload(candidate)
                except LLMResponseParseError:
                    continue
                if isinstance(candidate_payload, dict):
                    nested = candidate_payload
                    break
        if nested is None:
            return current
        current = nested
    return current


def _extract_entity_lists(payload: dict[str, Any]) -> tuple[Any, Any]:
    nodes_raw = _coerce_list_like(payload.get("nodes"), field_name="nodes")
    if not isinstance(nodes_raw, list):
        for alias in ("entities", "vertexes", "vertices"):
            candidate = _coerce_list_like(payload.get(alias), field_name="nodes")
            if isinstance(candidate, list):
                nodes_raw = candidate
                break

    edges_raw = _coerce_list_like(payload.get("edges"), field_name="edges")
    if not isinstance(edges_raw, list):
        for alias in ("relations", "links", "connections"):
            candidate = _coerce_list_like(payload.get(alias), field_name="edges")
            if isinstance(candidate, list):
                edges_raw = candidate
                break
    if edges_raw is None:
        edges_raw = []

    return nodes_raw, edges_raw


def _coerce_list_like(raw_value: Any, *, field_name: str) -> Any:
    if isinstance(raw_value, list):
        return raw_value

    if isinstance(raw_value, tuple):
        return list(raw_value)

    if isinstance(raw_value, str):
        candidate = raw_value.strip()
        if not candidate:
            return None
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return _coerce_list_like(parsed, field_name=field_name)

    if isinstance(raw_value, dict):
        for key in ("items", "list", "data", field_name):
            nested = raw_value.get(key)
            if nested is raw_value:
                continue
            coerced = _coerce_list_like(nested, field_name=field_name)
            if isinstance(coerced, list):
                return coerced

        if field_name == "nodes" and {"id", "label"} & set(raw_value.keys()):
            return [raw_value]
        if field_name == "edges" and {"source", "target"} <= set(raw_value.keys()):
            return [raw_value]

    return None


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


def _truncate_for_log(value: Any, limit: int = 1000) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(truncated {len(text) - limit} chars)"


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


def _normalize_entity_type(raw_type: Any) -> str:
    value = str(raw_type or "").strip().lower()
    aliases = {
        "term": "term",
        "technology": "technology",
        "tech": "technology",
        "concept": "concept",
        "person": "person",
        "human": "person",
        "name": "person",
    }
    normalized = aliases.get(value, value)
    if normalized not in ALLOWED_ENTITY_NODE_TYPES:
        return "concept"
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
        node_type = _normalize_entity_type(item.get("type"))
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


def _merge_graph_payload(
    base_nodes_raw: list[dict[str, Any]],
    base_edges_raw: list[dict[str, Any]],
    new_nodes_raw: list[Any],
    new_edges_raw: list[Any],
) -> dict[str, Any]:
    base_nodes, base_ref_map = _normalize_nodes(base_nodes_raw)
    new_nodes, new_ref_map = _normalize_nodes(new_nodes_raw)

    existing_enriched_flags = _extract_enriched_flags(base_nodes_raw)
    merged_nodes: list[dict[str, Any]] = []
    node_by_key: dict[str, dict[str, Any]] = {}
    used_ids: set[str] = set()
    ref_map: dict[str, str] = {}

    for node in base_nodes:
        key = _normalize_label_key(str(node.get("label", "")))
        if not key:
            continue
        merged = _register_merged_node(
            node=node,
            enriched=bool(existing_enriched_flags.get(key, False)),
            preserve_mentions=True,
            used_ids=used_ids,
            ref_map=ref_map,
            merged_nodes=merged_nodes,
        )
        _add_aliases_from_ref_map(
            target_ref_map=ref_map,
            source_ref_map=base_ref_map,
            source_canonical_id=str(node.get("id", "")),
            target_canonical_id=str(merged["id"]),
        )
        node_by_key[key] = merged

    for node in new_nodes:
        key = _normalize_label_key(str(node.get("label", "")))
        if not key:
            continue
        existing = node_by_key.get(key)
        if existing is not None:
            if existing.get("type") == "term" and node.get("type") != "term":
                existing["type"] = node.get("type") or existing["type"]
            _add_ref_aliases(
                ref_map,
                str(existing["id"]),
                [node.get("id"), node.get("label"), key],
            )
            _add_aliases_from_ref_map(
                target_ref_map=ref_map,
                source_ref_map=new_ref_map,
                source_canonical_id=str(node.get("id", "")),
                target_canonical_id=str(existing["id"]),
            )
            continue

        merged = _register_merged_node(
            node=node,
            enriched=True,
            preserve_mentions=False,
            used_ids=used_ids,
            ref_map=ref_map,
            merged_nodes=merged_nodes,
        )
        _add_aliases_from_ref_map(
            target_ref_map=ref_map,
            source_ref_map=new_ref_map,
            source_canonical_id=str(node.get("id", "")),
            target_canonical_id=str(merged["id"]),
        )
        node_by_key[key] = merged

    allowed_ids = {str(node["id"]) for node in merged_nodes}
    merged_edges = _normalize_edges([*base_edges_raw, *new_edges_raw], ref_map, allowed_ids)
    return {"nodes": merged_nodes, "edges": merged_edges}


def _register_merged_node(
    node: dict[str, Any],
    enriched: bool,
    preserve_mentions: bool,
    used_ids: set[str],
    ref_map: dict[str, str],
    merged_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    label = str(node.get("label", "")).strip()
    if not label:
        raise LLMResponseParseError("Node label is required")

    raw_id = str(node.get("id", "")).strip()
    candidate_id = raw_id or _slugify(label)
    if candidate_id in used_ids:
        node_id = _make_unique_id(candidate_id, used_ids)
    else:
        used_ids.add(candidate_id)
        node_id = candidate_id

    normalized = {
        "id": node_id,
        "label": label,
        "type": _normalize_entity_type(node.get("type")),
        "mentions": _normalize_mentions(node.get("mentions")) if preserve_mentions else [],
        "enriched": enriched,
    }
    merged_nodes.append(normalized)
    _add_ref_aliases(
        ref_map,
        str(node_id),
        [raw_id, label, _normalize_label_key(label)],
    )
    return normalized


def _extract_enriched_flags(nodes_raw: list[dict[str, Any]]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for item in nodes_raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        key = _normalize_label_key(label)
        if not key:
            continue
        flags[key] = bool(item.get("enriched", False)) or flags.get(key, False)
    return flags


def _add_ref_aliases(ref_map: dict[str, str], canonical_id: str, aliases: list[Any]) -> None:
    for alias in aliases:
        alias_key = _normalize_ref(alias)
        if alias_key:
            ref_map[alias_key] = canonical_id


def _add_aliases_from_ref_map(
    target_ref_map: dict[str, str],
    source_ref_map: dict[str, str],
    source_canonical_id: str,
    target_canonical_id: str,
) -> None:
    if not source_canonical_id:
        return
    for alias, canonical_id in source_ref_map.items():
        if canonical_id == source_canonical_id and alias:
            target_ref_map[alias] = target_canonical_id


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
    return _normalize_label_key(str(value))


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

    # Reject booleans explicitly: bool is a subclass of int.
    if isinstance(raw_value, bool):
        return None

    if isinstance(raw_value, int):
        value = raw_value
    elif isinstance(raw_value, float):
        if not math.isfinite(raw_value) or not raw_value.is_integer():
            return None
        value = int(raw_value)
    elif isinstance(raw_value, str):
        candidate = raw_value.strip()
        if not re.fullmatch(r"[+-]?\d+", candidate):
            return None
        try:
            value = int(candidate)
        except (TypeError, ValueError, OverflowError):
            return None
    else:
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


def _resolve_top_p(raw_value: Any, default_value: float) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError, OverflowError):
        return default_value
    if not math.isfinite(value) or value <= 0 or value > 1:
        return default_value
    return value
