"""
Handler for `email.verification.requested` / `EMAIL_VERIFICATION_REQUESTED` event.

Sends an email verification OTP to the customer's email address.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.schemas.envelope import EventEnvelope
from app.events.handlers.base import HandlerContext
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class EmailVerificationHandler:
    """Sends an email verification OTP when EMAIL_VERIFICATION_REQUESTED is received."""

    event_type: str = "email.verification.requested"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data

        otp = data.get("otp") or data.get("verification_code") or ""
        expiry_minutes = data.get("expiry_minutes", 10)

        context = {
            "otp": otp,
            "expiry_minutes": expiry_minutes,
        }

        # Only send via email channel
        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if not email_recipient:
            logger.warning(
                "No email address in email.verification.requested event",
                event_id=envelope.event_id_str,
            )
            return

        req = NotificationRequest(
            event_id=envelope.event_id_str,
            recipient=email_recipient,
            template_name="user/email_verify",
            template_context={**context, "customer_name": email_recipient.name},
            subject="Verify your USHSPA email address",
            customer_id=str(data.get("customer_id") or data.get("user_id") or ""),
            correlation_id=envelope.correlation_id_str,
        )
        await NotificationService(ctx).send(req)
