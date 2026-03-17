from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TokenTransactionResponse(BaseModel):
    id: UUID
    amount: int
    reason: str
    created_at: datetime


class TokenBalanceResponse(BaseModel):
    balance: int
    transactions: list[TokenTransactionResponse]


class TokenHistoryResponse(BaseModel):
    items: list[TokenTransactionResponse]
    total: int
    skip: int
    limit: int


class TokenOperationCostItem(BaseModel):
    action: str
    cost: int


class TokenOperationCostsResponse(BaseModel):
    items: list[TokenOperationCostItem]
