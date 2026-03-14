"""API routers."""

from .admin import admin_stats_router
from .admin import admin_users_router
from .auth import router as auth_router
from .favourites import router as favourites_router
from .history import router as history_router
from .lectures import router as lectures_router
from .lectures import ws_router as lectures_ws_router

__all__ = [
    "admin_stats_router",
    "admin_users_router",
    "auth_router",
    "favourites_router",
    "history_router",
    "lectures_router",
    "lectures_ws_router",
]
