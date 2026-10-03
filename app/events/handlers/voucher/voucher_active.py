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
from app.invoicing.triggers import trigger_voucher_invoice
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationChannel, NotificationStatus
from app.notifications.domain.value_objects import NotificationRequest, Recipient

logger = get_logger(__name__)

# Public gift-card page URL template — {public_token} will be filled at runtime.
_GIFT_CARD_PAGE_URL = "https://ushspa.co/gift/{public_token}"
# App Store link and website shown to recipients who may not be customers yet.
_APP_INSTALL_URL = "https://apps.apple.com/kw/app/ushspa/id6771279814"
_WEBSITE_URL = "https://ushspa.co/"


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
    sender_details: dict = data.get("sender_details") or data.get("sender_data") or {}
    recipient_details: dict = data.get("recipient_details") or data.get("recipient_data") or {}

    service_data: dict = data.get("service_data") or {}
    branch_data: dict = data.get("branch_data") or {}

    expire_raw = data.get("expire_date") or ""
    expire_fmt = _fmt_expire(expire_raw) if expire_raw else ""

    public_token = data.get("public_token") or ""
    gift_card_url = _GIFT_CARD_PAGE_URL.format(public_token=public_token) if public_token else _APP_INSTALL_URL

    gift_from = (data.get("gift_from") or sender_details.get("gift_from") or "").strip()
    sender_name = sender_details.get("name") or data.get("sender_name") or "A generous friend"
    recipient_name = recipient_details.get("name") or data.get("recipient_name") or "Valued Customer"
    service_name = service_data.get("name") or data.get("service_name") or "Spa Experience"
    branch_name = branch_data.get("name") or data.get("branch_name") or "USHSPA"

    sender_language = str(
        data.get("sender_language")
        or sender_details.get("language_preference")
        or sender_details.get("language")
        or "en"
    ).lower()
    recipient_language = str(
        data.get("recipient_language")
        or recipient_details.get("language_preference")
        or recipient_details.get("language")
        or "en"
    ).lower()

    return {
        "voucher_id": str(data.get("id") or ""),
        "voucher_number": str(data.get("voucher_number") or ""),
        "secret_code": str(data.get("secret_code") or ""),
        "public_token": public_token,
        "gift_card_url": gift_card_url,
        "app_install_url": _APP_INSTALL_URL,
        "website_url": _WEBSITE_URL,
        "expire_date": expire_fmt,
        "expire_date_raw": expire_raw,
        "gift_message": data.get("gift_message") or "",
        "gift_from": gift_from,
        "gift_template": data.get("gift_template") or "",
        "total_amount": str(data.get("total_amount") or ""),
        "total_duration": str(data.get("total_duration") or ""),
        "currency": "KWD",
        "service_name": service_name,
        "branch_name": branch_name,
        "sender_name": sender_name,
        "sender_phone": sender_details.get("phone_number") or data.get("sender_phone") or "",
        "sender_language": sender_language,
        "recipient_name": recipient_name,
        "recipient_phone": recipient_details.get("phone_number") or data.get("recipient_phone") or "",
        "recipient_email": recipient_details.get("email") or data.get("recipient_email") or "",
        "recipient_password": str(recipient_details.get("password") or data.get("recipient_password") or ""),
        "recipient_language": recipient_language,
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


def _sender_whatsapp_message_ar(ctx: dict) -> str:
    lines = [
        "🎁 *تم إرسال الهدية بنجاح* — USHSPA",
        "",
        f"عزيزي {ctx['sender_name']}،",
        "",
        f"تم إرسال هديتك إلى *{ctx['recipient_name']}* وهي الآن مفعّلة! 🎉",
        "",
        f"🧴 *الخدمة:* {ctx['service_name']}",
        f"📍 *الفرع:* {ctx['branch_name']}",
    ]
    if ctx["total_amount"]:
        lines.append(f"💳 *القيمة:* {ctx['total_amount']} {ctx['currency']}")
    if ctx["expire_date"]:
        lines.append(f"📅 *تاريخ الانتهاء:* {ctx['expire_date']}")
    if ctx["gift_message"]:
        lines += ["", f"💬 *رسالتك:* _{ctx['gift_message']}_"]
    lines += [
        "",
        "شكراً لمشاركتك تجربة USHSPA!",
        "— فريق USHSPA",
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


def _sender_sms_message_ar(ctx: dict) -> str:
    name_first = (ctx["sender_name"] or "").split()[0] or "عميلنا"
    recipient = ctx["recipient_name"] or "المستلم"
    amount_part = f" ({ctx['total_amount']} {ctx['currency']})" if ctx["total_amount"] else ""
    expire_part = f" تنتهي في: {ctx['expire_date']}." if ctx["expire_date"] else ""
    return (
        f"USHSPA: مرحباً {name_first}، تم إرسال هديتك{amount_part} إلى {recipient}.{expire_part} "
        f"شكراً لك!"
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
    ]
    if ctx.get("recipient_password"):
        lines += [
            "",
            f"🔑 *Your Login Password:* {ctx['recipient_password']}",
        ]
    lines += [
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
    recipient_name = ctx.get("recipient_name") or "Customer"
    if recipient_name == "Valued Customer":
        recipient_name = "Customer"
    sender = ctx.get("gift_from") or ctx.get("sender_name") or "Someone special"
    message = ctx.get("gift_message") or "Happy birthday"
    if not message.endswith("🎈 🙏🏻") and not message.endswith("\U0001f388 \U0001f64f\U0001f3fb"):
        message_line = f"{message} 🎈 🙏🏻"
    else:
        message_line = message
    url = ctx.get("gift_card_url") or ""

    lines = [
        "USHSPA:",
        "",
        f"Hi {recipient_name}, you received a gift from {sender} !",
        "Message say:",
        message_line,
        "",
        "View the link below to receive your gift",
        "",
        url,
    ]
    if ctx.get("recipient_password"):
        lines += [
            "",
            f"Your temporary password: {ctx['recipient_password']}",
        ]
    return "\n".join(lines)


def _recipient_whatsapp_message_ar(ctx: dict) -> str:
    lines = [
        "🎁 *لقد استلمت هدية!* — USHSPA",
        "",
        f"عزيزي {ctx['recipient_name']}،",
        "",
        f"أرسل لك *{ctx['sender_name']}* هدية سبا حصرية! 💆‍♀️✨",
        "",
        f"🧴 *الخدمة:* {ctx['service_name']}",
        f"📍 *الفرع:* {ctx['branch_name']}",
    ]
    if ctx["total_amount"]:
        lines.append(f"💳 *قيمة الهدية:* {ctx['total_amount']} {ctx['currency']}")
    if ctx["expire_date"]:
        lines.append(f"📅 *صالحة حتى:* {ctx['expire_date']}")
    if ctx["gift_message"]:
        lines += ["", f"💬 *رسالة من {ctx['sender_name']}:* _{ctx['gift_message']}_"]
    lines += [
        "",
        "🔐 *رمزك السري:* " + (ctx["secret_code"] or "راجع بريدك الإلكتروني"),
    ]
    if ctx.get("recipient_password"):
        lines += [
            "",
            f"🔑 *كلمة المرور الخاصة بك:* {ctx['recipient_password']}",
        ]
    lines += [
        "",
        f"🌐 *عرض بطاقة الهدية:* {ctx['gift_card_url']}",
        "",
        "📱 للاستفادة من الهدية، حمّل تطبيق USHSPA وسجّل الدخول بهذا الرقم:",
        f"{ctx['app_install_url']}",
        "",
        "— فريق USHSPA",
    ]
    return "\n".join(lines)


def _recipient_sms_message_ar(ctx: dict) -> str:
    recipient_name = ctx.get("recipient_name") or "عميلنا"
    if recipient_name == "Valued Customer":
        recipient_name = "عميلنا"
    sender = ctx.get("gift_from") or ctx.get("sender_name") or "شخص مميز"
    message = ctx.get("gift_message") or "عيد ميلاد سعيد"
    if not message.endswith("🎈 🙏🏻") and not message.endswith("\U0001f388 \U0001f64f\U0001f3fb"):
        message_line = f"{message} 🎈 🙏🏻"
    else:
        message_line = message
    url = ctx.get("gift_card_url") or ""

    lines = [
        "USHSPA:",
        "",
        f"مرحباً {recipient_name}، لقد استلمت هدية من {sender}!",
        "نص الرسالة:",
        message_line,
        "",
        "اضغط على الرابط أدناه لاستلام هديتك",
        "",
        url,
    ]
    if ctx.get("recipient_password"):
        lines += [
            "",
            f"كلمة المرور المؤقتة: {ctx['recipient_password']}",
        ]
    return "\n".join(lines)


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

        # ── 3. Notify RECIPIENT ──────────────────────────────────────────────
        recipient_details: dict = data.get("recipient_details") or {}
        recipient_phone = (
            recipient_details.get("phone_number")
            or data.get("recipient_phone")
            or ""
        )
        recipient_email = recipient_details.get("email") or data.get("recipient_email") or ""
        recipient_name = vctx["recipient_name"]

        # Normalise phone digits to detect self-gifting (purchasing for own phone)
        sender_digits = "".join(c for c in sender_phone if c.isdigit())
        recipient_digits = "".join(c for c in recipient_phone if c.isdigit())
        is_self_gift = bool(sender_digits and recipient_digits and sender_digits == recipient_digits)

        if sender_phone:
            if is_self_gift:
                logger.info(
                    "voucher_active_sender_skipped_self_gift",
                    voucher_id=voucher_id,
                    phone=sender_phone,
                )
            else:
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
            sender_details: dict = data.get("sender_details") or data.get("sender_data") or {}

            # ── Derive gateway-level fields from payment_data ──────────────
            payment_id: str = str(data.get("payment_id") or payment_data.get("invoiceId") or payment_data.get("invoice_id") or "")
            invoice_id: str = str(payment_data.get("invoice_id") or payment_data.get("invoiceId") or payment_data.get("InvoiceId") or "")
            invoice_value: Any = (
                vctx["total_amount"]
                or payment_data.get("invoiceValue")
                or payment_data.get("InvoiceValue")
                or ""
            )
            payment_url: str = str(data.get("payment_url") or "")

            # sender_id is the registered customer UUID who purchased the voucher
            sender_id: str = str(data.get("sender_id") or "")

            # ── Normalise payment_gateway to spec values ───────────────────
            _gw_raw = str(payment_data.get("paymentGateway") or payment_data.get("PaymentGateway") or "").upper()
            if "KNET" in _gw_raw or "K-NET" in _gw_raw:
                normalised_gw = "KNET"
            elif "TAP" in _gw_raw:
                normalised_gw = "TAP"
            elif _gw_raw:
                normalised_gw = "Other"
            else:
                normalised_gw = None

            # ── Service / location fields ──────────────────────────────────
            service_id: str | None = str(data.get("service_id") or "") or None
            service_data: dict | None = data.get("service_data") or None
            branch_id: str | None = str(data.get("branch_id") or "") or None
            branch_data: dict | None = data.get("branch_data") or None
            service_arrangement_id: str | None = str(data.get("service_arrangement_id") or "") or None
            service_arrangement_data: dict | None = data.get("service_arrangement_data") or None

            # ── Addons & pricing breakdown ─────────────────────────────────
            addons: list = data.get("addons") or []
            # Sum the "price" field from each addon dict (gracefully handle missing/non-numeric)
            addons_price_total: float = 0.0
            for addon in addons:
                try:
                    addons_price_total += float(addon.get("price") or 0)
                except (TypeError, ValueError):
                    pass
            addons_price: str | None = (
                f"{addons_price_total:.3f}" if addons_price_total > 0 else None
            )

            extra_time: int | None = data.get("extra_time") or None
            price_for_extra_time: str | None = data.get("price_for_extra_time") or None

            # ── Recipient fields ───────────────────────────────────────────
            recipient_id: str | None = str(data.get("recipient_id") or "") or None
            recipient_phone: str | None = (
                data.get("recipient_phone")
                or (data.get("recipient_data") or {}).get("phone_number")
                or vctx.get("recipient_phone")
                or None
            )
            recipient_data: dict | None = data.get("recipient_data") or None

            # ── Creator ────────────────────────────────────────────────────
            raw_created_by = (
                data.get("created_by_user")
                or data.get("created_by")
                or sender_id
                or ""
            )
            created_by_user: str | None = str(raw_created_by).strip() if raw_created_by and str(raw_created_by).strip() else None

            raw_creator_data = (
                data.get("created_by_user_data")
                or data.get("user_data")
            )
            if isinstance(raw_creator_data, dict) and raw_creator_data:
                created_by_user_data: dict[str, Any] = dict(raw_creator_data)
                if created_by_user and not created_by_user_data.get("id"):
                    created_by_user_data["id"] = created_by_user
            else:
                created_by_user_data = {
                    "id": created_by_user or sender_id or "",
                    "name": sender_details.get("name") or "",
                    "phone": sender_details.get("phone_number") or sender_details.get("mobile") or "",
                    "email": sender_details.get("email") or "",
                    "role": "customer" if (created_by_user == sender_id or not created_by_user) else "user",
                }

            built_payment_data: dict[str, Any] = dict(payment_data) if isinstance(payment_data, dict) else {}
            for k, v in [
                ("payment_id", payment_id),
                ("invoice_id", invoice_id),
                ("invoice_value", invoice_value),
                ("payment_url", payment_url),
                ("payment_gateway", normalised_gw),
                ("invoice_reference", payment_data.get("invoiceReference") or payment_data.get("InvoiceReference") or payment_data.get("invoice_reference")),
                ("customer_reference", payment_data.get("customerReference") or payment_data.get("CustomerReference") or payment_data.get("customer_reference")),
            ]:
                if v is not None and k not in built_payment_data:
                    built_payment_data[k] = v

            payload: dict[str, Any] = {
                # ── Required fields ────────────────────────────────────────
                "customer_id": sender_id or None,
                "total_amount": str(vctx["total_amount"] or invoice_value or "0"),
                "total_duration": int(vctx.get("total_duration") or 0),
                "currency": vctx["currency"],
                # ── Associations ───────────────────────────────────────────
                "voucher_id": voucher_id,
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
                # ── Invoice & transaction identifiers ──────────────────────
                "payment_id": payment_id,
                "transaction_id": str(
                    payment_data.get("transactionId")
                    or payment_data.get("TransactionId")
                    or payment_id
                ),
                "track_id": str(
                    payment_data.get("trace_id")      # snake_case (actual format)
                    or payment_data.get("trackId")    # camelCase (MyFatoorah)
                    or payment_data.get("TrackId")
                    or ""
                ) or None,
                "invoice_id": invoice_id or None,
                "reference_id": str(
                    payment_data.get("reference_id")  # snake_case (actual format)
                    or payment_data.get("referenceId")
                    or payment_data.get("ReferenceId")
                    or ""
                ) or None,
                "transaction_date": str(
                    payment_data.get("transaction_date")   # snake_case (actual format)
                    or payment_data.get("transactionDate")
                    or payment_data.get("TransactionDate")
                    or ""
                ) or None,
                "transaction_status": (
                    "success"
                    if str(payment_data.get("status", "")).lower() in ("paid", "success")
                    else str(payment_data.get("status") or "") or None
                ),
                "invoice_value": str(invoice_value),
                "payment_url": payment_url,
                # ── Service & location ─────────────────────────────────────
                "service_id": service_id,
                "service_data": service_data,
                "branch_id": branch_id,
                "branch_data": branch_data,
                "service_arrangement_id": service_arrangement_id,
                "service_arrangement_data": service_arrangement_data,
                # ── Pricing breakdown ──────────────────────────────────────
                "addons": addons or None,
                "addons_price": addons_price,
                "extra_time": extra_time,
                "price_for_extra_time": price_for_extra_time,
                # ── Recipient ──────────────────────────────────────────────
                "recipient_id": recipient_id,
                "recipient_phone": recipient_phone,
                "recipient_data": recipient_data,
                # ── Sender as customer snapshot ────────────────────────────
                "sender_id": sender_id or None,
                "sender_data": sender_details or None,
                "customer_data": {
                    "name": sender_details.get("name") or "",
                    "mobile": (
                        sender_details.get("phone_number")
                        or sender_details.get("mobile")
                        or ""
                    ),
                    "email": sender_details.get("email") or "",
                },
                # ── Audit / Creator ────────────────────────────────────────
                "created_by": created_by_user,
                "created_by_user": created_by_user,
                "created_by_user_data": created_by_user_data,
                # ── Voucher data snapshot ──────────────────────────────────
                "voucher_data": {
                    "voucher_id": voucher_id,
                    "service_id": service_id or "",
                    "service_name": vctx["service_name"],
                    "branch_id": branch_id or "",
                    "branch_name": vctx["branch_name"],
                    "expire_date": vctx["expire_date_raw"],
                    "total_amount": vctx["total_amount"],
                    "total_duration": vctx["total_duration"],
                    "recipient_phone": recipient_phone or vctx.get("recipient_phone") or "",
                    "addons_price": addons_price,
                    "price_for_extra_time": price_for_extra_time,
                },
                # ── Raw gateway data → payment_data JSONB ──────────────────
                "payment_data": built_payment_data,
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
                total_amount=vctx["total_amount"],
                addons_price=addons_price,
                price_for_extra_time=price_for_extra_time,
                service_id=service_id,
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
        sender_lang = vctx.get("sender_language") or "en"
        whatsapp_sent = False

        # WhatsApp
        try:
            if sender_lang == "ar":
                wa_body = _sender_whatsapp_message_ar(vctx)
            else:
                wa_body = _sender_whatsapp_message(vctx)
            sender_payload = {
                "phone_number": sender_phone,
                "customer_name": sender_name,
                "language_preference": sender_lang,
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
                notification = await service.send(req)
                status_val = getattr(notification, "status", None)
                if status_val in (
                    NotificationStatus.SENT,
                    NotificationStatus.DELIVERED,
                    NotificationStatus.SENT.value,
                    NotificationStatus.DELIVERED.value,
                ) or (notification is not None and not isinstance(status_val, (str, NotificationStatus))):
                    whatsapp_sent = True
                logger.info(
                    "voucher_active_sender_whatsapp_sent",
                    voucher_id=voucher_id,
                    status=str(status_val),
                )
        except Exception as exc:
            logger.warning("voucher_active_sender_whatsapp_failed", voucher_id=voucher_id, error=str(exc))

        # SMS fallback (only sent if WhatsApp was NOT sent)
        if not whatsapp_sent:
            try:
                if sender_lang == "ar":
                    sms_body = _sender_sms_message_ar(vctx)
                else:
                    sms_body = _sender_sms_message(vctx)
                sender_payload_sms = {
                    "phone_number": sender_phone,
                    "customer_name": sender_name,
                    "language_preference": sender_lang,
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
                    "language_preference": sender_lang,
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
        recipient_lang = vctx.get("recipient_language") or "en"
        whatsapp_sent = False

        # WhatsApp
        if recipient_phone:
            try:
                if recipient_lang == "ar":
                    wa_body = _recipient_whatsapp_message_ar(vctx)
                else:
                    wa_body = _recipient_whatsapp_message(vctx)
                recipient_payload = {
                    "phone_number": recipient_phone,
                    "customer_name": recipient_name,
                    "language_preference": recipient_lang,
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
                    notification = await service.send(req)
                    status_val = getattr(notification, "status", None)
                    if status_val in (
                        NotificationStatus.SENT,
                        NotificationStatus.DELIVERED,
                        NotificationStatus.SENT.value,
                        NotificationStatus.DELIVERED.value,
                    ) or (notification is not None and not isinstance(status_val, (str, NotificationStatus))):
                        whatsapp_sent = True
                    logger.info(
                        "voucher_active_recipient_whatsapp_sent",
                        voucher_id=voucher_id,
                        status=str(status_val),
                    )
            except Exception as exc:
                logger.warning("voucher_active_recipient_whatsapp_failed", voucher_id=voucher_id, error=str(exc))

            # SMS fallback (only sent if WhatsApp was NOT sent)
            if not whatsapp_sent:
                try:
                    if recipient_lang == "ar":
                        sms_body = _recipient_sms_message_ar(vctx)
                    else:
                        sms_body = _recipient_sms_message(vctx)
                    recipient_payload_sms = {
                        "phone_number": recipient_phone,
                        "customer_name": recipient_name,
                        "language_preference": recipient_lang,
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

            # Record in Redis that this recipient was notified (with TTL 24h)
            # This prevents duplicate welcome notifications if customer.new_created
            # is processed concurrently or subsequently.
            try:
                from app.core.redis import get_redis
                redis_client = get_redis()
                norm_phone = "".join(c for c in recipient_phone if c.isdigit())
                if norm_phone:
                    await redis_client.setex(f"voucher_recipient_notified:{norm_phone}", 86400, "1")
                    await redis_client.delete(f"voucher_recipient_pending:{norm_phone}")
            except Exception as exc:
                logger.debug("voucher_recipient_redis_set_failed", error=str(exc))

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

        # ── Invoice: create in ushanr (non-blocking) ──────────────────────────
        if voucher_id:
            await trigger_voucher_invoice(envelope.data, correlation_id=correlation_id)
