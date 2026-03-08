from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
REFRESH_TOKEN_EXPIRE_DAYS = 30


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except (UnknownHashError, ValueError):
        return False


def create_access_token(data: dict, expires_delta: timedelta) -> str:
    if "user_id" not in data:
        raise ValueError("Access token payload must include user_id")
    if expires_delta <= timedelta(0):
        raise ValueError("expires_delta must be greater than zero")

    now = datetime.now(timezone.utc)
    payload = data.copy()
    payload["user_id"] = str(payload["user_id"])
    payload["exp"] = now + expires_delta
    payload.setdefault("type", "access")

    return jwt.encode(
        payload,
        settings.jwt_secret_key_effective,
        algorithm=settings.JWT_ALGORITHM,
    )


def create_refresh_token(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": str(user_id),
        "type": "refresh",
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key_effective,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key_effective,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc

    if "user_id" not in payload:
        raise ValueError("Token payload missing user_id claim")
    if "exp" not in payload:
        raise ValueError("Token payload missing exp claim")
    return payload
