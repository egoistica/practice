from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests
from sqlalchemy import select

try:
    from telegram_bot.db import TelegramUser, session_scope
except ModuleNotFoundError:
    from db import TelegramUser, session_scope

DEFAULT_API_BASE_URL = "http://backend:8000"
REQUEST_TIMEOUT_SECONDS = 20


class BotAuthError(RuntimeError):
    pass


class APIUnauthorizedError(BotAuthError):
    pass


@dataclass(frozen=True)
class AuthTokens:
    user_id: str
    access_token: str
    refresh_token: str | None


def _api_base_url() -> str:
    raw = os.getenv("API_BASE_URL")
    return raw.strip() if raw and raw.strip() else DEFAULT_API_BASE_URL


def _url(path: str) -> str:
    return f"{_api_base_url().rstrip('/')}/{path.lstrip('/')}"


def _extract_error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"

    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return f"HTTP {response.status_code}"


def _post(path: str, payload: dict[str, Any], *, auth_token: str | None = None) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    try:
        response = requests.post(
            _url(path),
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise BotAuthError(f"API request failed: {exc}") from exc

    if response.status_code in (401, 403):
        raise APIUnauthorizedError(_extract_error_detail(response))
    if response.status_code >= 400:
        raise BotAuthError(_extract_error_detail(response))

    try:
        return response.json()
    except ValueError as exc:
        raise BotAuthError("API returned invalid JSON") from exc


def _get(path: str, *, auth_token: str) -> dict[str, Any]:
    try:
        response = requests.get(
            _url(path),
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise BotAuthError(f"API request failed: {exc}") from exc

    if response.status_code in (401, 403):
        raise APIUnauthorizedError(_extract_error_detail(response))
    if response.status_code >= 400:
        raise BotAuthError(_extract_error_detail(response))

    try:
        return response.json()
    except ValueError as exc:
        raise BotAuthError("API returned invalid JSON") from exc


def login_with_password(login_value: str, password: str) -> AuthTokens:
    payload = _post("/auth/login", {"username": login_value, "password": password})
    return AuthTokens(
        user_id=str(payload.get("user_id", "")),
        access_token=str(payload.get("access_token", "")),
        refresh_token=str(payload.get("refresh_token", "")) or None,
    )


def login_with_one_time_token(raw_token: str) -> AuthTokens:
    token = raw_token.strip()
    if not token:
        raise BotAuthError("Token must not be empty")

    try:
        payload = _post("/auth/refresh", {"refresh_token": token})
        return AuthTokens(
            user_id=str(payload.get("user_id", "")),
            access_token=str(payload.get("access_token", "")),
            refresh_token=str(payload.get("refresh_token", "")) or token,
        )
    except BotAuthError:
        me = _get("/auth/me", auth_token=token)
        return AuthTokens(
            user_id=str(me.get("user_id", "")),
            access_token=token,
            refresh_token=None,
        )


def _refresh_access_token(refresh_token: str) -> AuthTokens:
    payload = _post("/auth/refresh", {"refresh_token": refresh_token})
    return AuthTokens(
        user_id=str(payload.get("user_id", "")),
        access_token=str(payload.get("access_token", "")),
        refresh_token=str(payload.get("refresh_token", "")) or refresh_token,
    )


def save_telegram_user_auth(
    telegram_id: int,
    username: str | None,
    tokens: AuthTokens,
) -> TelegramUser:
    if not tokens.user_id or not tokens.access_token:
        raise BotAuthError("API did not return required auth data")

    with session_scope() as session:
        user = session.get(TelegramUser, telegram_id)
        if user is None:
            user = TelegramUser(
                telegram_id=telegram_id,
                user_id=tokens.user_id,
                username=username,
                jwt_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
            )
            session.add(user)
            session.flush()
            return user

        user.user_id = tokens.user_id
        user.username = username
        user.jwt_token = tokens.access_token
        user.refresh_token = tokens.refresh_token
        session.flush()
        return user


def get_telegram_user(telegram_id: int) -> TelegramUser | None:
    with session_scope() as session:
        return session.get(TelegramUser, telegram_id)


def clear_telegram_user_auth(telegram_id: int) -> None:
    with session_scope() as session:
        user = session.get(TelegramUser, telegram_id)
        if user is None:
            return
        session.delete(user)


def ensure_authorized_telegram_user(telegram_id: int) -> TelegramUser:
    with session_scope() as session:
        user = session.get(TelegramUser, telegram_id)
        if user is None:
            raise BotAuthError("User is not linked. Use /start to authorize.")

        try:
            _get("/auth/me", auth_token=user.jwt_token)
            session.flush()
            return user
        except APIUnauthorizedError:
            if not user.refresh_token:
                raise BotAuthError("Session expired. Use /start to authorize again.") from None

            refreshed = _refresh_access_token(user.refresh_token)
            user.jwt_token = refreshed.access_token
            user.refresh_token = refreshed.refresh_token
            _get("/auth/me", auth_token=user.jwt_token)
            session.flush()
            return user


def fetch_profile(access_token: str) -> dict[str, Any]:
    return _get("/auth/me", auth_token=access_token)


def list_linked_telegram_users() -> list[TelegramUser]:
    with session_scope() as session:
        result = session.execute(select(TelegramUser).order_by(TelegramUser.created_at.desc()))
        return list(result.scalars().all())
