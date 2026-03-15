from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import BotSettings
from handlers import register_handlers


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [telegram_bot] %(name)s: %(message)s",
    )


async def run() -> None:
    settings = BotSettings.from_env()
    if settings.mode == "webhook":
        raise RuntimeError(
            "Webhook mode is not implemented yet. Set TELEGRAM_BOT_MODE=polling."
        )

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    register_handlers(dispatcher)

    logging.info(
        "Starting telegram bot in %s mode (API_BASE_URL=%s)",
        settings.mode,
        settings.api_base_url,
    )

    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


def main() -> None:
    configure_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
