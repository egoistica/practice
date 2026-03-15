from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BotSettings:
    telegram_bot_token: str
    api_base_url: str
    mode: str

    @classmethod
    def from_env(cls) -> "BotSettings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        raw_api_base_url = os.getenv("API_BASE_URL")
        api_base_url = (
            raw_api_base_url.strip()
            if raw_api_base_url and raw_api_base_url.strip()
            else "http://backend:8000"
        )
        mode = os.getenv("TELEGRAM_BOT_MODE", "polling").strip().lower() or "polling"

        if mode not in {"polling", "webhook"}:
            raise RuntimeError(
                "TELEGRAM_BOT_MODE must be either 'polling' or 'webhook'"
            )
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

        return cls(
            telegram_bot_token=token,
            api_base_url=api_base_url,
            mode=mode,
        )
