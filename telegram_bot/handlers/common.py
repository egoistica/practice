from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="common")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(
        "Привет! Бот запущен и готов к работе.\n"
        "Отправь любое сообщение, я отвечу."
    )


@router.message()
async def handle_message(message: Message) -> None:
    text = (message.text or "").strip()
    if text:
        await message.answer(f"Получено сообщение: {text}")
        return
    await message.answer("Получено сообщение (не текстовый формат).")
