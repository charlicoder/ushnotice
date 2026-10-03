"""
Handler for `payment.refunded` event.

Sends a refund confirmation notification to the customer.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.invoicing.triggers import trigger_credit_note
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
        voucher_id = str(data.get("voucher_id") or "")
        correlation_id = envelope.correlation_id_str

        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        if sms_recipient:
            req_sms = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=sms_recipient,
                template_name="booking/payment_failed",
                template_context={**context, "customer_name": sms_recipient.name},
                customer_id=customer_id or None,
                booking_id=booking_id or None,
                correlation_id=correlation_id,
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
                correlation_id=correlation_id,
            )
            await service.send(req_email)

        # ── Credit note in ushanr (non-blocking) ──────────────────────────────
        # Prefer booking_id, fall back to voucher_id if the refund is for a voucher.
        if booking_id:
            await trigger_credit_note(
                source_document_type="booking",
                source_document_id=booking_id,
                notes=f"Refund — {context['booking_reference']}" if context["booking_reference"] else None,
                correlation_id=correlation_id,
            )
        elif voucher_id:
            await trigger_credit_note(
                source_document_type="gift_voucher",
                source_document_id=voucher_id,
                notes="Refund — Gift Voucher",
                correlation_id=correlation_id,
            )
