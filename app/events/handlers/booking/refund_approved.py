"""
Handler for `refund.approved` event.

Fired by ushbooknpay when a refund request is approved by management.
- If refund_method == 'money':
    1. Creates a credit note in ushanr without cash movement (record_refund_payment=False).
    2. Links the credit note back to the Refund record in ushbooknpay.
- If refund_method == 'loyalty_points':
    Credit note is not created (loyalty point adjustments do not generate credit notes).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.invoicing.triggers import trigger_credit_note

logger = get_logger(__name__)


class RefundApprovedHandler:
    """Processes refund.approved and booking.refund_approved events."""

    event_type: str = "refund.approved"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data

        refund_id = str(data.get("refund_id") or "")
        refund_number = str(data.get("refund_number") or "")
        booking_id = str(data.get("booking_id") or "")
        booking_ref = str(data.get("booking_reference") or data.get("booking_number") or "")
        amount = str(data.get("refund_amount") or data.get("approved_amount") or data.get("amount") or "")
        fee = str(data.get("cancellation_fee") or "0.000")
        refund_method = str(data.get("refund_method") or "loyalty_points").lower().strip()
        refund_type = str(data.get("refund_type") or "manual")
        branch_id = str(data.get("branch_id") or "")
        processed_by = str(data.get("processed_by") or "")
        correlation_id = envelope.correlation_id_str

        # If approved method is loyalty points, no credit note is required
        if refund_method in ("loyalty_points", "loyalty", "points"):
            logger.info(
                "refund_approved_loyalty_points_credit_note_skipped",
                refund_number=refund_number,
                booking_id=booking_id,
                correlation_id=correlation_id,
            )
            return

        # ── Money Refund Approved: Create credit note in ushanr ──────────────
        cn_result = None
        if booking_id:
            cn_result = await trigger_credit_note(
                source_document_type="booking",
                source_document_id=booking_id,
                notes=f"Approved refund {refund_number} (MONEY) for booking {booking_ref}".strip(),
                correlation_id=correlation_id,
                cancellation_fee=fee,
                refund_amount=amount,
                refund_method=refund_method,
                refund_number=refund_number,
                branch_id=branch_id or None,
                processed_by=processed_by or None,
                record_refund_payment=False,  # Outbound payment recorded upon process_refund
            )

        # ── Link credit note to refund in ushbooknpay ─────────────────────────
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
                    "refund_approved_credit_note_linked",
                    refund_number=refund_number,
                    credit_note_number=credit_note_number,
                    booking_id=booking_id,
                    correlation_id=correlation_id,
                )
            except Exception as exc:
                logger.warning(
                    "refund_approved_credit_note_link_failed",
                    refund_number=refund_number,
                    booking_id=booking_id,
                    error=str(exc),
                    correlation_id=correlation_id,
                )
            finally:
                await link_client.aclose()
