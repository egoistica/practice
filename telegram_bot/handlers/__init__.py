from __future__ import annotations

from aiogram import Dispatcher

from .auth import router as auth_router
from .common import router as common_router
from .graph import router as graph_router
from .menu import router as menu_router
from .summary import router as summary_router
from .upload import router as upload_router


def register_handlers(dispatcher: Dispatcher) -> None:
    dispatcher.include_router(auth_router)
    dispatcher.include_router(upload_router)
    dispatcher.include_router(summary_router)
    dispatcher.include_router(graph_router)
    dispatcher.include_router(menu_router)
    dispatcher.include_router(common_router)
