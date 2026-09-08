"""
Pytest configuration and test fixtures.
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Force test configuration
os.environ["ENVIRONMENT"] = "local"
os.environ["USHSPA_TOKEN"] = "test-ushspa-secret-token"
os.environ["SMS_PROVIDER"] = "stub"
os.environ["WHATSAPP_PROVIDER"] = "stub"
os.environ["EMAIL_PROVIDER"] = "stub"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.events.handlers.base import HandlerContext
from app.events.registry.handler_registry import build_default_registry
from app.events.router import EventRouter
from app.main import create_app
from app.notifications.infrastructure.repositories import (
    ApiRequestRepository,
    EventProcessingRepository,
    EventRepository,
    NotificationAttemptRepository,
    NotificationRepository,
)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def test_db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory async SQLite database session for unit and service tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def handler_context(test_db_session: AsyncSession) -> HandlerContext:
    """Construct a HandlerContext using the in-memory test database."""
    return HandlerContext.from_session(test_db_session)


@pytest.fixture
def default_router() -> EventRouter:
    """Return EventRouter with all standard handlers registered."""
    registry = build_default_registry()
    return EventRouter(registry)


@pytest.fixture
async def async_client(test_db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client bound to FastAPI application."""
    app = create_app()

    # Override get_db dependency
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db] = _override_get_db

    # Mock Redis health check
    with patch("app.api.v1.health.redis_health_check", AsyncMock(return_value=True)), \
         patch("app.core.redis.redis_health_check", AsyncMock(return_value=True)), \
         patch("app.core.redis.init_redis", AsyncMock()), \
         patch("app.core.redis.close_redis", AsyncMock()), \
         patch("app.events.consumer.sqs_consumer.SQSConsumer.start", AsyncMock()), \
         patch("app.events.consumer.sqs_consumer.SQSConsumer.stop", AsyncMock()):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
