from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_admin
from app.models.user import User
from app.schemas.admin_users import AdminAddTokensRequest, AdminAddTokensResponse
from app.services.token_service import add_tokens as add_tokens_service

router = APIRouter(prefix="/admin/tokens", tags=["admin-tokens"])
logger = logging.getLogger(__name__)


@router.post("/{user_id}", response_model=AdminAddTokensResponse, status_code=status.HTTP_200_OK)
async def add_tokens(
    user_id: UUID,
    payload: AdminAddTokensRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
) -> AdminAddTokensResponse:
    current_balance = await db.scalar(select(User.token_balance).where(User.id == user_id))
    if current_balance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await add_tokens_service(
        user_id=user_id,
        amount=payload.amount,
        reason=payload.reason,
    )
    updated_balance = await db.scalar(select(User.token_balance).where(User.id == user_id))
    if updated_balance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    logger.info(
        "admin_action action=add_tokens admin_user_id=%s target_user_id=%s amount=%s",
        admin_user.id,
        user_id,
        payload.amount,
    )
    return AdminAddTokensResponse(
        user_id=user_id,
        token_balance=int(updated_balance),
        added_amount=payload.amount,
    )
