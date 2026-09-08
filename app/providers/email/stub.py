"""Stub email provider for tests and local development."""
from __future__ import annotations

import uuid

from app.core.logging import get_logger
from app.notifications.domain.value_objects import DeliveryResult

logger = get_logger(__name__)


class StubEmailProvider:
    """No-op email provider for tests and local development."""

    provider_name: str = "stub"

    async def send_email(
        self,
        *,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
        reply_to: str | None = None,
    ) -> DeliveryResult:
        fake_id = f"stub-email-{uuid.uuid4().hex[:8]}"
        logger.info(
            "[STUB] Email sent (no actual delivery)",
            provider="stub",
            to=to,
            subject=subject,
            provider_message_id=fake_id,
        )
        return DeliveryResult.ok(
            provider_message_id=fake_id,
            raw_response={"stub": True, "to": to, "subject": subject},
        )
