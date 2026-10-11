"""
Handler for booking-level `booking.payment_failed` event.

Sends a notification to the customer explaining the booking payment failure.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class BookingPaymentFailedHandler:
    """Processes booking payment failed events."""

    event_type: str = "booking.payment_failed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        # Notifications (SMS / WhatsApp / Email) intentionally disabled for this event.
        logger.info(
            "booking_payment_failed_no_notification",
            booking_id=str(envelope.data.get("booking_id") or ""),
        )
