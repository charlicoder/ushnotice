"""
Handler for ``payment_link.failed`` event.

Logs gateway payment link failures to console and structured logs.
"""
from __future__ import annotations

import json
from app.core.logging import get_logger
from app.events.handlers.base import HandlerContext
from app.events.schemas.envelope import EventEnvelope

logger = get_logger(__name__)


class PaymentLinkFailedHandler:
    """Processes ``payment_link.failed`` events."""

    event_type: str = "payment_link.failed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        correlation_id = envelope.correlation_id_str or envelope.event_id_str

        print(
            "=== [PAYMENT-LINK FAILED EVENT] ===",
            json.dumps(data, indent=2, default=str),
            flush=True,
        )

        logger.warning(
            "payment_link_failed_received",
            payment_link_id=data.get("payment_link_id"),
            target_type=data.get("target_type"),
            target_id=data.get("target_id") or data.get("booking_id"),
            gateway=data.get("gateway"),
            provider=data.get("provider"),
            gateway_invoice_id=data.get("gateway_invoice_id"),
            amount=data.get("amount"),
            failure_reason=data.get("failure_reason"),
            correlation_id=correlation_id,
        )
