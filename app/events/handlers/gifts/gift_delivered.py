"""
Handler for `gift.delivered` event.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)

_GIFT_CARD_PAGE_URL = "https://ushspa.co/gift/{public_token}"


class GiftDeliveredHandler:
    """
    Processes gift.delivered events.
    """
    event_type: str = "gift.delivered"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)
        correlation_id = envelope.correlation_id_str

        gift_id = str(data.get("id") or "")
        recipient_data: dict = data.get("recipient_data") or {}
        
        recipient_name = recipient_data.get("name") or "Valued Customer"
        recipient_phone = data.get("recipient_phone") or recipient_data.get("phone_number") or ""
        recipient_language = str(data.get("recipient_language") or "en").lower()
        public_token = data.get("public_token") or ""
        
        gift_card_url = _GIFT_CARD_PAGE_URL.format(public_token=public_token) if public_token else "https://ushspa.co/"

        if not recipient_phone:
            logger.info("gift_delivered_recipient_no_phone", gift_id=gift_id)
            return

        try:
            if recipient_language == "ar":
                wa_body = f"📦 تم توصيل هديتك المادية من USHSPA! يرجى تأكيد الاستلام: {gift_card_url}"
            else:
                wa_body = f"📦 Your USHSPA Physical Gift has been delivered! Please confirm receipt: {gift_card_url}"

            recipient_payload = {
                "phone_number": recipient_phone,
                "customer_name": recipient_name,
            }
            recipient_obj = ChannelResolver.resolve_whatsapp_recipient(recipient_payload)
            if recipient_obj:
                req = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=recipient_obj,
                    template_name="gifts/delivered_recipient_whatsapp",
                    template_context={"message_body": wa_body},
                    customer_id=None,
                    booking_id=gift_id or None,
                    correlation_id=correlation_id,
                )
                await service.send(req)
                logger.info("gift_delivered_recipient_whatsapp_sent", gift_id=gift_id)
        except Exception as exc:
            logger.warning("gift_delivered_recipient_whatsapp_failed", gift_id=gift_id, error=str(exc))
