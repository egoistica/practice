from __future__ import annotations

import logging
import time
from uuid import UUID

import uvicorn
from celery import Celery
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .api import (
    admin_stats_router,
    admin_tokens_router,
    admin_users_router,
    auth_router,
    favourites_router,
    history_router,
    lectures_router,
    lectures_ws_router,
    tokens_router,
)
from .core.config import settings
from .core.dependencies import get_celery_app
from .core.limiter import limiter, rate_limit_exceeded_handler
from .core.ownership import extract_bearer_token, extract_lecture_id_from_path
from .core.security import decode_token
from .models.lecture import Lecture
from .services.progress_service import start_progress_listener, stop_progress_listener
from .core.database import AsyncSessionLocal


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
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=settings.cors_credentials_enabled,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SlowAPIMiddleware)
    app.include_router(auth_router)
    app.include_router(admin_users_router)
    app.include_router(admin_stats_router)
    app.include_router(admin_tokens_router)
    app.include_router(favourites_router)
    app.include_router(history_router)
    app.include_router(lectures_router)
    app.include_router(lectures_ws_router)
    app.include_router(tokens_router)

    @app.on_event("startup")
    async def startup_progress_listener() -> None:
        await start_progress_listener()

    @app.on_event("shutdown")
    async def shutdown_progress_listener() -> None:
        await stop_progress_listener()

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

    @app.middleware("http")
    async def lecture_ownership_middleware(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        lecture_id = extract_lecture_id_from_path(request.url.path)
        if lecture_id is None:
            return await call_next(request)

        token = extract_bearer_token(request)
        if not token:
            return await call_next(request)

        try:
            payload = decode_token(token)
            user_id = UUID(str(payload.get("user_id")))
        except (ValueError, TypeError):
            # Let auth dependencies return canonical 401 response.
            return await call_next(request)

        async with AsyncSessionLocal() as db:
            lecture_owner_id = (
                await db.execute(
                    select(Lecture.user_id).where(Lecture.id == lecture_id)
                )
            ).scalar_one_or_none()

        # Preserve "not found" behavior for missing lecture.
        if lecture_owner_id is None:
            return await call_next(request)

        if lecture_owner_id != user_id:
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

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
