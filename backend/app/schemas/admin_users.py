from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AdminUserResponse(BaseModel):
    id: UUID
    username: str
    email: str
    is_admin: bool
    is_active: bool
    token_balance: int
    created_at: datetime
    updated_at: datetime


class AdminUsersListResponse(BaseModel):
    items: list[AdminUserResponse]
    total: int
    skip: int
    limit: int


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: str = Field(min_length=5, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    generate_password: bool = False
    is_admin: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def validate_password_source(self) -> "AdminCreateUserRequest":
        if self.generate_password and self.password:
            raise ValueError("Provide either password or set generate_password=true, not both")
        if not self.generate_password and not self.password:
            raise ValueError("Either password must be provided or generate_password must be true")
        return self


class AdminCreateUserResponse(BaseModel):
    user: AdminUserResponse
    generated_password: str | None = None


class AdminUpdateUserRequest(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None

    @model_validator(mode="after")
    def validate_not_empty(self) -> "AdminUpdateUserRequest":
        if self.is_active is None and self.is_admin is None:
            raise ValueError("At least one field must be provided")
        return self


class AdminAddTokensRequest(BaseModel):
    amount: int = Field(gt=0)
    reason: str = Field(default="admin adjustment", min_length=3, max_length=255)


class AdminAddTokensResponse(BaseModel):
    user_id: UUID
    token_balance: int
    added_amount: int
