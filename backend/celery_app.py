from __future__ import annotations

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