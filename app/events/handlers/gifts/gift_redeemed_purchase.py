"""
Handler for `gift.redeemed` event.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class GiftRedeemedPurchaseHandler:
    """
    Processes gift.redeemed events.
    """
    event_type: str = "gift.redeemed"

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
        recipient_phone = data.get("recipient_phone") or recipient_data.get("phone_number") or ""
        recipient_language = str(data.get("recipient_language") or "en").lower()

        # ── Notify SENDER ─────────────────────────────────────────────────
        if sender_phone:
            try:
                wa_body_sender = f"🎁 Gift Redeemed — {recipient_name} has redeemed your gift at USHSPA!"
                sender_payload = {
                    "phone_number": sender_phone,
                    "customer_name": sender_name,
                }
                recipient_obj = ChannelResolver.resolve_whatsapp_recipient(sender_payload)
                if recipient_obj:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=recipient_obj,
                        template_name="gifts/redeemed_sender_whatsapp",
                        template_context={"message_body": wa_body_sender},
                        customer_id=None,
                        booking_id=gift_id or None,
                        correlation_id=correlation_id,
                    )
                    await service.send(req)
                    logger.info("gift_redeemed_sender_whatsapp_sent", gift_id=gift_id)
            except Exception as exc:
                logger.warning("gift_redeemed_sender_whatsapp_failed", gift_id=gift_id, error=str(exc))
        else:
            logger.info("gift_redeemed_sender_no_phone", gift_id=gift_id)

        # ── Notify RECIPIENT ──────────────────────────────────────────────
        if recipient_phone:
            try:
                if recipient_language == "ar":
                    wa_body_recipient = "تم استخدام هديتك في USHSPA بنجاح! شكراً لك."
                else:
                    wa_body_recipient = "Your USHSPA gift has been successfully redeemed! Thank you."

                recipient_payload = {
                    "phone_number": recipient_phone,
                    "customer_name": recipient_name,
                }
                recipient_obj = ChannelResolver.resolve_whatsapp_recipient(recipient_payload)
                if recipient_obj:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=recipient_obj,
                        template_name="gifts/redeemed_recipient_whatsapp",
                        template_context={"message_body": wa_body_recipient},
                        customer_id=None,
                        booking_id=gift_id or None,
                        correlation_id=correlation_id,
                    )
                    await service.send(req)
                    logger.info("gift_redeemed_recipient_whatsapp_sent", gift_id=gift_id)
            except Exception as exc:
                logger.warning("gift_redeemed_recipient_whatsapp_failed", gift_id=gift_id, error=str(exc))
        else:
            logger.info("gift_redeemed_recipient_no_phone", gift_id=gift_id)
