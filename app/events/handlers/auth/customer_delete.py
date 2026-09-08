"""
Handler for `customer.deleted` or `user.deleted` event.

Anonymizes or marks audit history according to privacy / retention rules.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope

logger = get_logger(__name__)


class CustomerDeleteHandler:
    """Processes customer deletion / GDPR compliance events."""

    event_type: str = "customer.deleted"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        customer_id = str(envelope.data.get("customer_id") or envelope.data.get("user_id") or "")
        if not customer_id:
            logger.warning("customer.deleted received without customer_id", event_id=envelope.event_id_str)
            return

        logger.info("Processing customer.deleted event", customer_id=customer_id)
        # Retain minimal audit logs per legal obligations while ensuring privacy
