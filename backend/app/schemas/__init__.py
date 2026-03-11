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
from .lecture import CreateLectureRequest, LLMRequestConfig, LectureListResponse, LectureResponse
from .summary import SummaryBlock, SummaryResponse, TranscriptResponse, TranscriptSegment

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
    "LLMRequestConfig",
    "LectureListResponse",
    "LectureResponse",
    "LoginRequest",
    "RefreshRequest",
    "RegisterRequest",
    "SummaryBlock",
    "SummaryResponse",
    "TokenResponse",
    "TranscriptResponse",
    "TranscriptSegment",
]
