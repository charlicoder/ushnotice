"""
Handler for `user.password_reset_requested` or `password_reset` event.

Sends a password reset link or OTP to the user via SMS and/or Email.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class PasswordResetHandler:
    """Processes password reset requests by dispatching reset instructions."""

    event_type: str = "user.password_reset_requested"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        # Context for template
        otp = data.get("otp") or data.get("reset_code")
        reset_link = data.get("reset_link") or data.get("url")
        expiry_minutes = data.get("expiry_minutes", 15)

        context = {
            "otp": otp,
            "reset_link": reset_link,
            "expiry_minutes": expiry_minutes,
        }

        # 1. SMS if phone is present
        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        if sms_recipient:
            req_sms = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=sms_recipient,
                template_name="user/password_reset",
                template_context={**context, "customer_name": sms_recipient.name},
                customer_id=str(data.get("customer_id") or data.get("user_id") or ""),
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_sms)

        # 2. Email if email is present
        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            req_email = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="user/password_reset",
                template_context={**context, "customer_name": email_recipient.name},
                subject="Reset Your USHSPA Password",
                customer_id=str(data.get("customer_id") or data.get("user_id") or ""),
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_email)
