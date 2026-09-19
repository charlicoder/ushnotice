"""
Handler for `gift.claimed` event.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


def _sender_whatsapp_message_en(sender_name: str, recipient_name: str) -> str:
    return f"🎁 Your Gift Has Been Opened!\n\nDear {sender_name}, your gift to {recipient_name} has been opened."


def _sender_whatsapp_message_ar(sender_name: str, recipient_name: str) -> str:
    return f"🎁 تم فتح هديتك!\n\nعزيزي {sender_name}، قام {recipient_name} بفتح هديتك."


class GiftClaimedHandler:
    """
    Processes gift.claimed events.
    """
    event_type: str = "gift.claimed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)
        correlation_id = envelope.correlation_id_str

        gift_id = str(data.get("id") or "")
        sender_data: dict = data.get("sender_data") or {}
        recipient_data: dict = data.get("recipient_data") or {}

        sender_name = sender_data.get("name") or data.get("sender_id") or "A generous friend"
        sender_phone = sender_data.get("phone_number") or ""
        recipient_name = recipient_data.get("name") or "Valued Customer"

        if not sender_phone:
            logger.info("gift_claimed_sender_no_phone", gift_id=gift_id)
            return

        # Try to infer sender language or default to English. Wait, the prompt says:
        # Arabic version: "🎁 تم فتح هديتك!\n\nعزيزي {sender_name}، قام {recipient_name} بفتح هديتك."
        # The prompt doesn't specify how to choose sender language, so we might just use English or send both?
        # Actually, let's check if there is a `sender_language` or `recipient_language`. If not, just send English or use recipient_language if provided?
        # Let's check sender_language if available, else default to English.
        sender_lang = str(data.get("sender_language") or "en").lower()
        if sender_lang == "ar":
            wa_body = _sender_whatsapp_message_ar(sender_name, recipient_name)
        else:
            wa_body = _sender_whatsapp_message_en(sender_name, recipient_name)

        try:
            sender_payload = {
                "phone_number": sender_phone,
                "customer_name": sender_name,
            }
            recipient = ChannelResolver.resolve_whatsapp_recipient(sender_payload)
            if recipient:
                req = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=recipient,
                    template_name="gifts/claimed_sender_whatsapp",
                    template_context={"message_body": wa_body},
                    customer_id=None,
                    booking_id=gift_id or None,
                    correlation_id=correlation_id,
                )
                await service.send(req)
                logger.info("gift_claimed_sender_whatsapp_sent", gift_id=gift_id)
        except Exception as exc:
            logger.warning("gift_claimed_sender_whatsapp_failed", gift_id=gift_id, error=str(exc))
