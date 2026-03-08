from __future__ import annotations

from celery import Celery

try:
    from backend.celery_app import celery_app as shared_celery_app
except ModuleNotFoundError:
    from celery_app import celery_app as shared_celery_app


def get_celery_app() -> Celery:
    return shared_celery_app