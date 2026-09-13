"""
Handler for ``shop.order_created`` event.

Fired by ushbooknpay when a new shop order is placed.

Actions:
  1. Send a WhatsApp confirmation message (preferred).
  2. Fall back to SMS if WhatsApp is not available.

The message includes:
  - Order number
  - Total amount
  - A public tracking URL where the customer can follow delivery status
  - The secret tracking_code required to confirm receipt

Message format (EN):
  "Your order #ORD-2026-0001 for 3.500 KWD has been received!
   Track it here: https://<domain>/shop/track/ORD-2026-0001/
   When your package arrives, use code ABC12345 to confirm receipt."

Message format (AR):
  "تم استلام طلبك رقم ORD-2026-0001 بقيمة 3.500 د.ك!
   تابع توصيلتك هنا: https://<domain>/shop/track/ORD-2026-0001/
   عند وصول الطرد استخدم الرمز ABC12345 لتأكيد الاستلام."
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.events.handlers.base import HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


def _whatsapp_message(
    order_number: str,
    total_amount: str,
    currency: str,
    tracking_url: str,
    tracking_code: str,
    customer_name: str,
) -> str:
    """Compose the WhatsApp/SMS confirmation text (English)."""
    return (
        f"Hi {customer_name}, your order *{order_number}* for {total_amount} {currency} "
        f"has been received! 🛍️\n\n"
        f"Track your delivery here:\n{tracking_url}\n\n"
        f"When your package arrives, use code *{tracking_code}* to confirm receipt. "
        f"Keep this code safe — it is required to mark your order as received."
    )


def _whatsapp_message_ar(
    order_number: str,
    total_amount: str,
    currency: str,
    tracking_url: str,
    tracking_code: str,
) -> str:
    """Compose the WhatsApp/SMS confirmation text (Arabic)."""
    return (
        f"تم استلام طلبك رقم *{order_number}* بقيمة {total_amount} {currency} 🛍️\n\n"
        f"تابع توصيلتك هنا:\n{tracking_url}\n\n"
        f"عند وصول الطرد، استخدم الرمز *{tracking_code}* لتأكيد الاستلام. "
        f"احتفظ بهذا الرمز — فهو مطلوب لتأكيد وصول طلبك."
    )


def _sms_message(
    order_number: str,
    total_amount: str,
    currency: str,
    tracking_url: str,
    tracking_code: str,
) -> str:
    """Compact SMS version (character limit aware)."""
    return (
        f"Order {order_number} confirmed. {total_amount} {currency}. "
        f"Track: {tracking_url} | Receipt code: {tracking_code}"
    )


class ShopOrderCreatedHandler:
    """
    Processes ``shop.order_created`` events.

    Sends a WhatsApp or SMS message to the customer with:
    - Order confirmation
    - Public tracking URL
    - Secret code to confirm receipt
    """

    event_type: str = "shop.order_created"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        settings = get_settings()

        order_id: str = data.get("order_id") or ""
        order_number: str = data.get("order_number") or ""
        customer_id: str = data.get("customer_id") or ""
        customer_name: str = data.get("customer_name") or ""
        customer_phone: str = data.get("customer_phone") or ""
        total_amount: str = data.get("total_amount") or "0.000"
        currency: str = data.get("currency") or "KWD"
        tracking_code: str = data.get("tracking_code") or ""
        correlation_id: str = envelope.event_id_str or ""

        if not order_number:
            logger.warning(
                "shop_order_created_missing_order_number",
                event_id=correlation_id,
            )
            return

        if not customer_phone and not customer_id:
            logger.warning(
                "shop_order_created_no_contact",
                order_id=order_id,
                order_number=order_number,
            )
            return

        # Build the public tracking URL
        base_url = settings.API_GATEWAY_BASE_URL.rstrip("/")
        tracking_url = f"{base_url}/booknpay/api/v1/track/{order_number}/"

        en_body = _whatsapp_message(
            order_number=order_number,
            total_amount=total_amount,
            currency=currency,
            tracking_url=tracking_url,
            tracking_code=tracking_code,
            customer_name=customer_name or "Valued Customer",
        )
        ar_body = _whatsapp_message_ar(
            order_number=order_number,
            total_amount=total_amount,
            currency=currency,
            tracking_url=tracking_url,
            tracking_code=tracking_code,
        )
        sms_body = _sms_message(
            order_number=order_number,
            total_amount=total_amount,
            currency=currency,
            tracking_url=tracking_url,
            tracking_code=tracking_code,
        )

        template_context = {
            "order_id": order_id,
            "order_number": order_number,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "total_amount": total_amount,
            "currency": currency,
            "tracking_url": tracking_url,
            "tracking_code": tracking_code,
            "message_body": en_body,
            "message_body_ar": ar_body,
        }

        service = NotificationService(ctx)
        whatsapp_sent = False

        # ── 1. WhatsApp (preferred) ───────────────────────────────────────
        wa_recipient = ChannelResolver.resolve_whatsapp_recipient(data)
        if wa_recipient:
            req_wa = NotificationRequest(
                event_id=correlation_id,
                recipient=wa_recipient,
                template_name="shop/order_created_whatsapp",
                template_context={
                    **template_context,
                    "customer_name": wa_recipient.name or customer_name,
                    "message_body": en_body,
                },
                customer_id=customer_id or None,
                correlation_id=correlation_id,
            )
            try:
                await service.send(req_wa)
                whatsapp_sent = True
                logger.info(
                    "shop_order_created_whatsapp_sent",
                    order_number=order_number,
                    customer_id=customer_id,
                )
            except Exception as exc:
                logger.warning(
                    "shop_order_created_whatsapp_failed",
                    order_number=order_number,
                    error=str(exc),
                )

        # ── 2. SMS fallback (if WhatsApp not sent) ────────────────────────
        if not whatsapp_sent:
            sms_recipient = ChannelResolver.resolve_sms_recipient(data)
            if sms_recipient:
                req_sms = NotificationRequest(
                    event_id=correlation_id,
                    recipient=sms_recipient,
                    template_name="shop/order_created_sms",
                    template_context={
                        **template_context,
                        "customer_name": sms_recipient.name or customer_name,
                        "message_body": sms_body,
                    },
                    customer_id=customer_id or None,
                    correlation_id=correlation_id,
                )
                try:
                    await service.send(req_sms)
                    logger.info(
                        "shop_order_created_sms_sent",
                        order_number=order_number,
                        customer_id=customer_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "shop_order_created_sms_failed",
                        order_number=order_number,
                        error=str(exc),
                    )

        if not whatsapp_sent and not (wa_recipient or ChannelResolver.resolve_sms_recipient(data)):
            logger.warning(
                "shop_order_created_no_notification_sent",
                order_number=order_number,
                reason="no valid phone/whatsapp contact found",
            )
