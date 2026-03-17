from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.token_transaction import TokenTransaction
from app.models.user import User


class InsufficientTokenBalanceError(RuntimeError):
    pass


def _normalize_idempotency_key(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    key = raw_value.strip()
    if not key:
        return None
    if len(key) > 191:
        raise ValueError("idempotency_key is too long; maximum length is 191 characters")
    return key


async def check_balance(user_id: UUID, required: int) -> bool:
    required_amount = int(required)
    if required_amount <= 0:
        return True

    async with AsyncSessionLocal() as db:
        balance = await db.scalar(select(User.token_balance).where(User.id == user_id))
        if balance is None:
            return False
        return int(balance) >= required_amount


async def deduct_tokens(
    user_id: UUID,
    amount: int,
    reason: str,
    *,
    idempotency_key: str | None = None,
) -> TokenTransaction:
    amount_value = int(amount)
    if amount_value <= 0:
        raise ValueError("amount must be greater than zero")

    normalized_reason = reason.strip() or "token deduction"
    normalized_key = _normalize_idempotency_key(idempotency_key)

    async with AsyncSessionLocal() as db:
        try:
            user = await _get_user_for_update(db, user_id)
            existing = await _get_by_idempotency_key(db, user_id, normalized_key)
            if existing is not None:
                return existing

            if user.token_balance < amount_value:
                raise InsufficientTokenBalanceError(
                    f"Insufficient token balance: required={amount_value}, available={user.token_balance}"
                )

            user.token_balance -= amount_value
            tx = TokenTransaction(
                user_id=user.id,
                amount=-amount_value,
                reason=normalized_reason,
                idempotency_key=normalized_key,
            )
            db.add(tx)
            await db.commit()
            await db.refresh(tx)
            return tx
        except IntegrityError:
            await db.rollback()
            existing = await _get_by_idempotency_key(db, user_id, normalized_key)
            if existing is not None:
                return existing
            raise


async def add_tokens(
    user_id: UUID,
    amount: int,
    reason: str,
    *,
    idempotency_key: str | None = None,
) -> TokenTransaction:
    amount_value = int(amount)
    if amount_value <= 0:
        raise ValueError("amount must be greater than zero")

    normalized_reason = reason.strip() or "token top-up"
    normalized_key = _normalize_idempotency_key(idempotency_key)

    async with AsyncSessionLocal() as db:
        try:
            user = await _get_user_for_update(db, user_id)
            existing = await _get_by_idempotency_key(db, user_id, normalized_key)
            if existing is not None:
                return existing
            user.token_balance += amount_value
            tx = TokenTransaction(
                user_id=user.id,
                amount=amount_value,
                reason=normalized_reason,
                idempotency_key=normalized_key,
            )
            db.add(tx)
            await db.commit()
            await db.refresh(tx)
            return tx
        except IntegrityError:
            await db.rollback()
            existing = await _get_by_idempotency_key(db, user_id, normalized_key)
            if existing is not None:
                return existing
            raise


async def _get_by_idempotency_key(
    db: AsyncSession,
    user_id: UUID,
    idempotency_key: str | None,
) -> TokenTransaction | None:
    if not idempotency_key:
        return None
    return (
        await db.execute(
            select(TokenTransaction).where(
                TokenTransaction.user_id == user_id,
                TokenTransaction.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


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
