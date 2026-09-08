"""
Notification management and history endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.api.deps import DBSession, RequireAuth
from app.common.pagination import PaginatedResponse, PaginationParams
from app.notifications.domain.enums import NotificationChannel, NotificationStatus
from app.notifications.domain.value_objects import NotificationRequest, Recipient
from app.notifications.infrastructure.repositories import (
    NotificationAttemptRepository,
    NotificationRepository,
)
from app.notifications.interfaces.schemas import (
    AttemptSchema,
    NotificationDetailResponse,
    NotificationResponse,
    StatusHistorySchema,
)
from app.workers.notification_worker import process_notification_background

router = APIRouter(prefix="/notifications", tags=["Notifications"], dependencies=[Depends(RequireAuth)])


@router.get("", response_model=PaginatedResponse[NotificationResponse], summary="List Notifications with Filters")
async def list_notifications(
    db: DBSession,
    pagination: Annotated[PaginationParams, Depends()],
    channel: Annotated[str | None, Query(description="Filter by channel (sms, whatsapp, email)")] = None,
    status_filter: Annotated[str | None, Query(alias="status", description="Filter by status (CREATED, SENT, FAILED, etc.)")] = None,
    provider: Annotated[str | None, Query(description="Filter by provider")] = None,
    customer_id: Annotated[str | None, Query(description="Filter by customer ID")] = None,
    booking_id: Annotated[str | None, Query(description="Filter by booking ID")] = None,
    correlation_id: Annotated[str | None, Query(description="Filter by correlation ID")] = None,
    date_from: Annotated[datetime | None, Query(description="Filter by created date >= date_from")] = None,
    date_to: Annotated[datetime | None, Query(description="Filter by created date <= date_to")] = None,
) -> PaginatedResponse[NotificationResponse]:
    repo = NotificationRepository(db)
    items, total = await repo.list_notifications(
        channel=channel,
        status=status_filter,
        provider=provider,
        customer_id=customer_id,
        booking_id=booking_id,
        correlation_id=correlation_id,
        date_from=date_from,
        date_to=date_to,
        offset=pagination.offset,
        limit=pagination.page_size,
    )

    response_items = [NotificationResponse.from_orm_masked(item) for item in items]
    return PaginatedResponse.build(response_items, total, pagination)


@router.get("/{notification_id}", response_model=NotificationDetailResponse, summary="Get Notification Details")
async def get_notification(
    notification_id: str,
    db: DBSession,
) -> NotificationDetailResponse:
    repo = NotificationRepository(db)
    attempt_repo = NotificationAttemptRepository(db)

    notification = await repo.get_by_id(notification_id)
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    attempts = await attempt_repo.list_for_notification(notification_id)

    # Build base response with masked recipient
    base = NotificationResponse.from_orm_masked(notification)

    attempt_schemas = [
        AttemptSchema.model_validate(att) for att in attempts
    ]
    history_schemas = [
        StatusHistorySchema.model_validate(hist) for hist in notification.status_history
    ]

    return NotificationDetailResponse(
        **base.model_dump(),
        attempts=attempt_schemas,
        status_history=history_schemas,
    )


@router.post("/{notification_id}/resend", summary="Resend a Notification in Background")
async def resend_notification(
    notification_id: str,
    background_tasks: BackgroundTasks,
    db: DBSession,
) -> dict[str, str]:
    repo = NotificationRepository(db)
    notification = await repo.get_by_id(notification_id)
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    # Construct request
    recipient = Recipient(
        channel=NotificationChannel(notification.channel),
        address=notification.recipient,
        name=notification.recipient_name or "",
    )

    req = NotificationRequest(
        event_id=notification.event_id,
        recipient=recipient,
        template_name=notification.template_name or "user/otp",
        template_context={},
        subject=notification.subject or "",
        customer_id=notification.customer_id,
        booking_id=notification.booking_id,
        correlation_id=notification.correlation_id,
    )

    # Schedule background dispatch
    background_tasks.add_task(
        process_notification_background,
        request=req,
        correlation_id=notification.correlation_id,
    )

    return {"message": "Notification scheduled for resend in background", "notification_id": notification_id}
