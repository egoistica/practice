from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    _HAS_PYDANTIC_SETTINGS = True
except ImportError:
    BaseSettings = BaseModel  # type: ignore[misc,assignment]
    SettingsConfigDict = dict  # type: ignore[misc,assignment]
    _HAS_PYDANTIC_SETTINGS = False


class Settings(BaseSettings):
    if _HAS_PYDANTIC_SETTINGS:
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )

    APP_NAME: str = "Lecture Notes API"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "development"

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    RELOAD: bool = False

    CORS_ORIGINS: str = "*"

    DATABASE_URL: str = "postgresql+asyncpg://app_user:app_password@localhost:5432/app_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = "change_me_to_a_long_random_secret"
    OPENAI_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    LLM_PROVIDER: str = "openai"
    MEDIA_ROOT: str = "/media"

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if _HAS_PYDANTIC_SETTINGS:
        return Settings()

    return Settings(
        APP_NAME=os.getenv("APP_NAME", "Lecture Notes API"),
        APP_VERSION=os.getenv("APP_VERSION", "0.1.0"),
        APP_ENV=os.getenv("APP_ENV", "development"),
        HOST=os.getenv("HOST", "0.0.0.0"),
        PORT=int(os.getenv("PORT", "8000")),
        RELOAD=os.getenv("RELOAD", "false").lower() in {"1", "true", "yes"},
        CORS_ORIGINS=os.getenv("CORS_ORIGINS", "*"),
        DATABASE_URL=os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://app_user:app_password@localhost:5432/app_db",
        ),
        REDIS_URL=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        JWT_SECRET=os.getenv("JWT_SECRET", "change_me_to_a_long_random_secret"),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        LLM_PROVIDER=os.getenv("LLM_PROVIDER", "openai"),
        MEDIA_ROOT=os.getenv("MEDIA_ROOT", "/media"),
    )


settings = get_settings()