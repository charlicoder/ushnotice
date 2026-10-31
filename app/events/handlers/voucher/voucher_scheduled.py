"""
Handler for `voucher.scheduled` event.
─────────────────────────────────────
Fired when a customer purchases a scheduled gift voucher and payment is confirmed.

On voucher.scheduled, this handler:
  1. Triggers invoice creation (accounting)
  2. Creates payment record in ushbooknpay
  3. Sends short confirmation SMS to the SENDER in Arabic:
     "مرحباً [الاسم]، تم استلام طلبك وجدولة إرسال بطاقة الهدية إلى [المستلم] بتاريخ [تاريخ الجدولة]. شكراً لاختيارك يوش سبا."
  4. Does NOT notify recipient at this stage (recipient is notified when delivery is due).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.handlers.voucher.voucher_active import (
    _build_voucher_context,
    _sender_sms_message_ar,
    _sender_whatsapp_message,
)
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.invoicing.triggers import trigger_voucher_invoice
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationStatus
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)

KUWAIT_TZ = ZoneInfo("Asia/Kuwait")


def _fmt_scheduled_datetime_ar(dt_str: str | None) -> str:
    """Format scheduled delivery datetime into Arabic-friendly string."""
    if not dt_str:
        return ""
    try:
        s = dt_str.strip()
        if s.endswith("Z"):
            s = s[:-1]
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt_kuwait = dt.replace(tzinfo=KUWAIT_TZ)
        elif dt.tzinfo == timezone.utc or str(dt.tzinfo) == "UTC":
            dt_kuwait = dt.replace(tzinfo=None).replace(tzinfo=KUWAIT_TZ)
        else:
            dt_kuwait = dt.astimezone(KUWAIT_TZ)
        return dt_kuwait.strftime("%Y-%m-%d %I:%M %p")
    except Exception:
        return str(dt_str)


def _sender_scheduled_confirmation_sms_ar(vctx: dict, scheduled_at_str: str) -> str:
    """Short and nice Arabic SMS for the sender confirming the scheduled delivery."""
    sender_name = vctx.get("sender_name") or "عميلنا العزيز"
    recipient_name = vctx.get("recipient_name") or "المستلم"
    scheduled_display = _fmt_scheduled_datetime_ar(scheduled_at_str)
    return (
        f"مرحباً {sender_name}،\n"
        f"تم استلام طلبك وجدولة إرسال بطاقة الهدية إلى {recipient_name} بتاريخ {scheduled_display}.\n"
        f"شكراً لاختيارك يوش سبا."
    )


class VoucherScheduledHandler(EventHandler):
    """
    Processes voucher.scheduled events.
    """

    event_type: str = "voucher.scheduled"

    async def handle(self, envelope: EventEnvelope, context: HandlerContext) -> None:
        data = envelope.data
        correlation_id = envelope.correlation_id_str
        voucher_id = str(data.get("id") or "")
        voucher_number = str(data.get("voucher_number") or "")
        scheduled_at_raw = str(data.get("scheduled_delivery_at") or "")

        logger.info(
            "voucher_scheduled_handler_started",
            voucher_id=voucher_id,
            voucher_number=voucher_number,
            scheduled_delivery_at=scheduled_at_raw,
            correlation_id=correlation_id,
        )

        vctx = _build_voucher_context(data)
        vctx["scheduled_delivery_at"] = scheduled_at_raw

        # ── 1. Accounting invoice ─────────────────────────────────────────────
        invoice_result: dict[str, Any] | None = None
        try:
            invoice_result = await trigger_voucher_invoice(
                data=data,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            logger.warning(
                "voucher_scheduled_invoice_trigger_failed",
                voucher_id=voucher_id,
                error=str(exc),
            )

        # ── 2. Create payment record in ushbooknpay ───────────────────────────
        try:
            client = UshBookNPayClient()
            payment_data: dict = data.get("payment_data") or {}
            sender_details: dict = data.get("sender_details") or data.get("sender_data") or {}
            sender_id = str(data.get("sender_id") or "")
            resolved_customer_id = (
                str(data.get("customer_id") or "")
                or sender_id
                or str(data.get("recipient_id") or "")
                or voucher_id
            )
            payment_id = str(data.get("payment_id") or payment_data.get("invoiceId") or payment_data.get("invoice_id") or "")
            invoice_number = (
                (invoice_result.get("invoice_name") if invoice_result else None)
                or data.get("invoice_number")
                or payment_data.get("invoice_number")
                or None
            )

            addons = data.get("addons") or []
            addons_price_total = 0.0
            for addon in addons:
                try:
                    addons_price_total += float(addon.get("price") or 0)
                except Exception:
                    pass

            extra_time = int(data.get("extra_time") or 0)
            price_for_extra_time = float(data.get("price_for_extra_time") or 0.0)

            # Normalise payment_gateway
            _gw_raw = str(payment_data.get("paymentGateway") or payment_data.get("PaymentGateway") or "").upper()
            if "KNET" in _gw_raw or "K-NET" in _gw_raw:
                normalised_gw = "KNET"
            elif "TAP" in _gw_raw:
                normalised_gw = "TAP"
            elif _gw_raw:
                normalised_gw = "Other"
            else:
                normalised_gw = None

            payload = {
                # ── Required fields ────────────────────────────────────────
                "customer_id": resolved_customer_id,
                "total_amount": str(vctx.get("total_amount") or "0"),
                "total_duration": int(vctx.get("total_duration") or 0),
                "currency": vctx.get("currency") or "KWD",
                # ── Associations ───────────────────────────────────────────
                "voucher_id": voucher_id,
                "voucher_number": voucher_number or None,
                "booking_id": None,
                # ── Status & classification ────────────────────────────────
                "status": "success",
                "payment_for": "gift_voucher",
                "payment_provider": str(data.get("payment_provider") or "MyFatoorah"),
                "payment_through": str(data.get("payment_through") or "ushspa"),
                "payment_gateway": normalised_gw,
                "payment_method": str(
                    payment_data.get("paymentMethod")
                    or payment_data.get("PaymentMethod")
                    or "card"
                ),
                "payment_id": payment_id,
                "invoice_number": invoice_number,
                "invoice_value": str(vctx["total_amount"]),
                "service_id": str(data.get("service_id") or "") or None,
                "service_data": data.get("service_data") or None,
                "branch_id": str(data.get("branch_id") or "") or None,
                "branch_data": data.get("branch_data") or None,
                "addons": addons or None,
                "addons_price": addons_price_total,
                "extra_time": extra_time,
                "price_for_extra_time": price_for_extra_time,
                "recipient_id": str(data.get("recipient_id") or "") or None,
                "recipient_phone": data.get("recipient_phone") or (data.get("recipient_data") or {}).get("phone_number") or None,
                "recipient_data": data.get("recipient_data") or None,
                "sender_id": sender_id or None,
                "sender_data": sender_details or None,
                "customer_data": {
                    "name": sender_details.get("name") or "",
                    "mobile": sender_details.get("phone_number") or sender_details.get("mobile") or "",
                    "email": sender_details.get("email") or "",
                },
                "voucher_data": {
                    "voucher_id": voucher_id,
                    "voucher_number": voucher_number,
                    "expire_date": vctx.get("expire_date_raw") or vctx.get("expire_date") or "",
                    "total_amount": vctx["total_amount"],
                    "scheduled_delivery_at": scheduled_at_raw,
                    "delivery_mode": "scheduled",
                },
                "payment_data": payment_data,
            }

            await client._client.post(
                "/api/v1/payments/",
                json=payload,
                correlation_id=correlation_id,
            )
            await client.aclose()
            logger.info("voucher_scheduled_payment_record_created", voucher_id=voucher_id)
        except Exception as exc:
            logger.warning("voucher_scheduled_payment_record_failed", voucher_id=voucher_id, error=str(exc))

        # ── 3. Notify SENDER (Arabic scheduled delivery confirmation) ─────────
        sender_details = data.get("sender_details") or data.get("sender_data") or {}
        sender_phone = sender_details.get("phone_number") or data.get("sender_phone") or ""
        sender_name = vctx["sender_name"]

        if sender_phone:
            try:
                sms_body = _sender_scheduled_confirmation_sms_ar(vctx, scheduled_at_raw)
                sender_payload_sms = {
                    "phone_number": sender_phone,
                    "customer_name": sender_name,
                    "language_preference": "ar",
                }
                sms_recipient = ChannelResolver.resolve_sms_recipient(sender_payload_sms)
                if sms_recipient:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=sms_recipient,
                        template_name="voucher/scheduled_sender_sms",
                        template_context={**vctx, "message_body": sms_body},
                        customer_id=None,
                        booking_id=voucher_id or None,
                        correlation_id=correlation_id,
                    )
                    service = getattr(context, "notification_service", None) or NotificationService(context)
                    await service.send(req)
                    logger.info("voucher_scheduled_sender_sms_sent", voucher_id=voucher_id, phone=sender_phone)
            except Exception as exc:
                logger.warning("voucher_scheduled_sender_sms_failed", voucher_id=voucher_id, error=str(exc))
        else:
            logger.info("voucher_scheduled_sender_no_phone", voucher_id=voucher_id)

