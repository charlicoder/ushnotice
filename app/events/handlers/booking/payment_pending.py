"""
Handler for booking-level `booking.payment_pending` event.

Sends a reminder to complete payment.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class BookingPaymentPendingHandler:
    """Processes booking payment pending events."""

    event_type: str = "booking.payment_pending"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        context = {
            "booking_reference": data.get("booking_reference") or data.get("reference") or "",
            "branch_name": data.get("branch_name") or "USHSPA",
            "appointment_date": data.get("appointment_date") or data.get("date") or "",
            "appointment_time": data.get("appointment_time") or data.get("time") or "",
        }

        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        if sms_recipient:
            req_sms = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=sms_recipient,
                template_name="booking/payment_pending",
                template_context={**context, "customer_name": sms_recipient.name},
                customer_id=str(data.get("customer_id") or "") or None,
                booking_id=str(data.get("booking_id") or "") or None,
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_sms)

        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            ref = context["booking_reference"]
            subject = f"Payment Reminder – {ref}" if ref else "Complete Your USHSPA Booking Payment"
            req_email = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="booking/payment_pending",
                template_context={**context, "customer_name": email_recipient.name},
                subject=subject,
                customer_id=str(data.get("customer_id") or "") or None,
                booking_id=str(data.get("booking_id") or "") or None,
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_email)
