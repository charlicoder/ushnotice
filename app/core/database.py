"""
Async SQLAlchemy engine and session factory.

The engine is created once at startup and reused for the lifetime of the
process.  Each request gets its own :class:`AsyncSession` via
:func:`get_db`, which is used as a FastAPI dependency.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Module-level singletons, initialised by :func:`init_db`.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy ORM models."""


async def init_db() -> None:
    """Create the engine and session factory.

    Call once during application startup (lifespan).
    """
    global _engine, _session_factory  # noqa: PLW0603

    settings = get_settings()

    _engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_timeout=settings.DATABASE_POOL_TIMEOUT,
        pool_pre_ping=True,
        echo=settings.DEBUG,
        future=True,
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    logger.info("Database engine initialised", url=settings.DATABASE_URL.split("@")[-1])


async def close_db() -> None:
    """Dispose the engine connection pool.

    Call once during application shutdown (lifespan).
    """
    global _engine  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        logger.info("Database engine disposed")
        _engine = None


def get_engine() -> AsyncEngine:
    """Return the active engine, raising if not initialised."""
    if _engine is None:
        raise RuntimeError("Database engine has not been initialised. Call init_db() first.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the active session factory, raising if not initialised."""
    if _session_factory is None:
        raise RuntimeError(
            "Session factory has not been initialised. Call init_db() first."
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an ``AsyncSession`` per request.

    The session is automatically closed after the request completes, and any
    uncommitted transaction is rolled back on exception.

    Usage::

        @router.get("/example")
        async def handler(db: AsyncSession = Depends(get_db)):
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Async context-manager variant of :func:`get_db`.

    Use this outside of FastAPI request handlers, e.g. in background workers
    and the SQS consumer.

    Usage::

        async with get_db_context() as db:
            repo = EventRepository(db)
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
