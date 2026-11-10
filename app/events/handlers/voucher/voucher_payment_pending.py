"""
Handler for `voucher.payment_pending` event.

Fired when a gift voucher is created and awaiting payment confirmation.
No external notification is sent while payment is still pending.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope

logger = get_logger(__name__)


class VoucherPaymentPendingHandler:
    """Processes voucher.payment_pending events as an acknowledged no-op."""

    event_type: str = "voucher.payment_pending"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        recipient_details: dict = data.get("recipient_details") or data.get("recipient_data") or {}
        recipient_phone = (
            recipient_details.get("phone_number")
            or data.get("recipient_phone")
            or ""
        )
        norm_phone = "".join(c for c in recipient_phone if c.isdigit())
        logger.info(
            "voucher_payment_pending_received",
            event_id=envelope.event_id_str,
            voucher_id=str(data.get("id") or ""),
            phone=norm_phone,
        )
