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
SYSTEM_PROMPT = (
    "Ты помощник по созданию конспектов лекций. Верни только валидный JSON без markdown. "
    "Точный формат: "
    '{"blocks":[{"title":"...","text":"...","type":"thought|definition|date|conclusion"}]}.'
)
ALLOWED_BLOCK_TYPES = {"thought", "definition", "date", "conclusion"}


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

    request_kwargs: dict[str, Any] = {
        "model": request_cfg["model"],
        "messages": messages,
        "temperature": _resolve_float(llm_config.get("temperature"), 0.2),
        "max_tokens": _resolve_int(llm_config.get("max_tokens"), 1200),
        "timeout": _resolve_timeout(llm_config.get("timeout"), 60.0),
    }
    if request_cfg.get("api_base"):
        request_kwargs["api_base"] = request_cfg["api_base"]
    if request_cfg.get("api_key"):
        request_kwargs["api_key"] = request_cfg["api_key"]

    extra_kwargs = llm_config.get("completion_kwargs")
    if isinstance(extra_kwargs, dict):
        request_kwargs.update(extra_kwargs)

    try:
        response = completion(**request_kwargs)
    except Exception as exc:  # pragma: no cover - runtime/provider specific
        raise LLMServiceError(
            f"LiteLLM request failed for provider={request_cfg['provider']} model={request_cfg['model']}"
        ) from exc

    raw_content = _extract_content(response)
    return _parse_summary_payload(raw_content)


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


def _resolve_timeout(raw_timeout: Any, default_timeout: float) -> float:
    try:
        timeout = float(raw_timeout) if raw_timeout is not None else default_timeout
    except (TypeError, ValueError, OverflowError):
        return default_timeout
    if not math.isfinite(timeout) or timeout <= 0:
        return default_timeout
    return timeout


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
