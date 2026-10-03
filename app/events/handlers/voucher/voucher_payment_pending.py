"""
Handler for `voucher.payment_pending` event.

Fired when a gift voucher is created and awaiting payment confirmation.
Marks the recipient phone as pending in Redis so that concurrent
`customer.new_created` events do not dispatch duplicate generic welcome messages.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope

logger = get_logger(__name__)


class VoucherPaymentPendingHandler:
    """Processes voucher.payment_pending events by caching recipient phone."""

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
        if norm_phone:
            try:
                from app.core.redis import get_redis
                redis_client = get_redis()
                await redis_client.setex(f"voucher_recipient_pending:{norm_phone}", 3600, "1")
                logger.info(
                    "voucher_payment_pending_cached_recipient",
                    event_id=envelope.event_id_str,
                    voucher_id=str(data.get("id") or ""),
                    phone=norm_phone,
                )
            except Exception as exc:
                logger.debug("voucher_payment_pending_redis_set_failed", error=str(exc))
