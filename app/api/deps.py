"""
Common FastAPI dependencies for endpoints.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_service_token
from app.notifications.infrastructure.repositories import (
    ApiRequestRepository,
    EventProcessingRepository,
    EventRepository,
    NotificationAttemptRepository,
    NotificationRepository,
)

# Database Session Dependency
DBSession = Annotated[AsyncSession, Depends(get_db)]

# Auth Dependency
RequireAuth = require_service_token


def get_event_repo(db: DBSession) -> EventRepository:
    return EventRepository(db)


def get_notification_repo(db: DBSession) -> NotificationRepository:
    return NotificationRepository(db)


def get_attempt_repo(db: DBSession) -> NotificationAttemptRepository:
    return NotificationAttemptRepository(db)


def get_api_request_repo(db: DBSession) -> ApiRequestRepository:
    return ApiRequestRepository(db)
