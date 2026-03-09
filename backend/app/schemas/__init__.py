"""Pydantic request/response schemas."""

from .admin_users import (
    AdminAddTokensRequest,
    AdminAddTokensResponse,
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminUpdateUserRequest,
    AdminUserResponse,
    AdminUsersListResponse,
)
from .auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse
from .lecture import CreateLectureRequest, LectureListResponse, LectureResponse

__all__ = [
    "AdminAddTokensRequest",
    "AdminAddTokensResponse",
    "AdminCreateUserRequest",
    "AdminCreateUserResponse",
    "AdminUpdateUserRequest",
    "AdminUserResponse",
    "AdminUsersListResponse",
    "CreateLectureRequest",
    "LectureListResponse",
    "LectureResponse",
    "LoginRequest",
    "RefreshRequest",
    "RegisterRequest",
    "TokenResponse",
]
