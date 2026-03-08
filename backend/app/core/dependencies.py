from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from uuid import UUID

from celery import Celery
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.user import User

try:
    from backend.celery_app import celery_app as shared_celery_app
except ModuleNotFoundError as e:
    # Fallback only for missing top-level target module, not for transitive import errors.
    if e.name not in {"backend", "backend.celery_app"}:
        raise
    from celery_app import celery_app as shared_celery_app

bearer_scheme = HTTPBearer(auto_error=False)


class UnauthorizedException(HTTPException):
    def __init__(self, detail: str = "Not authenticated") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenException(HTTPException):
    def __init__(self, detail: str = "Forbidden") -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def get_celery_app() -> Celery:
    return shared_celery_app


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    from app.core.database import get_async_session

    async for session in get_async_session():
        yield session


async def get_current_user(
    token: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Any = Depends(get_db),
) -> "User":
    from app.models.user import User
    from app.core.security import decode_token

    if token is None or not token.credentials:
        raise UnauthorizedException()

    try:
        payload = decode_token(token.credentials)
    except ValueError as exc:
        raise UnauthorizedException(str(exc)) from exc

    raw_user_id = payload.get("user_id")
    try:
        user_id = UUID(str(raw_user_id))
    except (ValueError, TypeError):
        raise UnauthorizedException("Invalid token payload") from None

    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedException("User not found")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    from app.models.user import User

    if not user.is_admin:
        raise ForbiddenException("Admin privileges required")
    return user
