"""
Main FastAPI Application Entry Point for `ushnotice`.

Configures:
- Structured JSON logging
- Database & Redis connection lifespans
- AWS SQS Consumer background worker
- Middlewares (Request ID, Tracing, CORS)
- API v1 routers
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.v1.router import api_v1_router
from app.common.request_id import RequestIDMiddleware
from app.core.config import get_settings
from app.core.database import close_db, init_db
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis, init_redis
from app.events.consumer.sqs_consumer import SQSConsumer
from app.events.registry.handler_registry import build_default_registry
from app.events.router import EventRouter

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle management."""
    settings = get_settings()

    # 1. Startup
    configure_logging(log_level=settings.LOG_LEVEL, json_logs=settings.is_production)
    logger.info(
        "Starting ushnotice microservice...",
        environment=settings.ENVIRONMENT,
        version=settings.APP_VERSION,
    )

    await init_db()
    await init_redis()

    # Build Event Registry & Router
    registry = build_default_registry()
    router = EventRouter(registry)
    app.state.event_registry = registry
    app.state.event_router = router

    # Start SQS Consumer background task
    sqs_consumer = SQSConsumer(router)
    app.state.sqs_consumer = sqs_consumer
    await sqs_consumer.start()

    logger.info("ushnotice startup complete")

    yield

    # 2. Shutdown
    logger.info("Shutting down ushnotice...")
    await sqs_consumer.stop()
    await close_redis()
    await close_db()
    logger.info("ushnotice shutdown complete")


def create_app() -> FastAPI:
    """FastAPI Application Factory."""
    settings = get_settings()

    app = FastAPI(
        title="USHSPA Notification Microservice (ushnotice)",
        description="Event-driven, multi-channel notification microservice for USHSPA.",
        version=settings.APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    # Middlewares
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API v1 router
    # Mounted at /v1 and /unotice/v1 for gateway compatibility
    app.include_router(api_v1_router)
    app.include_router(api_v1_router, prefix=settings.USHNOTICE_BASE_PATH)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()
