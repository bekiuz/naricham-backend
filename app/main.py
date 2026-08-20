from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.manus import ManusClient
from app.routes.chat import router as chat_router
from app.routes.health import router as health_router

logger = logging.getLogger("nari.backend")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.manus_client = ManusClient(settings)
    try:
        yield
    finally:
        await app.state.manus_client.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Nari Backend",
        description="Secure FastAPI middle layer for Nari clients and the Manus API v2.",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    application.include_router(health_router)
    application.include_router(chat_router)

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception(
            "Unhandled server error method=%s path=%s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "internal_server_error",
                    "message": "Internal server error",
                }
            },
        )

    return application


app = create_app()
