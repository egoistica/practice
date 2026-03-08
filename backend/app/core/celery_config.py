from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CeleryConfig:
    broker_url: str
    result_backend: str
    timezone: str = "UTC"
    task_track_started: bool = True
    task_serializer: str = "json"
    result_serializer: str = "json"
    accept_content: tuple[str, ...] = ("json",)
    enable_utc: bool = True
    include: tuple[str, ...] = ("backend.celery_app",)

    @classmethod
    def from_settings(cls, settings: Any) -> "CeleryConfig":
        redis_url = str(getattr(settings, "REDIS_URL", "redis://localhost:6379/0"))
        return cls(broker_url=redis_url, result_backend=redis_url)

    def to_celery_conf(self) -> dict[str, Any]:
        return {
            "task_track_started": self.task_track_started,
            "task_serializer": self.task_serializer,
            "accept_content": list(self.accept_content),
            "result_serializer": self.result_serializer,
            "timezone": self.timezone,
            "enable_utc": self.enable_utc,
        }