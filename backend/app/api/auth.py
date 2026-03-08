from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import UnauthorizedException, get_current_user, get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response_for_user(user: User) -> TokenResponse:
    access_token = create_access_token(
        {"user_id": user.id},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = create_refresh_token(user.id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user_id=user.id,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    username = payload.username.strip()
    email = payload.email.strip().lower()

    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is required")
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required")

    existing_by_username = await db.execute(select(User).where(User.username == username))
    if existing_by_username.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    existing_by_email = await db.execute(select(User).where(User.email == email))
    if existing_by_email.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(payload.password),
        is_admin=False,
        is_active=True,
        token_balance=1000,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from None

    await db.refresh(user)
    return _token_response_for_user(user)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    login_value = payload.username.strip()
    login_value_lower = login_value.lower()

    query = select(User).where(
        or_(
            User.username == login_value,
            User.email == login_value_lower,
        )
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.hashed_password):
        raise UnauthorizedException("Invalid username/email or password")
    if not user.is_active:
        raise UnauthorizedException("User is inactive")

    return _token_response_for_user(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        token_payload = decode_token(payload.refresh_token)
    except ValueError as exc:
        raise UnauthorizedException(str(exc)) from exc

    if token_payload.get("type") != "refresh":
        raise UnauthorizedException("Invalid token type")

    raw_user_id = token_payload.get("user_id")
    try:
        user_id = UUID(str(raw_user_id))
    except (ValueError, TypeError):
        raise UnauthorizedException("Invalid token payload") from None

    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedException("User not found")
    if not user.is_active:
        raise UnauthorizedException("User is inactive")

    return _token_response_for_user(user)


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict[str, str | bool]:
    return {
        "user_id": str(user.id),
        "username": user.username,
        "email": user.email,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
    }
