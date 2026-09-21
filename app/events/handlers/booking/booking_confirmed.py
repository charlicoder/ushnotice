"""
Handler for `booking.confirmed` event.

On a confirmed booking this handler:
  1. Creates an appointment-cache record in ushauth (to mark the slot as confirmed).
  2. Creates a payment record in ushbooknpay (from the payment_data in the event).
  3. Creates / increments a loyalty tracker in ushbooknpay for branch bookings.
  4. Sends notifications to the customer:
       • If payment_status == 'pending':
           – WhatsApp/SMS with booking details + a payment link to complete payment.
           – Email with the same information.
       • Otherwise (already paid / no pending payment):
           – WhatsApp if whatsapp_verified, SMS as fallback, Email independently.

Steps 1–3 replace the side-effects that previously ran synchronously inside
ushbooknpay. They are now delegated here via the SQS ``booking.confirmed`` event.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

from app.integrations.ushauth_client import UshAuthClient
from app.integrations.ushbooknpay_client import UshBookNPayClient

logger = get_logger(__name__)

# Demo payment link — replaced by event data when payment_link becomes available in the event
_DEMO_PAYMENT_LINK = "https://pay.ushspa.com/pay"


def _resolve_payment_link(data: dict) -> str:
    """
    Return the payment link for pending-payment notifications.

    Priority:
      1. ``data['payment_link']``          — explicit field from event
      2. ``payment_data['payment_url']``  — gateway-generated URL
      3. ``payment_data['payment_link']`` — alternate key
      4. Demo link                         — placeholder until real link is available
    """
    payment_data: dict = data.get("payment_data") or {}
    return (
        data.get("payment_link")
        or payment_data.get("payment_url")
        or payment_data.get("payment_link")
        or _DEMO_PAYMENT_LINK
    )


def _build_booking_context(data: dict) -> dict:
    """Extract and normalise booking fields for template rendering."""
    payment_data = data.get("payment_data") or {}
    appointment_date = (
        data.get("appointment_date")
        or data.get("date")
        or ""
    )
    appointment_time = (
        data.get("appointment_starttime")
        or data.get("appointment_time")
        or data.get("time")
        or ""
    )
    appointment_end_time = data.get("appointment_endtime") or data.get("end_time") or ""

    booking_reference = (
        data.get("booking_reference")
        or data.get("reference")
        or payment_data.get("invoice_reference")
        or payment_data.get("invoice_id")
        or data.get("booking_id")
        or ""
    )
    total_amount = (
        data.get("total_amount")
        or data.get("amount")
        or payment_data.get("invoice_value")
        or ""
    )

    # Branch details
    branch_data: dict = data.get("branch_data") or {}
    branch_name = data.get("branch_name") or branch_data.get("name") or "USHSPA"
    branch_location = (
        data.get("branch_location")
        or branch_data.get("location")
        or branch_data.get("address")
        or branch_data.get("city")
        or ""
    )
    branch_phone = (
        data.get("branch_phone")
        or branch_data.get("phone")
        or branch_data.get("contact_number")
        or branch_data.get("phone_number")
        or ""
    )

    # Service details
    service_data: dict = data.get("service_data") or {}
    service_name = data.get("service_name") or service_data.get("name") or "Spa Service"
    service_description = (
        data.get("service_description")
        or service_data.get("short_description")
        or service_data.get("description")
        or ""
    )

    # Duration
    duration_minutes = (
        data.get("duration_minutes")
        or data.get("duration")
        or service_data.get("duration_minutes")
        or ""
    )

    return {
        "booking_id": data.get("booking_id") or "",
        "booking_reference": booking_reference,
        "booking_number": data.get("booking_number") or "",
        "branch_name": branch_name,
        "branch_location": branch_location,
        "branch_phone": branch_phone,
        "service_name": service_name,
        "service_description": service_description,
        "service_arrangement": data.get("service_arrangement") or "",
        "appointment_date": appointment_date,
        "appointment_time": appointment_time,
        "appointment_end_time": appointment_end_time,
        "duration_minutes": duration_minutes,
        "therapist_name": data.get("therapist_name") or "",
        "total_amount": total_amount,
        "currency": data.get("currency") or "KWD",
        "payment_data": payment_data,
    }



def _whatsapp_message(context: dict, customer_name: str) -> str:
    """Compose a professional WhatsApp confirmation message."""
    name = customer_name or "Valued Customer"
    date_str = context["appointment_date"]
    time_str = context["appointment_time"]
    end_time_str = context["appointment_end_time"]
    time_range = f"{time_str}–{end_time_str}" if end_time_str else time_str
    amount = context["total_amount"]
    currency = context["currency"]

    lines = [
        f"✅ *Booking Confirmed* — USHSPA",
        f"",
        f"Dear {name},",
        f"",
        f"Your booking has been successfully confirmed. Here are your details:",
        f"",
        f"📅 *Date:* {date_str}",
        f"🕐 *Time:* {time_range}",
    ]
    if context["therapist_name"]:
        lines.append(f"💆 *Therapist:* {context['therapist_name']}")
    if context["service_name"]:
        lines.append(f"🧴 *Service:* {context['service_name']}")
    if context["branch_name"]:
        lines.append(f"📍 *Branch:* {context['branch_name']}")
    if amount:
        lines.append(f"💳 *Total Paid:* {amount} {currency}")
    if context["booking_reference"]:
        lines.append(f"🔖 *Ref:* {context['booking_reference']}")

    lines += [
        f"",
        f"We look forward to welcoming you! Please arrive 5 minutes early.",
        f"",
        f"For assistance, contact us at any time.",
        f"— The USHSPA Team",
    ]
    return "\n".join(lines)


def _sms_message(context: dict, customer_name: str) -> str:
    """Compose a concise SMS confirmation message.

    Format:
        USHSPA:
        Hi <name>, your booking is CONFIRMED for <date> at <time>. Your booking number:<number> See you soon!
    """
    name = customer_name.split()[0] if customer_name else "Customer"
    date_str = context["appointment_date"]
    time_str = context["appointment_time"]
    booking_number = context.get("booking_number") or context.get("booking_reference") or ""
    return (
        f"USHSPA: \n"
        f"Hi {name}, your booking is CONFIRMED for {date_str} at {time_str}."
        f" Your booking number:{booking_number} See you soon!"
    )


def _email_subject(context: dict) -> str:
    ref = context["booking_reference"]
    return f"Booking Confirmed – {ref}" if ref else "Your USHSPA Booking is Confirmed"


# ── Pending-payment message composers ─────────────────────────────────────────

def _whatsapp_pending_payment_message(
    context: dict, customer_name: str, payment_link: str
) -> str:
    """
    WhatsApp message for confirmed bookings where payment is still pending.
    Includes the payment link so the customer can complete payment.
    """
    name = customer_name or "Valued Customer"
    date_str = context["appointment_date"]
    time_str = context["appointment_time"]
    end_time_str = context["appointment_end_time"]
    time_range = f"{time_str}–{end_time_str}" if end_time_str else time_str
    amount = context["total_amount"]
    currency = context["currency"]

    lines = [
        "📋 *Booking Confirmed – Payment Required* — USHSPA",
        "",
        f"Dear {name},",
        "",
        "Great news! Your booking has been *confirmed*. Please complete your payment to secure your appointment.",
        "",
        f"📅 *Date:* {date_str}",
        f"🕐 *Time:* {time_range}",
    ]
    if context["therapist_name"]:
        lines.append(f"💆 *Therapist:* {context['therapist_name']}")
    if context["service_name"]:
        lines.append(f"🧴 *Service:* {context['service_name']}")
    if context["branch_name"]:
        lines.append(f"📍 *Branch:* {context['branch_name']}")
    if amount:
        lines.append(f"💰 *Amount Due:* {amount} {currency}")
    if context["booking_reference"]:
        lines.append(f"🔖 *Ref:* {context['booking_reference']}")

    lines += [
        "",
        f"💳 *Pay Now:* {payment_link}",
        "",
        "Your slot is reserved. Please pay before your appointment to avoid cancellation.",
        "",
        "For assistance, contact us at any time.",
        "— The USHSPA Team",
    ]
    return "\n".join(lines)


def _sms_pending_payment_message(
    context: dict, customer_name: str, payment_link: str
) -> str:
    """Concise SMS for confirmed bookings where payment is still pending."""
    name = customer_name.split()[0] if customer_name else "Customer"
    date_str = context["appointment_date"]
    time_str = context["appointment_time"]
    amount = context["total_amount"]
    currency = context["currency"]
    amount_part = f" {amount} {currency}" if amount else ""
    return (
        f"USHSPA: Hi {name}, your booking for {date_str} at {time_str} is confirmed. "
        f"Please pay{amount_part} to secure your slot: {payment_link}"
    )


def _email_pending_payment_subject(context: dict) -> str:
    ref = context["booking_reference"]
    return (
        f"Booking Confirmed – Payment Required ({ref})"
        if ref
        else "Your USHSPA Booking is Confirmed – Payment Required"
    )


class BookingConfirmedHandler:
    """
    Processes booking.confirmed events.

    Responsibilities (in order):
      1. Create appointment-cache in ushauth.
      2. Create payment record in ushbooknpay (only when is_paid=True).
      3. Create / increment loyalty tracker (branch bookings, eligible services).
      4. Send customer notifications:
           • payment_status == 'pending' → payment-link messages (WhatsApp/SMS + Email).
           • otherwise                   → standard booking-confirmed messages.
    """

    event_type: str = "booking.confirmed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        customer_id = str(data.get("customer_id") or "")
        booking_id = str(data.get("booking_id") or "")
        customer_name = str(data.get("customer_name") or "")
        correlation_id = envelope.correlation_id_str
        payment_data: dict = data.get("payment_data") or {}
        booking_type: str = str(data.get("booking_type") or "branch")
        # Payment record should be created when:
        #   a) payment_data.is_paid == True  (explicit flag from gateway callback), OR
        #   b) event-level payment_status == "success" (booking created with status=confirmed+payment_status=success)
        _event_payment_status: str = str(data.get("payment_status") or "").lower()
        is_paid: bool = (
            payment_data.get("is_paid") is True
            or payment_data.get("is_paid") == "true"
            or _event_payment_status == "success"
        )

        # Detect pending-payment scenario: booking confirmed but customer hasn't paid yet
        payment_status: str = str(data.get("payment_status") or "").lower()
        payment_is_pending: bool = payment_status == "pending"

        whatsapp_verified: bool = bool(data.get("whatsapp_verified") or data.get("is_whatsapp_verified"))
        email_verified: bool = bool(data.get("email_verified") or data.get("is_email_verified"))

        context = _build_booking_context(data)

        # ── Helper: resolve appointment date/time from event data ────────────
        appt_start: str = data.get("appointment_start") or ""
        appt_date: str = (
            data.get("appointment_date")
            or (appt_start.split("T")[0] if "T" in appt_start else "")
        )
        # Keep full HH:MM:SS format as required by ushauth create-appointment-cache
        appt_time_raw: str = (
            data.get("appointment_starttime")
            or data.get("appointment_time")
            or (appt_start.split("T")[1].rstrip("Z").split("+")[0] if "T" in appt_start else "")
        )
        # Ensure at least HH:MM:SS — pad with :00 if only HH:MM given
        appt_time: str = appt_time_raw if len(appt_time_raw) >= 8 else f"{appt_time_raw}:00" if len(appt_time_raw) == 5 else appt_time_raw
        duration: int = int(
            data.get("total_duration")
            or data.get("duration_minutes")
            or 60
        )

        # ── 1. Create appointment-cache in ushauth ───────────────────────────
        if booking_id:
            try:
                ushauth_client = UshAuthClient()
                # payment_status in cache must be a value ushauth accepts:
                # unpaid | success | pending | refunded | failed
                # Map ushbooknpay-specific values (e.g. "rewarded") → "success"
                _USHAUTH_VALID_PAYMENT_STATUSES = {"unpaid", "success", "pending", "refunded", "failed"}
                _raw_ps: str = str(data.get("payment_status") or "success").lower()
                cache_payment_status: str = _raw_ps if _raw_ps in _USHAUTH_VALID_PAYMENT_STATUSES else "success"
                # ushauth BookingType only accepts: branch | home | leave
                # Map ushbooknpay-specific "loyalty" → "branch" (loyalty bookings are branch visits)
                _USHAUTH_VALID_BOOKING_TYPES = {"branch", "home", "leave"}
                _cache_booking_type: str = booking_type if booking_type in _USHAUTH_VALID_BOOKING_TYPES else "branch"

                _bc_cache_payload: dict = {
                    "service_arrangement_id": str(data.get("service_arrangement_id") or "") or None,
                    "therapist_id": str(data.get("therapist_id") or ""),
                    "booking_id": booking_id,
                    "appointment_date": appt_date,
                    "appointment_time": appt_time,
                    "booking_type": _cache_booking_type,
                    "duration": duration,
                    "status": "confirmed",
                    "customer_id": customer_id or None,
                    "customer_name": customer_name or None,
                    "branch_id": str(data.get("branch_id") or "") or None,
                    "service_id": str(data.get("service_id") or "") or None,
                    "payment_status": cache_payment_status,
                }
                logger.info(
                    "booking_confirmed_appointment_cache_request",
                    booking_id=booking_id,
                    payload=_bc_cache_payload,
                )
                _bc_cache_response = await ushauth_client.create_appointment_cache(
                    # Pass None (not "") for optional UUID fields — ushauth UUIDField rejects empty strings
                    service_arrangement_id=_bc_cache_payload["service_arrangement_id"],
                    therapist_id=_bc_cache_payload["therapist_id"],
                    booking_id=booking_id,
                    appointment_date=appt_date,
                    appointment_time=appt_time,
                    booking_type=_cache_booking_type,
                    duration=duration,
                    status="confirmed",
                    customer_id=customer_id or None,
                    customer_name=customer_name or None,
                    branch_id=str(data.get("branch_id") or "") or None,
                    service_id=str(data.get("service_id") or "") or None,
                    payment_status=cache_payment_status,
                    correlation_id=correlation_id,
                )
                await ushauth_client.aclose()
                logger.info(
                    "booking_confirmed_appointment_cache_response",
                    booking_id=booking_id,
                    response=_bc_cache_response,
                )
                logger.info(
                    "booking_confirmed_appointment_cache_created",
                    booking_id=booking_id,
                    customer_id=customer_id,
                    appointment_date=appt_date,
                    appointment_time=appt_time,
                )
            except Exception as exc:
                # Non-blocking — notification flow must not fail due to cache errors
                logger.warning(
                    "booking_confirmed_appointment_cache_failed",
                    booking_id=booking_id,
                    error=str(exc),
                )

        # ── 2. Create payment record in ushbooknpay ──────────────────────────
        # Only create a payment record when the booking was actually paid AND
        # it is NOT a gift-voucher booking.  For gift_voucher bookings the
        # payment record is already created by ushbooknpay at voucher-redemption
        # time — creating it again here would produce a duplicate.
        payment_type: str = str(data.get("payment_type") or "").lower()
        _is_gift_voucher: bool = payment_type == "gift_voucher"
        if booking_id and is_paid and not _is_gift_voucher:
            try:
                booknpay_client = UshBookNPayClient()
                pricing: dict = data.get("pricing") or {}
                total_amount: str = (
                    str(payment_data.get("invoice_value") or "")
                    or str(data.get("total_amount") or "")
                    or str(pricing.get("total") or "0")
                )
                currency: str = str(
                    data.get("currency")
                    or pricing.get("currency")
                    or "KWD"
                )
                payment_gateway: str = str(
                    payment_data.get("payment_gateway") or "myfatoorah"
                )
                meta = payment_data or {}

                # Resolve total_duration from event data (required by new payment model)
                total_duration: int = int(
                    data.get("total_duration")
                    or data.get("duration_minutes")
                    or data.get("duration")
                    or pricing.get("duration_minutes")
                    or 0
                )

                # Normalise payment_provider to new enum values
                _raw_provider = str(meta.get("provider") or meta.get("payment_provider") or "myfatoorah").lower()
                if "fatoorah" in _raw_provider:
                    payment_provider = "MyFatoorah"
                elif "directlink" in _raw_provider or "direct" in _raw_provider:
                    payment_provider = "DirectLink"
                elif "deema" in _raw_provider:
                    payment_provider = "Deema"
                else:
                    payment_provider = "MyFatoorah"

                # Normalise payment_for based on booking type
                if booking_type in ("home_service", "home"):
                    payment_for = "home_service"
                else:
                    payment_for = "branch_service"

                # Normalise payment_gateway to spec values: KNET | TAP | Other
                _gw_raw = str(meta.get("payment_gateway") or payment_gateway or "").upper()
                if "KNET" in _gw_raw or "K-NET" in _gw_raw:
                    normalised_gateway = "KNET"
                elif "TAP" in _gw_raw:
                    normalised_gateway = "TAP"
                elif _gw_raw:
                    normalised_gateway = "Other"
                else:
                    normalised_gateway = None

                # Build payload matching the new ushbooknpay Payment model spec
                payload: dict[str, Any] = {
                    # ── Required fields ────────────────────────────────────
                    "customer_id": customer_id,
                    "total_amount": total_amount,
                    "total_duration": total_duration,
                    "currency": currency,
                    # ── Associations ───────────────────────────────────────
                    "booking_id": booking_id,
                    # ── Status ─────────────────────────────────────────────
                    "status": "success",
                    "payment_for": payment_for,
                    # ── Classification ─────────────────────────────────────
                    "payment_provider": payment_provider,
                    "payment_through": "ushspa",
                    "payment_gateway": normalised_gateway,
                    # payment_method: prefer explicit value from payment_data over gateway-inferred default
                    "payment_method": (
                        str(
                            meta.get("payment_method")
                            or meta.get("paymentMethod")
                            or meta.get("PaymentMethod")
                            or ("knet" if normalised_gateway == "KNET" else "card")
                        )
                    ),
                    # ── Invoice & Transaction identifiers ──────────────────
                    # Check snake_case (actual event format) then camelCase (MyFatoorah) for each field
                    "payment_id": (
                        meta.get("payment_id")
                        or meta.get("transaction_id")
                        or meta.get("transactionId")
                        or meta.get("TransactionId")
                        or meta.get("invoice_id")
                        or meta.get("invoiceId")
                        or meta.get("InvoiceId")
                    ),
                    "transaction_id": (
                        meta.get("transaction_id")
                        or meta.get("transactionId")
                        or meta.get("TransactionId")
                    ),
                    "invoice_id": (
                        meta.get("invoice_id")
                        or meta.get("invoiceId")
                        or meta.get("InvoiceId")
                    ),
                    "invoice_value": meta.get("invoice_value") or total_amount,
                    "reference_id": (
                        meta.get("reference_id")
                        or meta.get("referenceId")
                        or meta.get("ReferenceId")
                    ),
                    "track_id": (
                        meta.get("trace_id")         # snake_case alias used in actual payloads
                        or meta.get("track_id")
                        or meta.get("trackId")
                        or meta.get("TrackId")
                    ),
                    # Normalize transaction_status: "Paid" / "paid" / "Succss" (sic) → "success"
                    "transaction_status": (
                        "success"
                        if str(meta.get("transaction_status") or meta.get("status") or "").lower()
                           in ("paid", "success", "succss")
                        else str(meta.get("transaction_status") or "") or None
                    ),
                    "transaction_date": (
                        meta.get("transaction_date")
                        or meta.get("transactionDate")
                        or meta.get("TransactionDate")
                    ),
                    "payment_url": meta.get("payment_url"),
                    # ── Service & location ─────────────────────────────────
                    "service_id": str(data.get("service_id") or "") or None,
                    "service_data": data.get("service_data") or {
                        "name": str(data.get("service_name") or ""),
                    },
                    "branch_id": str(data.get("branch_id") or "") or None,
                    "branch_data": data.get("branch_data") or {
                        "name": str(data.get("branch_name") or ""),
                    },
                    "service_arrangement_id": str(data.get("service_arrangement_id") or "") or None,
                    "service_arrangement_data": data.get("service_arrangement_data") or None,
                    # ── Pricing breakdown ──────────────────────────────────
                    "addons": data.get("addons") or None,
                    "addons_price": str(data.get("addon_price") or pricing.get("addon_price") or "") or None,
                    "extra_time": data.get("extra_minutes") or data.get("extra_time") or None,
                    "price_for_extra_time": str(data.get("price_for_extra_minutes") or "") or None,
                    # ── Booking data snapshot ──────────────────────────────
                    "booking_data": {
                        "booking_id": booking_id,
                        "service_id": str(data.get("service_id") or ""),
                        "service_name": str(data.get("service_name") or ""),
                        "branch_id": str(data.get("branch_id") or ""),
                        "branch_name": str(data.get("branch_name") or ""),
                        "appointment_start": str(data.get("appointment_start") or ""),
                        "appointment_end": str(data.get("appointment_end") or ""),
                        "duration_minutes": total_duration,
                        "booking_type": booking_type,
                        "currency": currency,
                        "total_amount": total_amount,
                    },
                    # ── Customer data snapshot ─────────────────────────────
                    "customer_data": {
                        "name": customer_name or meta.get("customer_name") or data.get("customer_name"),
                        "mobile": (
                            meta.get("customer_mobile")
                            or meta.get("customer_phone")
                            or data.get("customer_phone")
                            or data.get("customer_mobile")
                        ),
                        "phone_number": (
                            meta.get("customer_mobile")
                            or meta.get("customer_phone")
                            or data.get("customer_phone")
                        ),
                        "email": meta.get("customer_email") or data.get("customer_email"),
                    },
                    # ── Raw gateway identifiers → payment_data JSONB ───────
                    "payment_data": {k: v for k, v in {
                        "invoice_reference": meta.get("invoice_reference"),
                        "customer_reference": meta.get("customer_reference"),
                        "authorization_id": meta.get("authorization_id"),
                        "gateway_name": meta.get("payment_gateway") or payment_gateway,
                        "vat_amount": meta.get("vat_amount"),
                        "created_date": meta.get("created_date"),
                        "raw_payment_data": meta,
                    }.items() if v is not None},
                }

                await booknpay_client._client.post(
                    "/api/v1/payments/",
                    json=payload,
                    correlation_id=correlation_id,
                )
                await booknpay_client.aclose()
                logger.info(
                    "booking_confirmed_payment_record_created",
                    booking_id=booking_id,
                    customer_id=customer_id,
                    total_amount=total_amount,
                    total_duration=total_duration,
                    payment_provider=payment_provider,
                )
            except Exception as exc:
                # Non-blocking — notification flow must not fail due to payment record errors
                logger.warning(
                    "booking_confirmed_payment_record_failed",
                    booking_id=booking_id,
                    error=str(exc),
                )
        elif _is_gift_voucher:
            # Payment record was already created by ushbooknpay at voucher-redemption
            # time — skip to avoid duplicate and log for traceability.
            logger.info(
                "booking_confirmed_payment_record_skipped_gift_voucher",
                booking_id=booking_id,
                customer_id=customer_id,
            )

        # ── 3. Create / increment loyalty tracker in ushbooknpay ─────────────
        # Only for branch bookings (not home service) on loyalty-eligible services
        is_eligible_for_loyalty: bool = bool(data.get("is_eligible_for_loyalty"))
        if booking_id and booking_type in ("branch_service", "branch") and customer_id and data.get("service_id") and is_eligible_for_loyalty:
            try:
                loyalty_client = UshBookNPayClient()
                service_data_dict: dict = data.get("service_data") or {}
                service_name_val = data.get("service_name") or service_data_dict.get("name") or ""
                await loyalty_client.record_loyalty_tracker(
                    customer_id=customer_id,
                    service_id=str(data.get("service_id") or ""),
                    booking_id=booking_id,
                    service_arrangement_id=str(data.get("service_arrangement_id") or "") or None,
                    booking_type=booking_type,
                    customer_name=customer_name or "",
                    customer_email=str(data.get("customer_email") or ""),
                    customer_phone=str(data.get("customer_phone") or ""),
                    service_name=str(service_name_val),
                    is_eligible_for_loyalty=True,
                    correlation_id=correlation_id,
                )
                await loyalty_client.aclose()
                logger.info(
                    "booking_confirmed_loyalty_tracker_recorded",
                    booking_id=booking_id,
                    customer_id=customer_id,
                    service_id=str(data.get("service_id") or ""),
                )
            except Exception as exc:
                # Non-blocking — loyalty failure must never break the flow
                logger.warning(
                    "booking_confirmed_loyalty_tracker_failed",
                    booking_id=booking_id,
                    error=str(exc),
                )
        elif booking_id and booking_type in ("branch_service", "branch") and not is_eligible_for_loyalty:
            logger.info(
                "loyalty_skipped_not_eligible",
                booking_id=booking_id,
                service_id=str(data.get("service_id") or ""),
            )

        # ── 4–6. Customer notifications ──────────────────────────────────────
        # payment_status=pending → booking slot reserved, but payment not yet made.
        #                          Send payment-link messages so the customer can pay.
        # All other statuses    → standard "booking confirmed" messages.
        logger.info(
            "booking_confirmed_notification_dispatch",
            booking_id=booking_id,
            payment_status=payment_status,
            payment_is_pending=payment_is_pending,
            whatsapp_verified=whatsapp_verified,
            has_phone=bool(data.get("customer_phone")),
            has_email=bool(data.get("customer_email")),
        )
        if payment_is_pending:
            await self._send_pending_payment_notifications(
                envelope=envelope,
                service=service,
                data=data,
                context=context,
                customer_id=customer_id,
                booking_id=booking_id,
                customer_name=customer_name,
                correlation_id=correlation_id,
                whatsapp_verified=whatsapp_verified,
            )
        else:
            await self._send_confirmed_notifications(
                envelope=envelope,
                service=service,
                data=data,
                context=context,
                customer_id=customer_id,
                booking_id=booking_id,
                customer_name=customer_name,
                correlation_id=correlation_id,
                whatsapp_verified=whatsapp_verified,
            )

    # ── Notification sub-methods ──────────────────────────────────────────────

    async def _send_confirmed_notifications(
        self,
        *,
        envelope: EventEnvelope,
        service: NotificationService,
        data: dict,
        context: dict,
        customer_id: str,
        booking_id: str,
        customer_name: str,
        correlation_id: str,
        whatsapp_verified: bool,
    ) -> None:
        """Send standard 'booking confirmed' messages (payment already complete)."""
        whatsapp_sent = False

        # WhatsApp (if verified)
        if whatsapp_verified:
            wa_recipient = ChannelResolver.resolve_whatsapp_recipient(data)
            if wa_recipient:
                wa_body = _whatsapp_message(context, wa_recipient.name or customer_name)
                req_wa = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=wa_recipient,
                    template_name="booking/confirmed_whatsapp",
                    template_context={
                        **context,
                        "customer_name": wa_recipient.name or customer_name,
                        "message_body": wa_body,
                    },
                    customer_id=customer_id or None,
                    booking_id=booking_id or None,
                    correlation_id=correlation_id,
                )
                try:
                    await service.send(req_wa)
                    whatsapp_sent = True
                    logger.info(
                        "booking_confirmed_whatsapp_sent",
                        booking_id=booking_id,
                        customer_id=customer_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "booking_confirmed_whatsapp_failed",
                        booking_id=booking_id,
                        error=str(exc),
                    )

        # SMS — only if WhatsApp was NOT sent
        if not whatsapp_sent:
            sms_recipient = ChannelResolver.resolve_sms_recipient(data)
            if sms_recipient:
                sms_body = _sms_message(context, sms_recipient.name or customer_name)
                req_sms = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=sms_recipient,
                    template_name="booking/confirmed_sms",
                    template_context={
                        **context,
                        "customer_name": sms_recipient.name or customer_name,
                        "message_body": sms_body,
                    },
                    customer_id=customer_id or None,
                    booking_id=booking_id or None,
                    correlation_id=correlation_id,
                )
                try:
                    await service.send(req_sms)
                    logger.info(
                        "booking_confirmed_sms_sent",
                        booking_id=booking_id,
                        customer_id=customer_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "booking_confirmed_sms_failed",
                        booking_id=booking_id,
                        error=str(exc),
                    )
            else:
                logger.info(
                    "booking_confirmed_sms_skipped_no_recipient",
                    booking_id=booking_id,
                    customer_phone=data.get("customer_phone") or "",
                )

        # Email — independent, sent if email is present
        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            req_email = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="booking/confirmed_email",
                template_context={
                    **context,
                    "customer_name": email_recipient.name or customer_name,
                },
                subject=_email_subject(context),
                customer_id=customer_id or None,
                booking_id=booking_id or None,
                correlation_id=correlation_id,
            )
            try:
                await service.send(req_email)
                logger.info(
                    "booking_confirmed_email_sent",
                    booking_id=booking_id,
                    customer_id=customer_id,
                )
            except Exception as exc:
                logger.warning(
                    "booking_confirmed_email_failed",
                    booking_id=booking_id,
                    error=str(exc),
                )
        else:
            logger.info(
                "booking_confirmed_email_skipped_no_recipient",
                booking_id=booking_id,
                customer_email=data.get("customer_email") or "",
            )

    async def _send_pending_payment_notifications(
        self,
        *,
        envelope: EventEnvelope,
        service: NotificationService,
        data: dict,
        context: dict,
        customer_id: str,
        booking_id: str,
        customer_name: str,
        correlation_id: str,
        whatsapp_verified: bool,
    ) -> None:
        """
        Send booking-confirmed + payment-link messages when payment_status=pending.

        The customer's slot is reserved. They must pay to finalise the appointment.
        Messages include a direct payment link to complete the transaction.
        """
        payment_link = _resolve_payment_link(data)
        whatsapp_sent = False

        logger.info(
            "booking_confirmed_pending_payment_notification_start",
            booking_id=booking_id,
            customer_id=customer_id,
            customer_phone=data.get("customer_phone") or "",
            customer_email=data.get("customer_email") or "",
            whatsapp_verified=whatsapp_verified,
            payment_link=payment_link,
        )

        # WhatsApp (if verified)
        if whatsapp_verified:
            wa_recipient = ChannelResolver.resolve_whatsapp_recipient(data)
            if wa_recipient:
                wa_body = _whatsapp_pending_payment_message(
                    context, wa_recipient.name or customer_name, payment_link
                )
                req_wa = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=wa_recipient,
                    template_name="booking/confirmed_pending_payment_whatsapp",
                    template_context={
                        **context,
                        "customer_name": wa_recipient.name or customer_name,
                        "payment_link": payment_link,
                        "message_body": wa_body,
                    },
                    customer_id=customer_id or None,
                    booking_id=booking_id or None,
                    correlation_id=correlation_id,
                )
                try:
                    await service.send(req_wa)
                    whatsapp_sent = True
                    logger.info(
                        "booking_confirmed_pending_payment_whatsapp_sent",
                        booking_id=booking_id,
                        customer_id=customer_id,
                        payment_link=payment_link,
                    )
                except Exception as exc:
                    logger.warning(
                        "booking_confirmed_pending_payment_whatsapp_failed",
                        booking_id=booking_id,
                        error=str(exc),
                    )

        # SMS — only if WhatsApp was NOT sent
        if not whatsapp_sent:
            sms_recipient = ChannelResolver.resolve_sms_recipient(data)
            if sms_recipient:
                sms_body = _sms_pending_payment_message(
                    context, sms_recipient.name or customer_name, payment_link
                )
                req_sms = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=sms_recipient,
                    template_name="booking/confirmed_pending_payment_sms",
                    template_context={
                        **context,
                        "customer_name": sms_recipient.name or customer_name,
                        "payment_link": payment_link,
                        "message_body": sms_body,
                    },
                    customer_id=customer_id or None,
                    booking_id=booking_id or None,
                    correlation_id=correlation_id,
                )
                try:
                    await service.send(req_sms)
                    logger.info(
                        "booking_confirmed_pending_payment_sms_sent",
                        booking_id=booking_id,
                        customer_id=customer_id,
                        payment_link=payment_link,
                    )
                except Exception as exc:
                    logger.warning(
                        "booking_confirmed_pending_payment_sms_failed",
                        booking_id=booking_id,
                        error=str(exc),
                    )
            else:
                logger.info(
                    "booking_confirmed_pending_payment_sms_skipped_no_recipient",
                    booking_id=booking_id,
                    customer_phone=data.get("customer_phone") or "",
                )

        # Email — independent, sent if email is present
        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            req_email = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="booking/confirmed_pending_payment_email",
                template_context={
                    **context,
                    "customer_name": email_recipient.name or customer_name,
                    "payment_link": payment_link,
                },
                subject=_email_pending_payment_subject(context),
                customer_id=customer_id or None,
                booking_id=booking_id or None,
                correlation_id=correlation_id,
            )
            try:
                await service.send(req_email)
                logger.info(
                    "booking_confirmed_pending_payment_email_sent",
                    booking_id=booking_id,
                    customer_id=customer_id,
                    payment_link=payment_link,
                )
            except Exception as exc:
                logger.warning(
                    "booking_confirmed_pending_payment_email_failed",
                    booking_id=booking_id,
                    error=str(exc),
                )
        else:
            logger.info(
                "booking_confirmed_pending_payment_email_skipped_no_recipient",
                booking_id=booking_id,
                customer_email=data.get("customer_email") or "",
            )
