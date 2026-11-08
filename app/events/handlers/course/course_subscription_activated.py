"""
Handler for ``course_subscription.activated`` event.

Fired by ushbooknpay once a course subscription is paid and activated
(confirm-payment at the desk, payment-link settlement or ``POST /payments``).
Payment, invoice and activation are already done in ushbooknpay — this handler
only sends the purchase confirmation (WhatsApp if verified, SMS fallback) with
course name, sessions, expiry and invoice number.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import HandlerContext
from app.events.handlers.course._common import (
    course_context,
    customer_info,
    send_course_notification,
)
from app.events.schemas.envelope import EventEnvelope

logger = get_logger(__name__)


def _whatsapp_en(c: dict, name: str) -> str:
    lines = [
        "✅ *Course Purchase Confirmed* — USHSPA",
        "",
        f"Dear {name or 'Valued Customer'},",
        "",
        f"Thank you for purchasing *{c['course_name'] or 'your course'}*. Your course is now active.",
        "",
    ]
    if c["service_name"]:
        lines.append(f"🧴 *Service:* {c['service_name']}")
    if c["total_sessions"]:
        lines.append(f"🎟️ *Sessions:* {c['total_sessions']}")
    if c["price"]:
        lines.append(f"💳 *Amount Paid:* {c['price']} {c['currency']}")
    if c["expires_date"]:
        lines.append(f"📅 *Valid until:* {c['expires_date']}")
    if c["invoice_number"]:
        lines.append(f"🧾 *Invoice:* {c['invoice_number']}")
    if c["subscription_number"]:
        lines.append(f"🔖 *Ref:* {c['subscription_number']}")
    lines += [
        "",
        "Book your sessions any time before the expiry date. Unbooked sessions expire with the course.",
        "",
        "— The USHSPA Team",
    ]
    return "\n".join(lines)


def _whatsapp_ar(c: dict, name: str) -> str:
    lines = [
        "✅ *تم تأكيد شراء الباقة* — USHSPA",
        "",
        f"عزيزي {name or 'العميل العزيز'}،",
        "",
        f"شكراً لشرائك *{c['course_name'] or 'الباقة'}*. باقتك مفعّلة الآن.",
        "",
    ]
    if c["service_name"]:
        lines.append(f"🧴 *الخدمة:* {c['service_name']}")
    if c["total_sessions"]:
        lines.append(f"🎟️ *عدد الجلسات:* {c['total_sessions']}")
    if c["price"]:
        lines.append(f"💳 *المبلغ المدفوع:* {c['price']} {c['currency']}")
    if c["expires_date"]:
        lines.append(f"📅 *صالحة حتى:* {c['expires_date']}")
    if c["invoice_number"]:
        lines.append(f"🧾 *رقم الفاتورة:* {c['invoice_number']}")
    if c["subscription_number"]:
        lines.append(f"🔖 *رقم المرجع:* {c['subscription_number']}")
    lines += [
        "",
        "يمكنك حجز جلساتك في أي وقت قبل تاريخ الانتهاء. الجلسات غير المحجوزة تنتهي بانتهاء الباقة.",
        "",
        "— فريق USHSPA",
    ]
    return "\n".join(lines)


def _sms_en(c: dict, first: str) -> str:
    sessions = f" ({c['total_sessions']} sessions)" if c["total_sessions"] else ""
    expiry = f" valid until {c['expires_date']}" if c["expires_date"] else ""
    inv = f" Invoice:{c['invoice_number']}" if c["invoice_number"] else ""
    ref = f" Ref:{c['subscription_number']}" if c["subscription_number"] else ""
    return (
        f"USHSPA:\nHi {first or 'Customer'}, your course {c['course_name']}{sessions} is ACTIVE{expiry}.{ref}{inv}"
    )


def _sms_ar(c: dict, first: str) -> str:
    sessions = f" ({c['total_sessions']} جلسات)" if c["total_sessions"] else ""
    expiry = f" وصالحة حتى {c['expires_date']}" if c["expires_date"] else ""
    inv = f" رقم الفاتورة: {c['invoice_number']}" if c["invoice_number"] else ""
    ref = f" رقم المرجع: {c['subscription_number']}" if c["subscription_number"] else ""
    return f"USHSPA:\nمرحباً {first or 'عميلنا'}، تم تفعيل باقتك {c['course_name']}{sessions}{expiry}.{ref}{inv}"


class CourseSubscriptionActivatedHandler:
    """Processes ``course_subscription.activated`` events (purchase confirmation)."""

    event_type: str = "course_subscription.activated"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        c = course_context(data)
        cust = customer_info(data)
        result = await send_course_notification(
            envelope=envelope,
            ctx=ctx,
            data=data,
            template_key="activated",
            entity_id=c["subscription_id"],
            whatsapp_en=_whatsapp_en(c, cust["customer_name"]),
            whatsapp_ar=_whatsapp_ar(c, cust["customer_name"]),
            sms_en=_sms_en(c, cust["first_name"]),
            sms_ar=_sms_ar(c, cust["first_name"]),
        )
        logger.info(
            "course_subscription_activated_processed",
            subscription_id=c["subscription_id"],
            subscription_number=c["subscription_number"],
            channel=result,
        )

