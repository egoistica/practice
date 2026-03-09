from __future__ import annotations

import logging
import time

import uvicorn
from celery import Celery
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .api import admin_users_router, auth_router, lectures_router
from .core.config import settings
from .core.dependencies import get_celery_app


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    cors_origins = settings.cors_origins_list
    if not cors_origins:
        raise RuntimeError("CORS_ORIGINS must contain at least one origin")

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=settings.cors_credentials_enabled,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(admin_users_router)
    app.include_router(lectures_router)

    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        process_time = time.perf_counter() - start
        response.headers["X-Process-Time"] = f"{process_time:.6f}"
        return response

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        logger.info("%s %s", request.method, request.url.path)
        return await call_next(request)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "backend", "status": "ok"}

    @app.get("/health")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/celery")
    async def celery_health(celery: Celery = Depends(get_celery_app)) -> dict[str, object]:
        try:
            inspector = celery.control.inspect(timeout=1)
            active = inspector.active() if inspector else {}
        except Exception:
            logger.exception("Celery health check failed")
            raise HTTPException(status_code=503, detail="dependency unavailable") from None

        workers = list((active or {}).keys())
        active_tasks = sum(len(tasks) for tasks in (active or {}).values())
        return {"workers": workers, "active_tasks": active_tasks}

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "backend.app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
    )
