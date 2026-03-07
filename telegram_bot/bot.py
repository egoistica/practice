import logging
import os
import time


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [telegram_bot] %(message)s",
)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logging.warning(
            "TELEGRAM_BOT_TOKEN is not set. Bot is running in idle mode."
        )
    else:
        logging.info("Telegram bot token detected. Running placeholder loop.")

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()