from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.token_transaction import TokenTransaction
from app.models.user import User
from app.schemas.tokens import TokenBalanceResponse, TokenTransactionResponse

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
