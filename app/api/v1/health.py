"""
Health check endpoints for load balancer and readiness probes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DBSession
from app.core.config import get_settings
from app.core.redis import redis_health_check

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Service Health and Readiness Check")
async def health_check(db: DBSession) -> dict[str, Any]:
    """Inspects DB and Redis connectivity."""
    db_healthy = False
    try:
        await db.execute(text("SELECT 1"))
        db_healthy = True
    except Exception:
        db_healthy = False

    redis_healthy = await redis_health_check()
    settings = get_settings()

    is_healthy = db_healthy and redis_healthy
    resp_body = {
        "status": "healthy" if is_healthy else "unhealthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "checks": {
            "database": "connected" if db_healthy else "error",
            "redis": "connected" if redis_healthy else "error",
        },
    }

    if not is_healthy:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=resp_body)
    return resp_body


@router.get("/ping", summary="Basic Liveness Probe")
async def ping() -> dict[str, str]:
    return {"ping": "pong"}
