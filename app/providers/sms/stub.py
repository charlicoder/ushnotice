"""
Stub SMS provider for local development and automated tests.

Does not make any network calls.  Logs the SMS to stdout and returns a
synthetic success response.  Switch to a real provider in production by
setting ``SMS_PROVIDER`` in the environment.
"""
from __future__ import annotations

import uuid

from app.core.logging import get_logger
from app.notifications.domain.value_objects import DeliveryResult

logger = get_logger(__name__)


class StubSmsProvider:
    """No-op SMS provider for tests and local development."""

    provider_name: str = "stub"

    async def send_sms(
        self,
        *,
        to: str,
        body: str,
        sender_id: str | None = None,
    ) -> DeliveryResult:
        """Log and return a synthetic success without sending anything."""
        fake_id = f"stub-sms-{uuid.uuid4().hex[:8]}"
        logger.info(
            "[STUB] SMS sent (no actual delivery)",
            provider="stub",
            to=to,
            sender_id=sender_id,
            message_preview=body[:50],
            provider_message_id=fake_id,
        )
        return DeliveryResult.ok(
            provider_message_id=fake_id,
            raw_response={"stub": True, "to": to, "body": body},
        )
