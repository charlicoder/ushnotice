"""
Notification statistics and metrics endpoint.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import DBSession, RequireAuth
from app.core.config import get_settings
from app.core.redis import get_redis
from app.notifications.infrastructure.repositories import NotificationRepository
from app.notifications.interfaces.schemas import StatisticsResponse

router = APIRouter(prefix="/statistics", tags=["Metrics"], dependencies=[Depends(RequireAuth)])

_STATS_CACHE_KEY = "ushnotice:stats:summary"


@router.get("", response_model=StatisticsResponse, summary="Get Notification Delivery Statistics")
async def get_statistics(db: DBSession) -> StatisticsResponse:
    settings = get_settings()

    # 1. Try Redis cache
    try:
        redis_client = get_redis()
        cached = await redis_client.get(_STATS_CACHE_KEY)
        if cached:
            data = json.loads(cached)
            return StatisticsResponse(**data)
    except Exception:
        pass

    # 2. Query from database
    repo = NotificationRepository(db)
    raw_stats = await repo.get_statistics()

    by_status = raw_stats.get("by_status", {})
    by_channel = raw_stats.get("by_channel", {})

    total = sum(by_status.values())
    delivered_or_sent = by_status.get("DELIVERED", 0) + by_status.get("SENT", 0)
    success_rate = (delivered_or_sent / total * 100.0) if total > 0 else 100.0

    stats_resp = StatisticsResponse(
        total_notifications=total,
        by_status=by_status,
        by_channel=by_channel,
        success_rate_percentage=round(success_rate, 2),
    )

    # 3. Cache in Redis
    try:
        redis_client = get_redis()
        await redis_client.setex(
            _STATS_CACHE_KEY,
            settings.STATS_CACHE_TTL,
            json.dumps(stats_resp.model_dump()),
        )
    except Exception:
        pass

    return stats_resp
