"""
Handler for `voucher.active` event.

On a voucher.active event this handler:
  1. Creates a payment record in ushbooknpay using voucher_id as the payment ID.
  2. Sends notifications to the SENDER:
       • WhatsApp/SMS — "Your gift has been sent to [recipient name]."
       • Email         — same information, with gift details.
  3. Sends notifications to the RECIPIENT:
       • WhatsApp/SMS — "You received a gift from [sender], expires [date], follow link or install app."
       • Email         — same information with full gift card details.

All side-effects are best-effort — failures are logged but never block event acknowledgement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationChannel
from app.notifications.domain.value_objects import NotificationRequest, Recipient

logger = get_logger(__name__)

# Public gift-card page URL template — {public_token} will be filled at runtime.
_GIFT_CARD_PAGE_URL = "https://gift.ushspa.com/{public_token}"
# App download / registration page shown to recipients who may not be customers yet.
_APP_INSTALL_URL = "https://app.ushspa.com/register"


# ── Context helpers ────────────────────────────────────────────────────────────

def _fmt_expire(expire_date: str) -> str:
    """Format an ISO expire_date string as 'DD MMM YYYY', gracefully."""
    try:
        dt = datetime.fromisoformat(expire_date.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return expire_date


def _build_voucher_context(data: dict) -> dict:
    """Extract and normalise all gift-voucher fields for template rendering."""
    sender_details: dict = data.get("sender_details") or {}
    recipient_details: dict = data.get("recipient_details") or {}
    service_data: dict = data.get("service_data") or {}
    branch_data: dict = data.get("branch_data") or {}

    expire_raw = data.get("expire_date") or ""
    expire_fmt = _fmt_expire(expire_raw) if expire_raw else ""

    public_token = data.get("public_token") or ""
    gift_card_url = _GIFT_CARD_PAGE_URL.format(public_token=public_token) if public_token else _APP_INSTALL_URL

    sender_name = sender_details.get("name") or data.get("sender_name") or "A generous friend"
    recipient_name = recipient_details.get("name") or data.get("recipient_name") or "Valued Customer"
    service_name = service_data.get("name") or data.get("service_name") or "Spa Experience"
    branch_name = branch_data.get("name") or data.get("branch_name") or "USHSPA"

    return {
        "voucher_id": str(data.get("id") or ""),
        "secret_code": str(data.get("secret_code") or ""),
        "public_token": public_token,
        "gift_card_url": gift_card_url,
        "app_install_url": _APP_INSTALL_URL,
        "expire_date": expire_fmt,
        "expire_date_raw": expire_raw,
        "gift_message": data.get("gift_message") or "",
        "gift_template": data.get("gift_template") or "",
        "total_amount": str(data.get("total_amount") or ""),
        "total_duration": str(data.get("total_duration") or ""),
        "currency": "KWD",
        "service_name": service_name,
        "branch_name": branch_name,
        "sender_name": sender_name,
        "sender_phone": sender_details.get("phone_number") or data.get("sender_phone") or "",
        "recipient_name": recipient_name,
        "recipient_phone": recipient_details.get("phone_number") or data.get("recipient_phone") or "",
        "recipient_email": recipient_details.get("email") or data.get("recipient_email") or "",
    }


# ── Message composers — SENDER ─────────────────────────────────────────────────

def _sender_whatsapp_message(ctx: dict) -> str:
    lines = [
        "🎁 *Gift Sent Successfully* — USHSPA",
        "",
        f"Dear {ctx['sender_name']},",
        "",
        f"Your gift has been sent to *{ctx['recipient_name']}* and is now active! 🎉",
        "",
        f"🧴 *Service:* {ctx['service_name']}",
        f"📍 *Branch:* {ctx['branch_name']}",
    ]
    if ctx["total_amount"]:
        lines.append(f"💳 *Value:* {ctx['total_amount']} {ctx['currency']}")
    if ctx["expire_date"]:
        lines.append(f"📅 *Expires:* {ctx['expire_date']}")
    if ctx["gift_message"]:
        lines += ["", f"💬 *Your message:* _{ctx['gift_message']}_"]
    lines += [
        "",
        "Thank you for sharing the USHSPA experience!",
        "— The USHSPA Team",
    ]
    return "\n".join(lines)


def _sender_sms_message(ctx: dict) -> str:
    name_first = (ctx["sender_name"] or "").split()[0] or "Customer"
    recipient = ctx["recipient_name"] or "the recipient"
    amount_part = f" ({ctx['total_amount']} {ctx['currency']})" if ctx["total_amount"] else ""
    expire_part = f" Expires: {ctx['expire_date']}." if ctx["expire_date"] else ""
    return (
        f"USHSPA: Hi {name_first}, your gift{amount_part} has been sent to {recipient}.{expire_part} "
        f"Thank you!"
    )


def _sender_email_subject(ctx: dict) -> str:
    recipient = ctx["recipient_name"] or "Your Recipient"
    return f"🎁 Your USHSPA Gift Has Been Sent to {recipient}"


# ── Message composers — RECIPIENT ─────────────────────────────────────────────

def _recipient_whatsapp_message(ctx: dict) -> str:
    lines = [
        "🎁 *You Have Received a Gift!* — USHSPA",
        "",
        f"Dear {ctx['recipient_name']},",
        "",
        f"*{ctx['sender_name']}* has sent you an exclusive spa gift! 💆‍♀️✨",
        "",
        f"🧴 *Service:* {ctx['service_name']}",
        f"📍 *Branch:* {ctx['branch_name']}",
    ]
    if ctx["total_amount"]:
        lines.append(f"💳 *Gift Value:* {ctx['total_amount']} {ctx['currency']}")
    if ctx["expire_date"]:
        lines.append(f"📅 *Valid Until:* {ctx['expire_date']}")
    if ctx["gift_message"]:
        lines += ["", f"💬 *Message from {ctx['sender_name']}:* _{ctx['gift_message']}_"]
    lines += [
        "",
        "🔐 *Your Secret Code:* " + (ctx["secret_code"] or "See your email"),
        "",
        f"🌐 *View Your Gift Card:* {ctx['gift_card_url']}",
        "",
        "📱 To redeem, install the USHSPA app and register with this phone number:",
        f"{ctx['app_install_url']}",
        "",
        "— The USHSPA Team",
    ]
    return "\n".join(lines)


def _recipient_sms_message(ctx: dict) -> str:
    name_first = (ctx["recipient_name"] or "").split()[0] or "Customer"
    sender = ctx["sender_name"] or "Someone special"
    expire_part = f" Valid until {ctx['expire_date']}." if ctx["expire_date"] else ""
    code_part = f" Code: {ctx['secret_code']}." if ctx["secret_code"] else ""
    return (
        f"USHSPA: Hi {name_first}, you received a gift from {sender}!{expire_part}{code_part} "
        f"View: {ctx['gift_card_url']}"
    )


def _recipient_email_subject(ctx: dict) -> str:
    sender = ctx["sender_name"] or "Someone Special"
    return f"🎁 {sender} Sent You an USHSPA Gift!"


# ── Handler ────────────────────────────────────────────────────────────────────

class VoucherActiveHandler:
    """
    Processes voucher.active events.

    Responsibilities (in order):
      1. Create payment record in ushbooknpay (voucher_id used as the payment identifier).
      2. Notify SENDER via WhatsApp/SMS + Email — "gift sent successfully".
      3. Notify RECIPIENT via WhatsApp/SMS + Email — "you received a gift", include
         secret_code, expiry, gift card URL, and app install link.
    """

    event_type: str = "voucher.active"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)
        correlation_id = envelope.correlation_id_str

        voucher_id = str(data.get("id") or "")
        vctx = _build_voucher_context(data)

        # ── 1. Create payment record in ushbooknpay ──────────────────────────
        await self._create_payment_record(
            data=data,
            voucher_id=voucher_id,
            vctx=vctx,
            correlation_id=correlation_id,
        )

        # ── 2. Notify SENDER ─────────────────────────────────────────────────
        sender_details: dict = data.get("sender_details") or {}
        sender_phone = sender_details.get("phone_number") or data.get("sender_phone") or ""
        sender_name = vctx["sender_name"]

        if sender_phone:
            await self._notify_sender(
                envelope=envelope,
                service=service,
                vctx=vctx,
                sender_phone=sender_phone,
                sender_name=sender_name,
                voucher_id=voucher_id,
                correlation_id=correlation_id,
            )
        else:
            logger.info("voucher_active_sender_no_phone", voucher_id=voucher_id)

        # ── 3. Notify RECIPIENT ──────────────────────────────────────────────
        recipient_details: dict = data.get("recipient_details") or {}
        recipient_phone = (
            recipient_details.get("phone_number")
            or data.get("recipient_phone")
            or ""
        )
        recipient_email = recipient_details.get("email") or data.get("recipient_email") or ""
        recipient_name = vctx["recipient_name"]

        if recipient_phone or recipient_email:
            await self._notify_recipient(
                envelope=envelope,
                service=service,
                vctx=vctx,
                recipient_phone=recipient_phone,
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                voucher_id=voucher_id,
                correlation_id=correlation_id,
            )
        else:
            logger.info("voucher_active_recipient_no_contact", voucher_id=voucher_id)

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _create_payment_record(
        self,
        *,
        data: dict,
        voucher_id: str,
        vctx: dict,
        correlation_id: str | None,
    ) -> None:
        """POST a payment record to ushbooknpay using voucher_id as the record ID."""
        if not voucher_id:
            logger.warning("voucher_active_payment_record_skipped_no_voucher_id")
            return
        try:
            client = UshBookNPayClient()
            payment_data: dict = data.get("payment_data") or {}
            sender_details: dict = data.get("sender_details") or {}

            # Derive gateway-level fields from payment_data (MyFatoorah / KNET response)
            payment_id: str = str(data.get("payment_id") or payment_data.get("invoiceId") or "")
            invoice_id: str = str(payment_data.get("invoiceId") or payment_data.get("InvoiceId") or "")
            invoice_value: Any = (
                vctx["total_amount"]
                or payment_data.get("invoiceValue")
                or payment_data.get("InvoiceValue")
                or ""
            )
            payment_url: str = str(data.get("payment_url") or "")

            # sender_id is the registered customer UUID who purchased this gift voucher
            sender_id: str = str(data.get("sender_id") or "")

            payload: dict[str, Any] = {
                # Use voucher_id as the id of this payment record
                "id": voucher_id,
                # Payment context — no booking, this is a voucher purchase
                "voucher_id": voucher_id,
                "booking_id": None,
                # customer_id is required by the payments endpoint when called with an app token.
                # For gift voucher purchases, the sender is the customer.
                "customer_id": sender_id or None,
                # Financial fields
                "amount": vctx["total_amount"] or invoice_value,
                "currency": vctx["currency"],
                "provider": "myfatoorah",
                "payment_method": str(payment_data.get("paymentMethod") or payment_data.get("PaymentMethod") or "card"),
                "status": "success",
                "is_paid": True,
                # Gateway identifiers
                "payment_id": payment_id,
                "transaction_id": str(payment_data.get("transactionId") or payment_data.get("TransactionId") or payment_id),
                "invoice_id": invoice_id,
                "invoice_value": str(invoice_value),
                "invoice_reference": str(payment_data.get("invoiceReference") or payment_data.get("InvoiceReference") or ""),
                "customer_reference": str(payment_data.get("customerReference") or payment_data.get("CustomerReference") or ""),
                "payment_gateway": str(payment_data.get("paymentGateway") or payment_data.get("PaymentGateway") or "myfatoorah"),
                "payment_url": payment_url,
                # Customer snapshot from sender_details
                "customer_name": sender_details.get("name") or "",
                "customer_mobile": sender_details.get("phone_number") or "",
                "customer_email": sender_details.get("email") or "",
                # Full gateway response snapshot
                "payment_data": payment_data or None,
                # Voucher-level snapshot
                "voucher_data": {
                    "voucher_id": voucher_id,
                    "service_id": str(data.get("service_id") or ""),
                    "service_name": vctx["service_name"],
                    "branch_id": str(data.get("branch_id") or ""),
                    "branch_name": vctx["branch_name"],
                    "expire_date": vctx["expire_date_raw"],
                    "total_amount": vctx["total_amount"],
                    "total_duration": vctx["total_duration"],
                    "recipient_phone": vctx["recipient_phone"],
                },
                "idempotency_key": f"voucher-active-{voucher_id}",
            }

            await client._client.post(
                "/api/v1/payments/",
                json=payload,
                correlation_id=correlation_id,
            )
            await client.aclose()
            logger.info(
                "voucher_active_payment_record_created",
                voucher_id=voucher_id,
                payment_id=payment_id,
                amount=vctx["total_amount"],
            )
        except Exception as exc:
            # Non-blocking — payment record failure must never prevent notifications
            logger.warning(
                "voucher_active_payment_record_failed",
                voucher_id=voucher_id,
                error=str(exc),
            )

    async def _notify_sender(
        self,
        *,
        envelope: EventEnvelope,
        service: NotificationService,
        vctx: dict,
        sender_phone: str,
        sender_name: str,
        voucher_id: str,
        correlation_id: str | None,
    ) -> None:
        """Send WhatsApp/SMS + Email to the gift sender."""
        # WhatsApp
        try:
            wa_body = _sender_whatsapp_message(vctx)
            sender_payload = {
                "phone_number": sender_phone,
                "customer_name": sender_name,
            }
            wa_recipient = ChannelResolver.resolve_whatsapp_recipient(sender_payload)
            if wa_recipient:
                req = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=wa_recipient,
                    template_name="voucher/active_sender_whatsapp",
                    template_context={**vctx, "message_body": wa_body},
                    customer_id=None,
                    booking_id=voucher_id or None,
                    correlation_id=correlation_id,
                )
                await service.send(req)
                logger.info("voucher_active_sender_whatsapp_sent", voucher_id=voucher_id)
        except Exception as exc:
            logger.warning("voucher_active_sender_whatsapp_failed", voucher_id=voucher_id, error=str(exc))

        # SMS fallback (always also send SMS so sender gets a text record)
        try:
            sms_body = _sender_sms_message(vctx)
            sender_payload_sms = {
                "phone_number": sender_phone,
                "customer_name": sender_name,
            }
            sms_recipient = ChannelResolver.resolve_sms_recipient(sender_payload_sms)
            if sms_recipient:
                req = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=sms_recipient,
                    template_name="voucher/active_sender_sms",
                    template_context={**vctx, "message_body": sms_body},
                    customer_id=None,
                    booking_id=voucher_id or None,
                    correlation_id=correlation_id,
                )
                await service.send(req)
                logger.info("voucher_active_sender_sms_sent", voucher_id=voucher_id)
        except Exception as exc:
            logger.warning("voucher_active_sender_sms_failed", voucher_id=voucher_id, error=str(exc))

        # Email — sender may have an email in sender_details
        sender_email = (
            (vctx.get("sender_details") if isinstance(vctx.get("sender_details"), dict) else {})
            .get("email") or ""
        )
        # Also check top-level event data for sender email
        if not sender_email:
            # sender_details is already destructured into vctx; check envelope.data directly
            sender_details_raw = envelope.data.get("sender_details") or {}
            sender_email = sender_details_raw.get("email") or ""

        if sender_email:
            try:
                sender_email_payload = {
                    "email": sender_email,
                    "customer_name": sender_name,
                }
                email_recipient = ChannelResolver.resolve_email_recipient(sender_email_payload)
                if email_recipient:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=email_recipient,
                        template_name="voucher/active_sender_email",
                        template_context={**vctx, "customer_name": sender_name},
                        subject=_sender_email_subject(vctx),
                        customer_id=None,
                        booking_id=voucher_id or None,
                        correlation_id=correlation_id,
                    )
                    await service.send(req)
                    logger.info("voucher_active_sender_email_sent", voucher_id=voucher_id)
            except Exception as exc:
                logger.warning("voucher_active_sender_email_failed", voucher_id=voucher_id, error=str(exc))
        else:
            logger.info("voucher_active_sender_email_skipped_no_email", voucher_id=voucher_id)

    async def _notify_recipient(
        self,
        *,
        envelope: EventEnvelope,
        service: NotificationService,
        vctx: dict,
        recipient_phone: str,
        recipient_email: str,
        recipient_name: str,
        voucher_id: str,
        correlation_id: str | None,
    ) -> None:
        """Send WhatsApp/SMS + Email to the gift recipient with secret_code and link."""
        # WhatsApp
        if recipient_phone:
            try:
                wa_body = _recipient_whatsapp_message(vctx)
                recipient_payload = {
                    "phone_number": recipient_phone,
                    "customer_name": recipient_name,
                }
                wa_recipient = ChannelResolver.resolve_whatsapp_recipient(recipient_payload)
                if wa_recipient:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=wa_recipient,
                        template_name="voucher/active_recipient_whatsapp",
                        template_context={**vctx, "message_body": wa_body},
                        customer_id=None,
                        booking_id=voucher_id or None,
                        correlation_id=correlation_id,
                    )
                    await service.send(req)
                    logger.info("voucher_active_recipient_whatsapp_sent", voucher_id=voucher_id)
            except Exception as exc:
                logger.warning("voucher_active_recipient_whatsapp_failed", voucher_id=voucher_id, error=str(exc))

            # SMS — also send so the recipient has the code as a text message
            try:
                sms_body = _recipient_sms_message(vctx)
                recipient_payload_sms = {
                    "phone_number": recipient_phone,
                    "customer_name": recipient_name,
                }
                sms_recipient = ChannelResolver.resolve_sms_recipient(recipient_payload_sms)
                if sms_recipient:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=sms_recipient,
                        template_name="voucher/active_recipient_sms",
                        template_context={**vctx, "message_body": sms_body},
                        customer_id=None,
                        booking_id=voucher_id or None,
                        correlation_id=correlation_id,
                    )
                    await service.send(req)
                    logger.info("voucher_active_recipient_sms_sent", voucher_id=voucher_id)
            except Exception as exc:
                logger.warning("voucher_active_recipient_sms_failed", voucher_id=voucher_id, error=str(exc))

        # Email — send independently if available
        if recipient_email:
            try:
                recipient_email_payload = {
                    "email": recipient_email,
                    "customer_name": recipient_name,
                }
                email_recipient = ChannelResolver.resolve_email_recipient(recipient_email_payload)
                if email_recipient:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=email_recipient,
                        template_name="voucher/active_recipient_email",
                        template_context={**vctx, "customer_name": recipient_name},
                        subject=_recipient_email_subject(vctx),
                        customer_id=None,
                        booking_id=voucher_id or None,
                        correlation_id=correlation_id,
                    )
                    await service.send(req)
                    logger.info("voucher_active_recipient_email_sent", voucher_id=voucher_id)
            except Exception as exc:
                logger.warning("voucher_active_recipient_email_failed", voucher_id=voucher_id, error=str(exc))
        else:
            logger.info("voucher_active_recipient_email_skipped_no_email", voucher_id=voucher_id)
