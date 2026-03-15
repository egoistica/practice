from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

try:
    from telegram_bot.config import BotSettings
    from telegram_bot.db import init_db
    from telegram_bot.handlers import register_handlers
    from telegram_bot.handlers.upload import cancel_background_tasks
except ModuleNotFoundError:
    from config import BotSettings
    from db import init_db
    from handlers import register_handlers
    from handlers.upload import cancel_background_tasks


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
    try:
        init_db()
        dispatcher = Dispatcher()
        register_handlers(dispatcher)

        logging.info("Starting telegram bot in %s mode", settings.mode)
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)
    finally:
        try:
            await cancel_background_tasks(bot)
        except Exception:
            logging.exception("Failed to cancel upload background tasks")
        await bot.session.close()


def main() -> None:
    configure_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
