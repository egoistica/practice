from __future__ import annotations

import logging
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .core.config import settings


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

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "backend.app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
    )
