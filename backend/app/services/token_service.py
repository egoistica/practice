from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.token_transaction import TokenTransaction
from app.models.user import User


class InsufficientTokenBalanceError(RuntimeError):
    pass


async def check_balance(user_id: UUID, required: int) -> bool:
    required_amount = int(required)
    if required_amount <= 0:
        return True

    async with AsyncSessionLocal() as db:
        balance = await db.scalar(select(User.token_balance).where(User.id == user_id))
        if balance is None:
            return False
        return int(balance) >= required_amount


async def deduct_tokens(user_id: UUID, amount: int, reason: str) -> TokenTransaction:
    amount_value = int(amount)
    if amount_value <= 0:
        raise ValueError("amount must be greater than zero")

    normalized_reason = reason.strip() or "token deduction"

    async with AsyncSessionLocal() as db:
        user = await _get_user_for_update(db, user_id)
        if user.token_balance < amount_value:
            raise InsufficientTokenBalanceError(
                f"Insufficient token balance: required={amount_value}, available={user.token_balance}"
            )

        user.token_balance -= amount_value
        tx = TokenTransaction(
            user_id=user.id,
            amount=-amount_value,
            reason=normalized_reason,
        )
        db.add(tx)
        await db.commit()
        await db.refresh(tx)
        return tx


async def add_tokens(user_id: UUID, amount: int, reason: str) -> TokenTransaction:
    amount_value = int(amount)
    if amount_value <= 0:
        raise ValueError("amount must be greater than zero")

    normalized_reason = reason.strip() or "token top-up"

    async with AsyncSessionLocal() as db:
        user = await _get_user_for_update(db, user_id)
        user.token_balance += amount_value
        tx = TokenTransaction(
            user_id=user.id,
            amount=amount_value,
            reason=normalized_reason,
        )
        db.add(tx)
        await db.commit()
        await db.refresh(tx)
        return tx


async def _get_user_for_update(db: AsyncSession, user_id: UUID) -> User:
    user = (
        await db.execute(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if user is None:
        raise ValueError(f"User not found: {user_id}")
    return user
