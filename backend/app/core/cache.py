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


def _index_version_key(index_key: str) -> str:
    return f"{index_key}:version"


def _index_members_key(index_key: str, version: int) -> str:
    return f"{index_key}:members:v{version}"


def _versioned_cache_key(key: str, version: int) -> str:
    return f"{key}:v{version}"


async def _get_index_version(index_key: str) -> int:
    client = await _get_redis_client()
    version_key = _index_version_key(index_key)
    try:
        await client.setnx(version_key, "1")
        raw = await client.get(version_key)
        version = int(str(raw or "1"))
    except Exception:
        logger.exception("Redis cache version read failed for index=%s", index_key)
        return 1
    return max(1, version)


async def cache_get_index_version(index_key: str) -> int:
    return await _get_index_version(index_key)


async def cache_get_json(key: str, *, index_key: str | None = None) -> Any | None:
    concrete_key = key
    if index_key:
        version = await _get_index_version(index_key)
        concrete_key = _versioned_cache_key(key, version)

    try:
        client = await _get_redis_client()
        raw = await client.get(concrete_key)
    except Exception:
        logger.exception("Redis cache read failed for key=%s", concrete_key)
        return None

    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON payload in cache for key=%s", concrete_key)
        return None


async def cache_set_json(
    key: str,
    value: Any,
    ttl_seconds: int,
    *,
    index_key: str | None = None,
    expected_version: int | None = None,
) -> None:
    concrete_key = key
    if index_key:
        version = await _get_index_version(index_key)
        if expected_version is not None and int(expected_version) != version:
            logger.debug(
                "Skip cache write due to version mismatch for index=%s key=%s expected=%s actual=%s",
                index_key,
                key,
                expected_version,
                version,
            )
            return
        concrete_key = _versioned_cache_key(key, version)

    payload = json.dumps(jsonable_encoder(value), ensure_ascii=False, separators=(",", ":"))
    try:
        client = await _get_redis_client()
        await client.set(concrete_key, payload, ex=max(1, int(ttl_seconds)))
    except Exception:
        logger.exception("Redis cache write failed for key=%s", concrete_key)


async def cache_add_to_index(
    index_key: str,
    cache_key: str,
    ttl_seconds: int,
    *,
    expected_version: int | None = None,
) -> None:
    try:
        client = await _get_redis_client()
        version = await _get_index_version(index_key)
        if expected_version is not None and int(expected_version) != version:
            logger.debug(
                "Skip cache index add due to version mismatch for index=%s key=%s expected=%s actual=%s",
                index_key,
                cache_key,
                expected_version,
                version,
            )
            return
        members_key = _index_members_key(index_key, version)
        concrete_cache_key = _versioned_cache_key(cache_key, version)
        await client.sadd(members_key, concrete_cache_key)
        await client.expire(members_key, max(1, int(ttl_seconds)))
    except Exception:
        logger.exception("Redis cache index add failed for index=%s key=%s", index_key, cache_key)


async def cache_invalidate_index(index_key: str) -> None:
    try:
        client = await _get_redis_client()
        version_key = _index_version_key(index_key)
        next_version = await client.incr(version_key)
        previous_members_key = _index_members_key(index_key, max(1, int(next_version) - 1))
        await client.delete(previous_members_key)
    except Exception:
        logger.exception("Redis cache index invalidation failed for index=%s", index_key)
