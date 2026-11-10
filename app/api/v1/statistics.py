"""
Notification statistics and metrics endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import DBSession, RequireAuth
from app.notifications.infrastructure.repositories import NotificationRepository
from app.notifications.interfaces.schemas import StatisticsResponse

router = APIRouter(prefix="/statistics", tags=["Metrics"], dependencies=[Depends(RequireAuth)])


@router.get("", response_model=StatisticsResponse, summary="Get Notification Delivery Statistics")
async def get_statistics(db: DBSession) -> StatisticsResponse:
    repo = NotificationRepository(db)
    raw_stats = await repo.get_statistics()

    by_status = raw_stats.get("by_status", {})
    by_channel = raw_stats.get("by_channel", {})

    total = sum(by_status.values())
    delivered_or_sent = by_status.get("DELIVERED", 0) + by_status.get("SENT", 0)
    success_rate = (delivered_or_sent / total * 100.0) if total > 0 else 100.0

    return StatisticsResponse(
        total_notifications=total,
        by_status=by_status,
        by_channel=by_channel,
        success_rate_percentage=round(success_rate, 2),
    )
