"""
API v1 combined router.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.delivery_failures import router as delivery_failures_router
from app.api.v1.events import router as events_router
from app.api.v1.health import router as health_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.statistics import router as statistics_router

api_v1_router = APIRouter(prefix="/v1")

# Mount all sub-routers
api_v1_router.include_router(health_router)
api_v1_router.include_router(notifications_router)
api_v1_router.include_router(events_router)
api_v1_router.include_router(delivery_failures_router)
api_v1_router.include_router(statistics_router)
