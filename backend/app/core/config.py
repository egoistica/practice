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

_DEV_ENVIRONMENTS = {"development", "dev", "local", "test", "testing"}
_INSECURE_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://app_user:app_password@localhost:5432/app_db",
    "JWT_SECRET": "change_me_to_a_long_random_secret",
}
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:3000,http://127.0.0.1:3000"
)


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
    ENVIRONMENT: str = ""
    DEBUG: bool = False

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    RELOAD: bool = False

    CORS_ORIGINS: str = _DEFAULT_CORS_ORIGINS
    CORS_ALLOW_CREDENTIALS: bool = True

    DATABASE_URL: str = _INSECURE_DEFAULTS["DATABASE_URL"]
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = _INSECURE_DEFAULTS["JWT_SECRET"]
    OPENAI_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    LLM_PROVIDER: str = "openai"
    MEDIA_ROOT: str = "/media"

    @property
    def is_dev_mode(self) -> bool:
        current_env = (self.ENVIRONMENT or self.APP_ENV).lower()
        return self.DEBUG or current_env in _DEV_ENVIRONMENTS

    @property
    def cors_origins_list(self) -> list[str]:
        origins = [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        if "*" in origins:
            if len(origins) > 1:
                raise RuntimeError("CORS_ORIGINS cannot combine '*' with explicit origins")
            return ["*"]
        return origins

    @property
    def cors_allow_all(self) -> bool:
        origins = self.cors_origins_list
        return len(origins) == 1 and origins[0] == "*"

    @property
    def cors_credentials_enabled(self) -> bool:
        if self.cors_allow_all and self.CORS_ALLOW_CREDENTIALS:
            raise RuntimeError(
                "CORS_ALLOW_CREDENTIALS=true is not allowed with wildcard CORS_ORIGINS='*'"
            )
        return self.CORS_ALLOW_CREDENTIALS and not self.cors_allow_all

    def validate_runtime_config(self) -> None:
        for key in ("DATABASE_URL", "JWT_SECRET"):
            value = getattr(self, key, "")
            if not str(value).strip():
                raise RuntimeError(f"{key} must be set")

        if not self.is_dev_mode:
            for key, insecure_default in _INSECURE_DEFAULTS.items():
                value = str(getattr(self, key, ""))
                if value == insecure_default:
                    raise RuntimeError(
                        f"{key} is using insecure default value; set a secure value in environment"
                    )

            llm_provider = str(getattr(self, "LLM_PROVIDER", "")).lower().strip()
            if llm_provider == "openai":
                openai_api_key = str(getattr(self, "OPENAI_API_KEY", ""))
                if not openai_api_key.strip():
                    raise RuntimeError(
                        "OPENAI_API_KEY must be set when LLM_PROVIDER=openai outside development/test environments"
                    )

        _ = self.cors_origins_list
        _ = self.cors_credentials_enabled


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if _HAS_PYDANTIC_SETTINGS:
        current_settings = Settings()
    else:
        current_settings = Settings(
            APP_NAME=os.getenv("APP_NAME", "Lecture Notes API"),
            APP_VERSION=os.getenv("APP_VERSION", "0.1.0"),
            APP_ENV=os.getenv("APP_ENV", "development"),
            ENVIRONMENT=os.getenv("ENVIRONMENT", ""),
            DEBUG=_env_bool("DEBUG", False),
            HOST=os.getenv("HOST", "0.0.0.0"),
            PORT=int(os.getenv("PORT", "8000")),
            RELOAD=_env_bool("RELOAD", False),
            CORS_ORIGINS=os.getenv("CORS_ORIGINS", _DEFAULT_CORS_ORIGINS),
            CORS_ALLOW_CREDENTIALS=_env_bool("CORS_ALLOW_CREDENTIALS", True),
            DATABASE_URL=os.getenv("DATABASE_URL", _INSECURE_DEFAULTS["DATABASE_URL"]),
            REDIS_URL=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            JWT_SECRET=os.getenv("JWT_SECRET", _INSECURE_DEFAULTS["JWT_SECRET"]),
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
            TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            LLM_PROVIDER=os.getenv("LLM_PROVIDER", "openai"),
            MEDIA_ROOT=os.getenv("MEDIA_ROOT", "/media"),
        )

    current_settings.validate_runtime_config()
    return current_settings


settings = get_settings()