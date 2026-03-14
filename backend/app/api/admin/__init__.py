"""Admin API routers."""

from .stats import router as admin_stats_router
from .users import router as admin_users_router

__all__ = ["admin_stats_router", "admin_users_router"]
