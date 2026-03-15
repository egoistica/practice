from __future__ import annotations

from aiogram import Dispatcher

from .common import router as common_router


def register_handlers(dispatcher: Dispatcher) -> None:
    dispatcher.include_router(common_router)
