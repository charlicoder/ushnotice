"""
Handler for `user.registered` or `user_registered` event.

Sends an OTP / verification message via SMS (and optionally Email/WhatsApp if provided).
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class UserRegisteredHandler:
    """Processes user registration events by sending verification OTP."""

    event_type: str = "user.registered"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        # 1. Resolve SMS Recipient
        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        otp = data.get("otp") or data.get("verification_code") or ""
        expiry_minutes = data.get("expiry_minutes", 5)

        if sms_recipient:
            request = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=sms_recipient,
                template_name="user/otp",
                template_context={
                    "customer_name": sms_recipient.name,
                    "otp": otp,
                    "expiry_minutes": expiry_minutes,
                },
                customer_id=str(data.get("customer_id") or data.get("user_id") or ""),
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(request)

        # 2. Resolve Email Recipient (if provided)
        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            email_request = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="user/otp",
                template_context={
                    "customer_name": email_recipient.name,
                    "otp": otp,
                    "expiry_minutes": expiry_minutes,
                },
                subject="Welcome to USHSPA – Verification Code",
                customer_id=str(data.get("customer_id") or data.get("user_id") or ""),
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(email_request)

        if not sms_recipient and not email_recipient:
            logger.warning(
                "Neither phone number nor email address in user.registered event",
                event_id=envelope.event_id_str,
            )
