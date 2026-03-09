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
from .engagement import (
    FavouriteLectureResponse,
    FavouritesListResponse,
    HistoryLectureResponse,
    HistoryListResponse,
)
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
    "FavouriteLectureResponse",
    "FavouritesListResponse",
    "HistoryLectureResponse",
    "HistoryListResponse",
    "LectureListResponse",
    "LectureResponse",
    "LoginRequest",
    "RefreshRequest",
    "RegisterRequest",
    "TokenResponse",
]
