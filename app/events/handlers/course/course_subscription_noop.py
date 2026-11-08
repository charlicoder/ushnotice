"""
No-op (ack) handlers for course subscription events that need no notification.

ushbooknpay publishes every course lifecycle event to the notification queue
(contract section F).  These events are acknowledged so they are not treated as
unknown event types; no side effects are performed.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import HandlerContext
from app.events.schemas.envelope import EventEnvelope

logger = get_logger(__name__)

COURSE_NOOP_EVENT_TYPES: tuple[str, ...] = (
    "course_subscription.payment_failed",
    "course_subscription.invoice_created",
    "course_subscription.session_reserved",
    "course_subscription.session_released",
)


class CourseSubscriptionNoopHandler:
    """Acknowledges a course subscription event without side effects."""

    event_type: str = "course_subscription.noop"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        logger.info(
            "course_subscription_event_acknowledged",
            event_type=envelope.event_type,
            event_id=envelope.event_id_str,
            subscription_id=str(data.get("subscription_id") or ""),
            subscription_number=str(data.get("subscription_number") or ""),
        )

