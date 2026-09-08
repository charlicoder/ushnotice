"""Stub WhatsApp provider for tests and local development."""
from __future__ import annotations

import uuid

from app.core.logging import get_logger
from app.notifications.domain.value_objects import DeliveryResult

logger = get_logger(__name__)


class StubWhatsAppProvider:
    """No-op WhatsApp provider for tests and local development."""

    provider_name: str = "stub"

    async def send_message(
        self,
        *,
        to: str,
        body: str,
        template_name: str | None = None,
        template_language: str | None = None,
        template_components: list[dict] | None = None,
    ) -> DeliveryResult:
        fake_id = f"stub-wa-{uuid.uuid4().hex[:8]}"
        logger.info(
            "[STUB] WhatsApp message sent (no actual delivery)",
            provider="stub",
            to=to,
            template=template_name,
            provider_message_id=fake_id,
        )
        return DeliveryResult.ok(
            provider_message_id=fake_id,
            raw_response={"stub": True, "to": to},
        )
