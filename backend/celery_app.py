import os

from celery import Celery


redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery("worker", broker=redis_url, backend=redis_url)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="healthcheck.ping")
def ping() -> str:
    return "pong"