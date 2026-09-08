"""
Background notification processing worker using FastAPI BackgroundTasks.

Enables asynchronous dispatching of notifications without blocking HTTP requests.
Operates within its own DB context.
"""
from __future__ import annotations

from app.core.database import get_db_context
from app.core.logging import bind_request_context, get_logger
from app.events.handlers.base import HandlerContext
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


async def process_notification_background(
    request: NotificationRequest,
    correlation_id: str | None = None,
) -> None:
    """Execute notification delivery in a background task.

    Opens an isolated DB session context, constructs HandlerContext, and calls NotificationService.
    """
    bind_request_context(
        event_id=request.event_id,
        correlation_id=correlation_id or request.correlation_id,
    )

    logger.info(
        "Starting background notification dispatch",
        event_id=request.event_id,
        channel=request.recipient.channel.value,
        recipient=request.recipient.masked_address,
    )

    async with get_db_context() as db:
        ctx = HandlerContext.from_session(db)
        service = NotificationService(ctx)
        try:
            await service.send(request)
            await db.commit()
            logger.info(
                "Background notification dispatched successfully",
                event_id=request.event_id,
                channel=request.recipient.channel.value,
            )
        except Exception as exc:
            await db.rollback()
            logger.exception(
                "Background notification processing failed",
                event_id=request.event_id,
                channel=request.recipient.channel.value,
                error=str(exc),
            )
