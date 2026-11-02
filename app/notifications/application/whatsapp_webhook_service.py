"""
Meta WhatsApp webhook processing service.

Handles incoming callbacks from Meta WhatsApp Cloud API:
- Status updates (sent, delivered, read, failed)
- Inbound customer messages (text, interactive, button, etc.)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.masking import mask_phone
from app.core.logging import get_logger
from app.notifications.domain.enums import AttemptStatus, NotificationStatus
from app.notifications.infrastructure.repositories import (
    InboundMessageRepository,
    NotificationAttemptRepository,
    NotificationRepository,
)

logger = get_logger(__name__)


class WhatsAppWebhookService:
    """Service to process Meta WhatsApp Cloud API webhook callbacks."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.attempt_repo = NotificationAttemptRepository(db)
        self.notification_repo = NotificationRepository(db)
        self.inbound_repo = InboundMessageRepository(db)

    async def process_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse and process the full Meta WhatsApp webhook payload.

        Returns summary stats of processed events.
        """
        object_type = payload.get("object")
        if object_type != "whatsapp_business_account":
            logger.info("Ignored non-whatsapp_business_account callback", object_type=object_type)
            return {"status": "ignored", "reason": "not_whatsapp_business_account"}

        processed_statuses = 0
        processed_messages = 0

        entries = payload.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                if change.get("field") != "messages":
                    continue

                value = change.get("value", {})
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id")

                # 1. Process delivery statuses (sent, delivered, read, failed)
                statuses = value.get("statuses", [])
                for status_item in statuses:
                    updated = await self._process_status(status_item)
                    if updated:
                        processed_statuses += 1

                # 2. Process inbound customer messages
                messages = value.get("messages", [])
                contacts_map: dict[str, str | None] = {}
                for contact in value.get("contacts", []):
                    wa_id = contact.get("wa_id")
                    if wa_id:
                        contacts_map[wa_id] = contact.get("profile", {}).get("name")

                for msg_item in messages:
                    created = await self._process_inbound_message(
                        msg_item,
                        contacts=contacts_map,
                        phone_number_id=phone_number_id,
                    )
                    if created:
                        processed_messages += 1

        await self.db.flush()
        return {
            "status": "success",
            "processed_statuses": processed_statuses,
            "processed_messages": processed_messages,
        }

    async def _process_status(self, status_item: dict[str, Any]) -> bool:
        """Process a single status callback event."""
        message_id = status_item.get("id")
        status_name = (status_item.get("status") or "").lower()
        recipient_id = status_item.get("recipient_id")

        if not message_id or not status_name:
            return False

        attempt = await self.attempt_repo.get_by_provider_message_id(message_id)
        if not attempt:
            logger.info(
                "Received WhatsApp status for unknown provider_message_id",
                provider_message_id=message_id,
                status=status_name,
                recipient=mask_phone(recipient_id or ""),
            )
            return False

        notification = await self.notification_repo.get_by_id(attempt.notification_id)
        if not notification:
            return False

        logger.info(
            "Processing WhatsApp message status transition",
            provider_message_id=message_id,
            notification_id=notification.id,
            current_status=notification.status,
            new_status=status_name,
        )

        if status_name == "delivered":
            # Only update if not already marked read
            if notification.status != NotificationStatus.READ.value:
                await self.notification_repo.update_status(
                    notification,
                    status=NotificationStatus.DELIVERED,
                    reason="Meta WhatsApp delivery status: delivered",
                )
            attempt.status = AttemptStatus.SUCCESS.value
            return True

        elif status_name == "read":
            await self.notification_repo.update_status(
                notification,
                status=NotificationStatus.READ,
                reason="Meta WhatsApp delivery status: read",
            )
            attempt.status = AttemptStatus.SUCCESS.value
            return True

        elif status_name == "failed":
            errors = status_item.get("errors", [])
            error_code = None
            error_message = "WhatsApp message delivery failed"
            if errors:
                err_dict = errors[0]
                error_code = str(err_dict.get("code", ""))
                error_message = (
                    err_dict.get("message")
                    or err_dict.get("title")
                    or "WhatsApp message delivery failed"
                )

            await self.attempt_repo.mark_failed(
                attempt,
                error_code=error_code,
                error_message=error_message,
                provider_response=status_item,
                is_retryable=False,
            )
            await self.notification_repo.update_status(
                notification,
                status=NotificationStatus.FAILED,
                reason=f"Meta WhatsApp delivery failed: {error_message}",
            )
            return True

        elif status_name == "sent":
            if notification.status in (
                NotificationStatus.PROCESSING.value,
                NotificationStatus.QUEUED.value,
                NotificationStatus.CREATED.value,
            ):
                await self.notification_repo.update_status(
                    notification,
                    status=NotificationStatus.SENT,
                    reason="Meta WhatsApp delivery status: sent",
                )
            return True

        return False

    async def _process_inbound_message(
        self,
        msg_item: dict[str, Any],
        contacts: dict[str, str | None],
        phone_number_id: str | None,
    ) -> bool:
        """Process an inbound message received from a user."""
        msg_id = msg_item.get("id")
        from_number = msg_item.get("from")
        msg_type = msg_item.get("type", "text")

        if not msg_id or not from_number:
            return False

        body: str | None = None
        if msg_type == "text":
            body = msg_item.get("text", {}).get("body")
        elif msg_type == "button":
            body = msg_item.get("button", {}).get("text")
        elif msg_type == "interactive":
            interactive = msg_item.get("interactive", {})
            itype = interactive.get("type")
            if itype == "button_reply":
                body = interactive.get("button_reply", {}).get("title")
            elif itype == "list_reply":
                body = interactive.get("list_reply", {}).get("title")

        sender_name = contacts.get(from_number)

        inbound = await self.inbound_repo.create(
            provider_message_id=msg_id,
            from_number=from_number,
            channel="whatsapp",
            provider="meta",
            to_number_id=phone_number_id,
            sender_name=sender_name,
            message_type=msg_type,
            body=body,
            payload=msg_item,
        )

        logger.info(
            "Persisted inbound WhatsApp message",
            provider_message_id=msg_id,
            from_number=mask_phone(from_number),
            message_type=msg_type,
            inbound_id=inbound.id,
        )
        return True
