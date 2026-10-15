"""
Handler for `sms.send` and `sms_send` events.

Processes single and mass SMS sending requests published by ushauth.
Dispatches messages to each recipient phone number via NotificationService.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationChannel
from app.notifications.domain.value_objects import NotificationRequest, Recipient

logger = get_logger(__name__)


class SendSMSHandler:
    """Processes `sms.send` events for single or mass SMS dispatch."""

    event_type: str = "sms.send"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data or {}
        service = NotificationService(ctx)

        # 1. Resolve content
        content = (
            data.get("content")
            or data.get("message")
            or data.get("sms_content")
            or ""
        ).strip()
        if not content:
            logger.warning(
                "sms_send_event_missing_content",
                event_id=envelope.event_id_str,
            )
            return

        # 2. Resolve phone numbers
        raw_numbers = data.get("phone_numbers") or []
        if isinstance(raw_numbers, str):
            raw_numbers = [n.strip() for n in raw_numbers.split(",") if n.strip()]
        elif not isinstance(raw_numbers, list):
            raw_numbers = []

        single_number = data.get("phone_number")
        if single_number and str(single_number).strip() not in raw_numbers:
            raw_numbers.append(str(single_number).strip())

        phone_numbers: list[str] = [str(n).strip() for n in raw_numbers if str(n).strip()]
        if not phone_numbers:
            logger.warning(
                "sms_send_event_no_recipients",
                event_id=envelope.event_id_str,
            )
            return

        language = str(data.get("language") or "ar").strip().lower()

        logger.info(
            "sms_send_event_processing_started",
            event_id=envelope.event_id_str,
            recipient_count=len(phone_numbers),
            language=language,
        )

        success_count = 0
        fail_count = 0

        for idx, phone in enumerate(phone_numbers):
            try:
                recipient = Recipient(
                    channel=NotificationChannel.SMS,
                    address=phone,
                    language=language,
                )
                request = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=recipient,
                    template_name="sms/direct",
                    template_context={
                        "raw_body": content,
                        "message": content,
                        "content": content,
                    },
                    customer_id=str(data.get("customer_id") or ""),
                    correlation_id=envelope.correlation_id_str,
                )
                await service.send(request)
                success_count += 1
            except Exception as exc:
                fail_count += 1
                logger.error(
                    "sms_send_to_recipient_failed",
                    phone=phone,
                    event_id=envelope.event_id_str,
                    error=str(exc),
                )

            # Avoid hitting gateway rate limits on bulk sends
            if len(phone_numbers) > 1 and idx < len(phone_numbers) - 1:
                await asyncio.sleep(0.05)

        logger.info(
            "sms_send_event_processing_completed",
            event_id=envelope.event_id_str,
            total=len(phone_numbers),
            success_count=success_count,
            fail_count=fail_count,
        )
