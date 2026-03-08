"""API routers."""

from .admin import admin_users_router
from .auth import router as auth_router

__all__ = ["admin_users_router", "auth_router"]
