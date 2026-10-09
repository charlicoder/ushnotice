"""
Handler for `booking.refund_completed` event.

Processes both automated payment gateway and manual branch/desk refunds:
1. Sends customer confirmation notification via SMS and Email.
2. Triggers credit note and outbound refund payment reconciliation in ushanr.
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


class RefundCompletedHandler:
    """Processes booking.refund_completed events."""

    event_type: str = "booking.refund_completed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        refund_number = str(data.get("refund_number") or "")
        booking_ref = str(data.get("booking_reference") or data.get("booking_number") or "")
        amount = str(data.get("refund_amount") or data.get("amount") or "")
        fee = str(data.get("cancellation_fee") or "0.000")
        currency = str(data.get("currency") or "KWD")
        refund_method = str(data.get("refund_method") or "cash")
        refund_type = str(data.get("refund_type") or "manual")

        context = {
            "booking_reference": booking_ref,
            "refund_number": refund_number,
            "amount": amount,
            "refund_amount": amount,
            "cancellation_fee": fee,
            "currency": currency,
            "refund_method": refund_method,
            "refund_type": refund_type,
        }

        customer_id = str(data.get("customer_id") or "")
        booking_id = str(data.get("booking_id") or "")
        branch_id = str(data.get("branch_id") or "")
        processed_by = str(data.get("processed_by") or "")
        correlation_id = envelope.correlation_id_str

        # ── 1. Send SMS notification ──────────────────────────────────────────
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
            try:
                await service.send(req_sms)
            except Exception as exc:
                logger.warning("refund_sms_notification_failed", error=str(exc))

        # ── 2. Send Email notification ────────────────────────────────────────
        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            subject = f"Refund Confirmation – {refund_number or booking_ref}"
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
            try:
                await service.send(req_email)
            except Exception as exc:
                logger.warning("refund_email_notification_failed", error=str(exc))

        # ── 3. Common Accounting: Credit Note & Outbound Payment in ushanr ────
        if booking_id:
            await trigger_credit_note(
                source_document_type="booking",
                source_document_id=booking_id,
                notes=f"Refund {refund_number} ({refund_method.upper()}) for booking {booking_ref}".strip(),
                correlation_id=correlation_id,
                cancellation_fee=fee,
                refund_amount=amount,
                refund_method=refund_method,
                refund_number=refund_number,
                branch_id=branch_id or None,
                processed_by=processed_by or None,
            )
