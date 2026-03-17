from __future__ import annotations

import logging
import logging.config
import os
import sys
from pathlib import Path
from typing import Any

from .config import settings

_CONFIGURED_SERVICES: set[str] = set()


def _resolve_log_level() -> str:
    raw_level = str(getattr(settings, "LOG_LEVEL", "") or "").strip().upper()
    if raw_level:
        return raw_level
    return "DEBUG" if settings.is_dev_mode else "INFO"


def _resolve_log_file(service_name: str) -> str:
    configured = str(getattr(settings, "LOG_FILE_PATH", "") or "").strip()
    if configured:
        return configured
    normalized = service_name.strip().lower() or "backend"
    return f"logs/{normalized}.log"


def _ensure_parent_dir(path_value: str) -> None:
    try:
        path = Path(path_value)
        parent = path.parent
        if str(parent).strip() and str(parent) != ".":
            parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # File handler setup will fail loudly later if path is invalid.
        pass


def _configure_sentry() -> None:
    dsn = str(getattr(settings, "SENTRY_DSN", "") or "").strip()
    if not dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception:
        logging.getLogger(__name__).warning("SENTRY_DSN is set but sentry_sdk is not installed")
        return

    if getattr(sentry_sdk.Hub.current, "client", None) is not None:
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=str(getattr(settings, "SENTRY_ENVIRONMENT", "") or settings.APP_ENV),
        traces_sample_rate=float(getattr(settings, "SENTRY_TRACES_SAMPLE_RATE", 0.0) or 0.0),
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    )


def setup_logging(service_name: str = "backend") -> None:
    normalized_service = service_name.strip().lower() or "backend"
    if normalized_service in _CONFIGURED_SERVICES:
        return

    log_level = _resolve_log_level()
    log_file = _resolve_log_file(normalized_service)
    _ensure_parent_dir(log_file)

    log_max_bytes = int(getattr(settings, "LOG_MAX_BYTES", 10 * 1024 * 1024) or 10 * 1024 * 1024)
    log_backup_count = int(getattr(settings, "LOG_BACKUP_COUNT", 5) or 5)

    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "standard",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": log_level,
                "formatter": "standard",
                "filename": log_file,
                "maxBytes": max(1, log_max_bytes),
                "backupCount": max(1, log_backup_count),
                "encoding": "utf-8",
            },
        },
        "root": {
            "level": log_level,
            "handlers": ["console", "file"],
        },
    }
    logging.config.dictConfig(config)
    _configure_sentry()
    _CONFIGURED_SERVICES.add(normalized_service)
    logging.getLogger(__name__).debug(
        "Logging configured for service=%s level=%s file=%s pid=%s",
        normalized_service,
        log_level,
        log_file,
        os.getpid(),
    )
