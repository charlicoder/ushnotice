"""
Handler for `user.verified` event.

Sends a welcome / verification confirmation notification to the customer.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class UserVerifiedHandler:
    """Processes user verified events."""

    event_type: str = "user.verified"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        if sms_recipient:
            req = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=sms_recipient,
                template_name="user/verified",
                template_context={"customer_name": sms_recipient.name},
                customer_id=str(data.get("customer_id") or data.get("user_id") or ""),
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req)

        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            req_email = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="user/verified",
                template_context={"customer_name": email_recipient.name},
                subject="Welcome to USHSPA – Account Verified",
                customer_id=str(data.get("customer_id") or data.get("user_id") or ""),
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_email)
