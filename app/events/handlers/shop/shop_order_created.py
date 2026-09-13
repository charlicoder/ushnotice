"""
Handler for ``shop.order_created`` event.

Fired by ushbooknpay when a shop order is placed AND payment_status = success.

Actions (in order):
  1. Create a payment record in ushbooknpay at POST /booknpay/api/v1/payments/
     so the finance dashboard can track the transaction.
  2. Send a WhatsApp confirmation (preferred channel).
  3. Fall back to SMS if WhatsApp delivery was not confirmed by the provider.

The message includes:
  - Order number
  - Total amount and currency
  - A public tracking URL: {USH_ORDER_TRACKING_BASE_URL}/{public_token}
  - The 6-digit PIN (tracking_code) required to confirm receipt
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.events.handlers.base import HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationStatus
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


def _build_tracking_url(settings, public_token: str, order_number: str) -> str:
    """
    Build the public order-tracking URL.

    Priority:
      1. {USH_ORDER_TRACKING_BASE_URL}/{public_token}   (preferred — clean public URL)
      2. {API_GATEWAY_BASE_URL}/booknpay/api/v1/track/{public_token or order_number}/
    """
    if settings.USH_ORDER_TRACKING_BASE_URL and public_token:
        base = settings.USH_ORDER_TRACKING_BASE_URL.rstrip("/")
        return f"{base}/{public_token}"
    base = settings.API_GATEWAY_BASE_URL.rstrip("/")
    token_or_number = public_token or order_number
    return f"{base}/booknpay/api/v1/track/{token_or_number}/"


def _whatsapp_message(
    order_number: str,
    total_amount: str,
    currency: str,
    tracking_url: str,
    tracking_code: str,
    customer_name: str,
) -> str:
    """Compose the WhatsApp confirmation text (English)."""
    return (
        f"Hi {customer_name}, your order *{order_number}* for {total_amount} {currency} "
        f"has been received! 🛍️\n\n"
        f"Track your delivery here:\n{tracking_url}\n\n"
        f"When your package arrives, enter PIN *{tracking_code}* to confirm receipt. "
        f"Keep this code safe — it is required to mark your order as received."
    )


def _whatsapp_message_ar(
    order_number: str,
    total_amount: str,
    currency: str,
    tracking_url: str,
    tracking_code: str,
) -> str:
    """Compose the WhatsApp confirmation text (Arabic)."""
    return (
        f"تم استلام طلبك رقم *{order_number}* بقيمة {total_amount} {currency} 🛍️\n\n"
        f"تابع توصيلتك هنا:\n{tracking_url}\n\n"
        f"عند وصول الطرد، أدخل الرمز *{tracking_code}* لتأكيد الاستلام. "
        f"احتفظ بهذا الرمز — فهو مطلوب لتأكيد وصول طلبك."
    )


def _sms_message(
    order_number: str,
    total_amount: str,
    currency: str,
    tracking_url: str,
    tracking_code: str,
) -> str:
    """Compact SMS version (character-limit aware)."""
    return (
        f"Order {order_number} confirmed. {total_amount} {currency}. "
        f"Track: {tracking_url} | PIN: {tracking_code}"
    )


class ShopOrderCreatedHandler:
    """
    Processes ``shop.order_created`` events.

    Flow:
      1. Create payment record in ushbooknpay (fire-and-forget, non-blocking on failure).
      2. Try WhatsApp — check the returned Notification.status to confirm actual delivery.
      3. If WhatsApp was NOT confirmed sent, fall back to SMS.

    Note: NotificationService.send() never raises — it catches all provider errors and
    returns a Notification with status=FAILED. We must inspect status explicitly.
    """

    event_type: str = "shop.order_created"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        settings = get_settings()

        # ── Extract event fields ──────────────────────────────────────────────
        order_id: str = data.get("order_id") or ""
        order_number: str = data.get("order_number") or ""
        customer_id: str = data.get("customer_id") or ""
        customer_name: str = data.get("customer_name") or ""
        customer_phone: str = data.get("customer_phone") or ""
        customer_data: dict = data.get("customer_data") or {}
        total_amount: str = data.get("total_amount") or "0.000"
        currency: str = data.get("currency") or "KWD"
        tracking_code: str = data.get("tracking_code") or ""
        public_token: str = data.get("public_token") or ""
        items: list = data.get("items") or []
        payment_status: str = data.get("payment_status") or "success"
        payment_method: str = data.get("payment_method") or ""
        payment_type: str = data.get("payment_type") or ""
        payment_provider: str = data.get("payment_provider") or ""
        correlation_id: str = envelope.event_id_str or ""

        if not order_number:
            logger.warning(
                "shop_order_created_missing_order_number",
                event_id=correlation_id,
            )
            return

        if not customer_id:
            logger.warning(
                "shop_order_created_missing_customer_id",
                order_id=order_id,
                order_number=order_number,
                event_id=correlation_id,
            )
            return

        # ── 1. Create payment record ──────────────────────────────────────────
        booknpay = UshBookNPayClient()
        try:
            payment_response = await booknpay.create_shop_order_payment(
                order_id=order_id,
                customer_id=customer_id,
                total_amount=total_amount,
                currency=currency,
                payment_status=payment_status,
                payment_method=payment_method,
                payment_type=payment_type,
                payment_provider=payment_provider,
                customer_data=customer_data,
                product_order_items=items,
                correlation_id=correlation_id,
            )
            payment_id = (payment_response or {}).get("id") or "?"
            logger.info(
                "shop_order_payment_record_created",
                order_id=order_id,
                order_number=order_number,
                payment_id=payment_id,
                event_id=correlation_id,
            )
        except Exception as exc:
            # Non-fatal — log and continue with notification
            logger.warning(
                "shop_order_payment_record_failed",
                order_id=order_id,
                order_number=order_number,
                error=str(exc),
                event_id=correlation_id,
            )

        if not customer_phone and not customer_id:
            logger.warning(
                "shop_order_created_no_contact",
                order_id=order_id,
                order_number=order_number,
            )
            return

        # ── Build tracking URL ────────────────────────────────────────────────
        tracking_url = _build_tracking_url(settings, public_token, order_number)

        logger.info(
            "shop_order_created_tracking_url",
            order_number=order_number,
            tracking_url=tracking_url,
            event_id=correlation_id,
        )

        # ── Pre-compose all message bodies ────────────────────────────────────
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

        base_context = {
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
        whatsapp_sent = False  # True only when provider confirms SENT/DELIVERED

        # ── 2. WhatsApp (preferred) ───────────────────────────────────────────
        wa_recipient = ChannelResolver.resolve_whatsapp_recipient(data)
        if wa_recipient:
            req_wa = NotificationRequest(
                event_id=correlation_id,
                recipient=wa_recipient,
                template_name="shop/order_created_whatsapp",
                template_context={
                    **base_context,
                    "customer_name": wa_recipient.name or customer_name,
                    "message_body": en_body,
                },
                customer_id=customer_id or None,
                correlation_id=correlation_id,
            )
            try:
                notification = await service.send(req_wa)
                # service.send() never raises — check actual delivery status
                if notification.status in (
                    NotificationStatus.SENT.value,
                    NotificationStatus.DELIVERED.value,
                ):
                    whatsapp_sent = True
                    logger.info(
                        "shop_order_created_whatsapp_sent",
                        order_number=order_number,
                        customer_id=customer_id,
                        status=notification.status,
                        event_id=correlation_id,
                    )
                else:
                    logger.warning(
                        "shop_order_created_whatsapp_not_delivered",
                        order_number=order_number,
                        status=notification.status,
                        event_id=correlation_id,
                    )
            except Exception as exc:
                logger.warning(
                    "shop_order_created_whatsapp_exception",
                    order_number=order_number,
                    error=str(exc),
                    event_id=correlation_id,
                )

        # ── 3. SMS (fallback — always sent if WhatsApp was not confirmed) ─────
        if not whatsapp_sent:
            sms_recipient = ChannelResolver.resolve_sms_recipient(data)
            if sms_recipient:
                req_sms = NotificationRequest(
                    event_id=correlation_id,
                    recipient=sms_recipient,
                    template_name="shop/order_created_sms",
                    template_context={
                        **base_context,
                        "customer_name": sms_recipient.name or customer_name,
                        "message_body": sms_body,
                    },
                    customer_id=customer_id or None,
                    correlation_id=correlation_id,
                )
                try:
                    notification = await service.send(req_sms)
                    if notification.status in (
                        NotificationStatus.SENT.value,
                        NotificationStatus.DELIVERED.value,
                    ):
                        logger.info(
                            "shop_order_created_sms_sent",
                            order_number=order_number,
                            customer_id=customer_id,
                            status=notification.status,
                            event_id=correlation_id,
                        )
                    else:
                        logger.warning(
                            "shop_order_created_sms_not_delivered",
                            order_number=order_number,
                            status=notification.status,
                            event_id=correlation_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "shop_order_created_sms_exception",
                        order_number=order_number,
                        error=str(exc),
                        event_id=correlation_id,
                    )
            else:
                logger.warning(
                    "shop_order_created_no_sms_recipient",
                    order_number=order_number,
                    reason="No phone number found in event payload",
                    event_id=correlation_id,
                )

        if not whatsapp_sent and not (wa_recipient or ChannelResolver.resolve_sms_recipient(data)):
            logger.warning(
                "shop_order_created_no_notification_sent",
                order_number=order_number,
                reason="No valid phone/WhatsApp contact found in event payload",
                event_id=correlation_id,
            )
