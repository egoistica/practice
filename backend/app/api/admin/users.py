from __future__ import annotations

import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_admin
from app.core.security import hash_password
from app.models.token_transaction import TokenTransaction
from app.models.user import User
from app.schemas.admin_users import (
    AdminAddTokensRequest,
    AdminAddTokensResponse,
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminUpdateUserRequest,
    AdminUserResponse,
    AdminUsersListResponse,
)

router = APIRouter(prefix="/admin/users", tags=["admin-users"])
logger = logging.getLogger(__name__)


def _to_user_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_admin=user.is_admin,
        is_active=user.is_active,
        token_balance=user.token_balance,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def _log_admin_action(
    request: Request,
    admin_user: User,
    action: str,
    target_user_id: UUID | None = None,
) -> None:
    logger.info(
        "admin_action action=%s admin_user_id=%s target_user_id=%s ip=%s",
        action,
        admin_user.id,
        target_user_id,
        _client_ip(request),
    )


@router.get("", response_model=AdminUsersListResponse)
async def list_users(
    request: Request,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    is_active: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
) -> AdminUsersListResponse:
    users_query = select(User)
    count_query = select(func.count()).select_from(User)
    if is_active is not None:
        users_query = users_query.where(User.is_active == is_active)
        count_query = count_query.where(User.is_active == is_active)

    users_result = await db.execute(
        users_query.order_by(User.created_at.desc()).offset(skip).limit(limit)
    )
    users = users_result.scalars().all()
    total = int((await db.execute(count_query)).scalar_one())

    await _log_admin_action(request, admin_user, action="list_users")

    return AdminUsersListResponse(
        items=[_to_user_response(user) for user in users],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("", response_model=AdminCreateUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: AdminCreateUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
) -> AdminCreateUserResponse:
    username = payload.username.strip()
    email = payload.email.strip().lower()
    generated_password: str | None = None

    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is required")
    if len(username) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be at least 3 characters",
        )
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required")
    if len(email) < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email must be at least 5 characters",
        )

    if payload.generate_password:
        generated_password = secrets.token_urlsafe(12)
        plain_password = generated_password
    else:
        plain_password = payload.password or ""

    existing_username = await db.execute(select(User).where(User.username == username))
    if existing_username.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    existing_email = await db.execute(select(User).where(User.email == email))
    if existing_email.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(plain_password),
        is_admin=payload.is_admin,
        is_active=payload.is_active,
        token_balance=1000,
    )
    db.add(user)
    try:
        await db.flush()
        await _log_admin_action(request, admin_user, action="create_user", target_user_id=user.id)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from None

    await db.refresh(user)
    return AdminCreateUserResponse(user=_to_user_response(user), generated_password=generated_password)


@router.patch("/{user_id}", response_model=AdminUserResponse)
async def update_user_status(
    user_id: UUID,
    payload: AdminUpdateUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
) -> AdminUserResponse:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if admin_user.id == user.id and (payload.is_admin is False or payload.is_active is False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot revoke their own admin access or deactivate themselves",
        )

    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.is_admin is not None:
        user.is_admin = payload.is_admin

    await _log_admin_action(request, admin_user, action="update_user_status", target_user_id=user.id)
    await db.commit()
    await db.refresh(user)
    return _to_user_response(user)


@router.delete("/{user_id}", response_model=AdminUserResponse)
async def deactivate_user(
    user_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
) -> AdminUserResponse:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if admin_user.id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot revoke their own admin access or deactivate themselves",
        )

    user.is_active = False
    await _log_admin_action(request, admin_user, action="deactivate_user", target_user_id=user.id)
    await db.commit()
    await db.refresh(user)
    return _to_user_response(user)


@router.post("/{user_id}/tokens", response_model=AdminAddTokensResponse)
async def add_tokens(
    user_id: UUID,
    payload: AdminAddTokensRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
) -> AdminAddTokensResponse:
    result = await db.execute(select(User).where(User.id == user_id).with_for_update())
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    db.add(
        TokenTransaction(
            user_id=user.id,
            amount=payload.amount,
            reason=payload.reason,
        )
    )
    user.token_balance += payload.amount
    await _log_admin_action(request, admin_user, action="add_tokens", target_user_id=user.id)
    await db.commit()
    await db.refresh(user)

    return AdminAddTokensResponse(
        user_id=user.id,
        token_balance=user.token_balance,
        added_amount=payload.amount,
    )
