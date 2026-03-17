from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import redis.asyncio as redis
from fastapi.encoders import jsonable_encoder

from .config import settings

logger = logging.getLogger(__name__)

CACHE_TTL_ONE_DAY_SECONDS = 24 * 60 * 60
CACHE_TTL_LIST_SECONDS = 5 * 60

_redis_client: redis.Redis | None = None


async def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _safe_version(value: datetime | None) -> str:
    if value is None:
        return "none"
    return value.isoformat()


def lecture_list_cache_key(user_id: UUID, skip: int, limit: int, sort_order: str) -> str:
    return f"cache:lectures:list:user:{user_id}:skip:{skip}:limit:{limit}:sort:{sort_order}"


def lecture_list_cache_index(user_id: UUID) -> str:
    return f"cache:index:lectures:list:user:{user_id}"


def history_list_cache_key(user_id: UUID, skip: int, limit: int, sort_order: str) -> str:
    return f"cache:history:list:user:{user_id}:skip:{skip}:limit:{limit}:sort:{sort_order}"


def history_list_cache_index(user_id: UUID) -> str:
    return f"cache:index:history:list:user:{user_id}"


def summary_cache_key(user_id: UUID, lecture_id: UUID, lecture_updated_at: datetime | None) -> str:
    return (
        f"cache:summary:user:{user_id}:lecture:{lecture_id}:"
        f"lecture_updated_at:{_safe_version(lecture_updated_at)}"
    )


def summary_cache_index(lecture_id: UUID) -> str:
    return f"cache:index:summary:lecture:{lecture_id}"


def graph_cache_key(user_id: UUID, lecture_id: UUID, lecture_updated_at: datetime | None) -> str:
    return (
        f"cache:graph:user:{user_id}:lecture:{lecture_id}:"
        f"lecture_updated_at:{_safe_version(lecture_updated_at)}"
    )


def graph_cache_index(lecture_id: UUID) -> str:
    return f"cache:index:graph:lecture:{lecture_id}"


async def cache_get_json(key: str) -> Any | None:
    try:
        client = await _get_redis_client()
        raw = await client.get(key)
    except Exception:
        logger.exception("Redis cache read failed for key=%s", key)
        return None

    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON payload in cache for key=%s", key)
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None:
    payload = json.dumps(jsonable_encoder(value), ensure_ascii=False, separators=(",", ":"))
    try:
        client = await _get_redis_client()
        await client.set(key, payload, ex=max(1, int(ttl_seconds)))
    except Exception:
        logger.exception("Redis cache write failed for key=%s", key)


async def cache_add_to_index(index_key: str, cache_key: str, ttl_seconds: int) -> None:
    try:
        client = await _get_redis_client()
        await client.sadd(index_key, cache_key)
        await client.expire(index_key, max(1, int(ttl_seconds)))
    except Exception:
        logger.exception("Redis cache index add failed for index=%s key=%s", index_key, cache_key)


async def cache_invalidate_index(index_key: str) -> None:
    try:
        client = await _get_redis_client()
        keys = await client.smembers(index_key)
        if keys:
            await client.delete(*list(keys))
        await client.delete(index_key)
    except Exception:
        logger.exception("Redis cache index invalidation failed for index=%s", index_key)
