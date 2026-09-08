"""
Handler for `payment.refunded` event.

Sends a refund confirmation notification to the customer.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class PaymentRefundedHandler:
    """Processes payment.refunded events."""

    event_type: str = "payment.refunded"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        context = {
            "booking_reference": data.get("booking_reference") or data.get("reference") or "",
            "amount": data.get("refund_amount") or data.get("amount"),
            "currency": data.get("currency", "KWD"),
            "refund_id": data.get("refund_id") or "",
        }

        customer_id = str(data.get("customer_id") or "")
        booking_id = str(data.get("booking_id") or "")

        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        if sms_recipient:
            req_sms = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=sms_recipient,
                template_name="booking/payment_failed",
                template_context={**context, "customer_name": sms_recipient.name},
                customer_id=customer_id or None,
                booking_id=booking_id or None,
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_sms)

        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            ref = context["booking_reference"]
            subject = f"Refund Confirmation – {ref}" if ref else "USHSPA Refund Confirmation"
            req_email = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="booking/payment_failed",
                template_context={**context, "customer_name": email_recipient.name},
                subject=subject,
                customer_id=customer_id or None,
                booking_id=booking_id or None,
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_email)
