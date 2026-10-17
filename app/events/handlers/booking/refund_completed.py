"""
Handler for `booking.refund_completed` event.

Processes both automated payment gateway and manual branch/desk refunds:
1. Triggers credit note WITH outbound refund payment in ushanr
   (record_refund_payment=True). This posts the actual cash-out entry
   against the credit note that was created at cancellation time.
2. Links the credit note back to the Refund in ushbooknpay.
3. Customer notification intentionally disabled (for now).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.invoicing.triggers import trigger_credit_note
from app.notifications.application.notification_service import NotificationService

logger = get_logger(__name__)


class RefundCompletedHandler:
    """Processes booking.refund_completed events."""

    event_type: str = "booking.refund_completed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data

        refund_id = str(data.get("refund_id") or "")
        refund_number = str(data.get("refund_number") or "")
        booking_id = str(data.get("booking_id") or "")
        booking_ref = str(data.get("booking_reference") or data.get("booking_number") or "")
        amount = str(data.get("refund_amount") or data.get("amount") or "")
        fee = str(data.get("cancellation_fee") or "0.000")
        currency = str(data.get("currency") or "KWD")
        refund_method = str(data.get("refund_method") or "cash")
        refund_type = str(data.get("refund_type") or "manual")
        branch_id = str(data.get("branch_id") or "")
        processed_by = str(data.get("processed_by") or "")
        correlation_id = envelope.correlation_id_str

        # ── 1. Notifications (SMS / WhatsApp / Email) intentionally disabled ──
        # No customer notification is sent for booking.refund_completed events.

        # ── 2. Credit Note with outbound refund payment in ushanr ─────────────
        # The credit note may already exist (created at cancellation time with
        # record_refund_payment=false). This call is idempotent by refund_number
        # in ushanr — if the CN already exists it will only add the outbound
        # refund payment; if not it creates both.
        cn_result = None
        if booking_id:
            cn_result = await trigger_credit_note(
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
                record_refund_payment=True,  # Post the actual cash-out entry
            )

        # ── 3. Link credit note to refund in ushbooknpay ─────────────────────
        credit_note_number = cn_result.get("invoice_name") if cn_result else None
        credit_note_id = cn_result.get("invoice_id") if cn_result else None
        if credit_note_number and (refund_id or refund_number or booking_id):
            link_client = UshBookNPayClient()
            try:
                await link_client.link_refund_credit_note(
                    refund_id=refund_id or None,
                    booking_id=booking_id or None,
                    refund_number=refund_number or None,
                    credit_note_number=credit_note_number,
                    credit_note_id=str(credit_note_id) if credit_note_id else None,
                    correlation_id=correlation_id,
                )
                logger.info(
                    "refund_credit_note_linked",
                    refund_number=refund_number,
                    credit_note_number=credit_note_number,
                    booking_id=booking_id,
                    correlation_id=correlation_id,
                )
            except Exception as exc:
                logger.warning(
                    "refund_credit_note_link_failed",
                    refund_number=refund_number,
                    booking_id=booking_id,
                    error=str(exc),
                    correlation_id=correlation_id,
                )
            finally:
                await link_client.aclose()
