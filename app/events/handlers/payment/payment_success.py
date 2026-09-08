"""
Handler for `payment.success` or `payment.completed` event.

Sends payment receipt / confirmation notification.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class PaymentSuccessHandler:
    """Processes payment.success events."""

    event_type: str = "payment.success"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        context = {
            "booking_reference": data.get("booking_reference") or data.get("reference") or "",
            "amount": data.get("amount"),
            "currency": data.get("currency", "KWD"),
            "transaction_id": data.get("transaction_id") or data.get("payment_id") or "",
            "branch_name": data.get("branch_name") or "USHSPA",
            "appointment_date": data.get("appointment_date") or "",
            "appointment_time": data.get("appointment_time") or "",
            "service_name": data.get("service_name") or "Spa Service",
        }

        customer_id = str(data.get("customer_id") or "")
        booking_id = str(data.get("booking_id") or "")

        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        if sms_recipient:
            req_sms = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=sms_recipient,
                template_name="booking/confirmed",
                template_context={**context, "customer_name": sms_recipient.name},
                customer_id=customer_id or None,
                booking_id=booking_id or None,
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_sms)

        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            ref = context["booking_reference"]
            subject = f"Payment Receipt – {ref}" if ref else "Your USHSPA Payment Receipt"
            req_email = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="booking/confirmed_email",
                template_context={
                    **context,
                    "customer_name": email_recipient.name,
                    "total_amount": context["amount"],
                },
                subject=subject,
                customer_id=customer_id or None,
                booking_id=booking_id or None,
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_email)
