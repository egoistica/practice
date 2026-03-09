from __future__ import annotations

from uuid import UUID

from celery import Celery

try:
    from backend.app.core.celery_config import CeleryConfig
    from backend.app.core.config import settings
except ModuleNotFoundError:
    from app.core.celery_config import CeleryConfig
    from app.core.config import settings


celery_config = CeleryConfig.from_settings(settings)

celery_app = Celery(
    "backend_worker",
    broker=celery_config.broker_url,
    backend=celery_config.result_backend,
    include=list(celery_config.include),
)
celery_app.conf.update(**celery_config.to_celery_conf())


@celery_app.task(name="healthcheck.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="lectures.broadcast_progress")
def publish_lecture_progress(lecture_id: str, progress: int, status_value: str | None = None) -> dict[str, object]:
    """Publish lecture progress updates from worker processes."""
    try:
        from backend.app.api.lectures import broadcast_progress_sync
    except ModuleNotFoundError:
        from app.api.lectures import broadcast_progress_sync

    lecture_uuid = UUID(str(lecture_id))
    normalized_progress = max(0, min(100, int(progress)))
    broadcast_progress_sync(lecture_uuid, normalized_progress, status_value)
    return {
        "lecture_id": str(lecture_uuid),
        "progress": normalized_progress,
        "status": status_value,
    }
