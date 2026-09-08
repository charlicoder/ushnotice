"""
Handler for `booking.created` event.

No actions taken yet — placeholder for future implementation.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope

logger = get_logger(__name__)


class BookingRequestedHandler:
    """Processes booking.created events — placeholder, no actions yet."""

    event_type: str = "booking.created"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        booking_id = data.get("booking_id") or ""
        customer_id = data.get("customer_id") or ""

        # TODO: Add actions here in future (e.g. internal alerts, analytics)
        print(
            f"[booking.created] Received booking.created event — "
            f"booking_id={booking_id} customer_id={customer_id}. "
            f"No actions configured yet."
        )
        logger.info(
            "booking_created_received_no_action",
            booking_id=booking_id,
            customer_id=customer_id,
            event_id=envelope.event_id_str,
        )
