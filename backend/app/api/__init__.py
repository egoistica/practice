"""API routers."""

from .admin import admin_users_router
from .auth import router as auth_router
from .lectures import router as lectures_router
from .lectures import ws_router as lectures_ws_router

__all__ = ["admin_users_router", "auth_router", "lectures_router", "lectures_ws_router"]
