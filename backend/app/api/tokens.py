from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.models.token_transaction import TokenTransaction
from app.models.user import User
from app.schemas.tokens import (
    TokenBalanceResponse,
    TokenHistoryResponse,
    TokenOperationCostItem,
    TokenOperationCostsResponse,
    TokenTransactionResponse,
)

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.get("/balance", response_model=TokenBalanceResponse)
async def get_tokens_balance(
    include_transactions: bool = Query(default=True),
    transactions_limit: int = Query(default=5, ge=0, le=20),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TokenBalanceResponse:
    transactions: list[TokenTransactionResponse] = []
    if include_transactions and transactions_limit > 0:
        rows = (
            await db.execute(
                select(TokenTransaction)
                .where(TokenTransaction.user_id == user.id)
                .order_by(TokenTransaction.created_at.desc())
                .limit(transactions_limit)
            )
        ).scalars().all()
        transactions = [
            TokenTransactionResponse(
                id=item.id,
                amount=item.amount,
                reason=item.reason,
                created_at=item.created_at,
            )
            for item in rows
        ]

    return TokenBalanceResponse(
        balance=user.token_balance,
        transactions=transactions,
    )


@router.get("/history", response_model=TokenHistoryResponse)
async def get_tokens_history(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TokenHistoryResponse:
    rows = (
        await db.execute(
            select(TokenTransaction)
            .where(TokenTransaction.user_id == user.id)
            .order_by(TokenTransaction.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
    ).scalars().all()
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(TokenTransaction)
                .where(TokenTransaction.user_id == user.id)
            )
        ).scalar_one()
    )
    items = [
        TokenTransactionResponse(
            id=item.id,
            amount=item.amount,
            reason=item.reason,
            created_at=item.created_at,
        )
        for item in rows
    ]
    return TokenHistoryResponse(
        items=items,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/costs", response_model=TokenOperationCostsResponse)
async def get_operation_costs(
    user: User = Depends(get_current_user),
) -> TokenOperationCostsResponse:
    _ = user
    return TokenOperationCostsResponse(
        items=[
            TokenOperationCostItem(action="Транскрибация", cost=settings.COST_TRANSCRIBE),
            TokenOperationCostItem(action="Суммаризация", cost=settings.COST_SUMMARIZE),
            TokenOperationCostItem(action="Извлечение сущностей", cost=settings.COST_EXTRACT_ENTITIES),
            TokenOperationCostItem(action="Обогащение", cost=settings.COST_ENRICH),
        ]
    )
