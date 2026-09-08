"""
Async Redis client factory.

Provides a single connection pool shared across the process lifetime.
Used for:
- Idempotency short-circuit cache (optional, backed by DB as source of truth)
- Rate limiting
- Statistics/dashboard caching
- Branch contact caching
"""
from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: aioredis.Redis | None = None  # type: ignore[type-arg]


async def init_redis() -> None:
    """Create the Redis connection pool.

    Called once at application startup.
    """
    global _redis_client  # noqa: PLW0603

    settings = get_settings()

    _redis_client = aioredis.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
        decode_responses=True,
        health_check_interval=30,
    )

    # Validate connectivity
    await _redis_client.ping()
    logger.info("Redis connection pool initialised")


async def close_redis() -> None:
    """Close the Redis connection pool.

    Called once during application shutdown.
    """
    global _redis_client  # noqa: PLW0603
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection pool closed")


def get_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    """Return the active Redis client.

    Raises ``RuntimeError`` if :func:`init_redis` has not been called.
    """
    if _redis_client is None:
        raise RuntimeError("Redis client has not been initialised. Call init_redis() first.")
    return _redis_client


async def redis_health_check() -> bool:
    """Return ``True`` if Redis is reachable, ``False`` otherwise."""
    try:
        client = get_redis()
        await client.ping()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis health check failed", error=str(exc))
        return False
