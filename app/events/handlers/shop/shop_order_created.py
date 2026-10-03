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
from app.invoicing.triggers import trigger_shop_order_invoice
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

        # ── Extract payment transaction metadata & identifiers ────────────────
        payment_data: dict = data.get("payment_data") if isinstance(data.get("payment_data"), dict) else {}
        inner_data: dict = (
            payment_data.get("data") if isinstance(payment_data.get("data"), dict)
            else payment_data.get("Data") if isinstance(payment_data.get("Data"), dict)
            else {}
        )
        txns: list = (
            inner_data.get("InvoiceTransactions")
            or payment_data.get("InvoiceTransactions")
            or payment_data.get("invoice_transactions")
            or []
        )
        first_txn: dict = txns[0] if isinstance(txns, list) and len(txns) > 0 and isinstance(txns[0], dict) else {}

        reference_id: str | None = (
            str(data.get("reference_id")).strip() if data.get("reference_id") is not None and str(data.get("reference_id")).strip()
            else str(first_txn.get("ReferenceId")).strip() if first_txn.get("ReferenceId") is not None and str(first_txn.get("ReferenceId")).strip()
            else str(first_txn.get("reference_id")).strip() if first_txn.get("reference_id") is not None and str(first_txn.get("reference_id")).strip()
            else str(payment_data.get("referenceId")).strip() if payment_data.get("referenceId") is not None and str(payment_data.get("referenceId")).strip()
            else str(payment_data.get("reference_id")).strip() if payment_data.get("reference_id") is not None and str(payment_data.get("reference_id")).strip()
            else None
        )

        track_id: str | None = (
            str(data.get("track_id")).strip() if data.get("track_id") is not None and str(data.get("track_id")).strip()
            else str(first_txn.get("TrackId")).strip() if first_txn.get("TrackId") is not None and str(first_txn.get("TrackId")).strip()
            else str(first_txn.get("track_id")).strip() if first_txn.get("track_id") is not None and str(first_txn.get("track_id")).strip()
            else str(payment_data.get("trackId")).strip() if payment_data.get("trackId") is not None and str(payment_data.get("trackId")).strip()
            else str(payment_data.get("track_id")).strip() if payment_data.get("track_id") is not None and str(payment_data.get("track_id")).strip()
            else str(payment_data.get("trace_id")).strip() if payment_data.get("trace_id") is not None and str(payment_data.get("trace_id")).strip()
            else None
        )

        country: str | None = (
            str(data.get("country")).strip() if data.get("country") is not None and str(data.get("country")).strip()
            else str(first_txn.get("Country")).strip() if first_txn.get("Country") is not None and str(first_txn.get("Country")).strip()
            else str(first_txn.get("country")).strip() if first_txn.get("country") is not None and str(first_txn.get("country")).strip()
            else str(payment_data.get("country")).strip() if payment_data.get("country") is not None and str(payment_data.get("country")).strip()
            else str(inner_data.get("Country")).strip() if inner_data.get("Country") is not None and str(inner_data.get("Country")).strip()
            else None
        )

        payment_id: str | None = (
            str(data.get("payment_id")).strip() if data.get("payment_id") is not None and str(data.get("payment_id")).strip()
            else str(first_txn.get("PaymentId")).strip() if first_txn.get("PaymentId") is not None and str(first_txn.get("PaymentId")).strip()
            else str(first_txn.get("payment_id")).strip() if first_txn.get("payment_id") is not None and str(first_txn.get("payment_id")).strip()
            else str(payment_data.get("paymentId")).strip() if payment_data.get("paymentId") is not None and str(payment_data.get("paymentId")).strip()
            else str(payment_data.get("payment_id")).strip() if payment_data.get("payment_id") is not None and str(payment_data.get("payment_id")).strip()
            else str(inner_data.get("PaymentId")).strip() if inner_data.get("PaymentId") is not None and str(inner_data.get("PaymentId")).strip()
            else None
        )

        transaction_id: str | None = (
            str(data.get("transaction_id")).strip() if data.get("transaction_id") is not None and str(data.get("transaction_id")).strip()
            else str(first_txn.get("TransactionId")).strip() if first_txn.get("TransactionId") is not None and str(first_txn.get("TransactionId")).strip()
            else str(first_txn.get("transaction_id")).strip() if first_txn.get("transaction_id") is not None and str(first_txn.get("transaction_id")).strip()
            else str(payment_data.get("transactionId")).strip() if payment_data.get("transactionId") is not None and str(payment_data.get("transactionId")).strip()
            else str(payment_data.get("transaction_id")).strip() if payment_data.get("transaction_id") is not None and str(payment_data.get("transaction_id")).strip()
            else payment_id
        )

        invoice_id: str | None = (
            str(data.get("invoice_id")).strip() if data.get("invoice_id") is not None and str(data.get("invoice_id")).strip()
            else str(payment_data.get("invoiceId")).strip() if payment_data.get("invoiceId") is not None and str(payment_data.get("invoiceId")).strip()
            else str(payment_data.get("invoice_id")).strip() if payment_data.get("invoice_id") is not None and str(payment_data.get("invoice_id")).strip()
            else str(inner_data.get("InvoiceId")).strip() if inner_data.get("InvoiceId") is not None and str(inner_data.get("InvoiceId")).strip()
            else str(inner_data.get("invoice_id")).strip() if inner_data.get("invoice_id") is not None and str(inner_data.get("invoice_id")).strip()
            else None
        )

        transaction_date: str | None = (
            str(data.get("transaction_date")).strip() if data.get("transaction_date") is not None and str(data.get("transaction_date")).strip()
            else str(first_txn.get("TransactionDate")).strip() if first_txn.get("TransactionDate") is not None and str(first_txn.get("TransactionDate")).strip()
            else str(first_txn.get("transaction_date")).strip() if first_txn.get("transaction_date") is not None and str(first_txn.get("transaction_date")).strip()
            else str(payment_data.get("transactionDate")).strip() if payment_data.get("transactionDate") is not None and str(payment_data.get("transactionDate")).strip()
            else str(payment_data.get("transaction_date")).strip() if payment_data.get("transaction_date") is not None and str(payment_data.get("transaction_date")).strip()
            else str(inner_data.get("CreatedDate")).strip() if inner_data.get("CreatedDate") is not None and str(inner_data.get("CreatedDate")).strip()
            else None
        )

        payment_gateway: str | None = (
            str(data.get("payment_gateway")).strip() if data.get("payment_gateway") is not None and str(data.get("payment_gateway")).strip()
            else str(first_txn.get("PaymentGateway")).strip() if first_txn.get("PaymentGateway") is not None and str(first_txn.get("PaymentGateway")).strip()
            else str(first_txn.get("payment_gateway")).strip() if first_txn.get("payment_gateway") is not None and str(first_txn.get("payment_gateway")).strip()
            else str(payment_data.get("paymentGateway")).strip() if payment_data.get("paymentGateway") is not None and str(payment_data.get("paymentGateway")).strip()
            else payment_method
            if payment_method and payment_method.lower() in ("knet", "tap")
            else None
        )

        transaction_status: str | None = (
            str(data.get("transaction_status")).strip() if data.get("transaction_status") is not None and str(data.get("transaction_status")).strip()
            else str(first_txn.get("TransactionStatus")).strip() if first_txn.get("TransactionStatus") is not None and str(first_txn.get("TransactionStatus")).strip()
            else str(first_txn.get("transaction_status")).strip() if first_txn.get("transaction_status") is not None and str(first_txn.get("transaction_status")).strip()
            else str(payment_data.get("status")).strip() if payment_data.get("status") is not None and str(payment_data.get("status")).strip()
            else str(inner_data.get("InvoiceStatus")).strip() if inner_data.get("InvoiceStatus") is not None and str(inner_data.get("InvoiceStatus")).strip()
            else None
        )

        payment_url: str | None = (
            str(data.get("payment_url")).strip() if data.get("payment_url") is not None and str(data.get("payment_url")).strip()
            else str(payment_data.get("paymentUrl")).strip() if payment_data.get("paymentUrl") is not None and str(payment_data.get("paymentUrl")).strip()
            else str(payment_data.get("payment_url")).strip() if payment_data.get("payment_url") is not None and str(payment_data.get("payment_url")).strip()
            else str(inner_data.get("PaymentURL")).strip() if inner_data.get("PaymentURL") is not None and str(inner_data.get("PaymentURL")).strip()
            else None
        )

        raw_created_by = (
            data.get("created_by_user")
            or data.get("created_by")
            or data.get("order_requested_by_user")
            or customer_id
            or inner_data.get("UserDefinedField")
            or ""
        )
        created_by: str | None = None
        if raw_created_by:
            try:
                import uuid as _uuid
                created_by = str(_uuid.UUID(str(raw_created_by).strip()))
            except (ValueError, TypeError):
                created_by = str(raw_created_by).strip() or None

        raw_user_data = (
            data.get("created_by_user_data")
            or data.get("order_requested_by_user_data")
            or payment_data.get("created_by_user_data")
        )
        created_by_user_data: dict | None = (
            dict(raw_user_data) if isinstance(raw_user_data, dict) and raw_user_data else None
        )

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
                payment_gateway=payment_gateway,
                reference_id=reference_id,
                track_id=track_id,
                country=country,
                payment_id=payment_id,
                transaction_id=transaction_id,
                invoice_id=invoice_id,
                transaction_date=transaction_date,
                transaction_status=transaction_status,
                created_by=created_by,
                created_by_user=created_by,
                created_by_user_data=created_by_user_data,
                payment_url=payment_url,
                payment_data=payment_data,
                customer_data=customer_data,
                product_order_items=items,
                correlation_id=correlation_id,
            )
            res_data = (payment_response or {}).get("data")
            payment_id_logged = (
                (res_data.get("id") if isinstance(res_data, dict) else None)
                or (payment_response or {}).get("id")
                or payment_id
                or "?"
            )
            logger.info(
                "shop_order_payment_record_created",
                order_id=order_id,
                order_number=order_number,
                payment_id=payment_id_logged,
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

        # ── Invoice: create in ushanr for paid orders (non-blocking) ──────────
        if order_id and str(payment_status).lower() == "success":
            await trigger_shop_order_invoice(data, correlation_id=correlation_id)
