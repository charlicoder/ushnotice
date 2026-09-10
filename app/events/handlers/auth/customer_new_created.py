"""
Handler for `customer.new_created` / `CUSTOMER_NEW_CREATED` event.

Fires when a new customer account is auto-created via the get-or-create API
(no OTP flow). Sends the customer an SMS with their temporary password and
instructions to download the USH Spa app.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class CustomerNewCreatedHandler:
    """Processes customer.new_created events by sending a welcome SMS."""

    event_type: str = "customer.new_created"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        if not sms_recipient:
            logger.warning(
                "No phone number in customer.new_created event — skipping SMS",
                event_id=envelope.event_id_str,
            )
            return

        # Build display name from first_name / last_name or name field
        customer_name = (
            data.get("name")
            or " ".join(
                filter(None, [data.get("first_name", ""), data.get("last_name", "")])
            )
            or ""
        ).strip()

        req = NotificationRequest(
            event_id=envelope.event_id_str,
            recipient=sms_recipient,
            template_name="user/customer_new_created",
            template_context={
                "customer_name": customer_name,
                "password": data.get("password") or "",
            },
            customer_id=str(data.get("user_id") or data.get("customer_id") or ""),
            correlation_id=envelope.correlation_id_str,
        )
        await service.send(req)

        logger.info(
            "Welcome SMS sent for new customer",
            event_id=envelope.event_id_str,
            phone=sms_recipient.address,
        )


class CustomerCreatedHandler(CustomerNewCreatedHandler):
    """Processes customer.created events by sending a welcome SMS with password."""

    event_type: str = "customer.created"
