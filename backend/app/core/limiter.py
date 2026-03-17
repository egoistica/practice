from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .security import decode_token


def _extract_bearer_token(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    raw = authorization_header.strip()
    if not raw:
        return None
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw or None


def user_limit_key(request: Request) -> str:
    # Prefer stable authenticated identity over IP.
    token = _extract_bearer_token(request.headers.get("Authorization"))
    if token:
        try:
            payload = decode_token(token)
            user_id = str(payload.get("user_id", "")).strip()
            if user_id:
                return f"user:{user_id}"
        except ValueError:
            pass

    # Fallback for unauthenticated endpoints (e.g. /auth/login).
    return get_remote_address(request)


limiter = Limiter(key_func=user_limit_key, headers_enabled=True)


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    retry_after: str | None = None
    if isinstance(exc, RateLimitExceeded):
        value: Any = getattr(exc, "retry_after", None)
        if value is not None:
            retry_after = str(value)

    detail = "Too many requests. Please retry later."
    response = JSONResponse(status_code=429, content={"detail": detail})
    if retry_after:
        response.headers["Retry-After"] = retry_after
    return response
