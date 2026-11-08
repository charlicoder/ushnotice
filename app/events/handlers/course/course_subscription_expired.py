"""
Handler for ``course_subscription.expired`` event.

Fired by ushbooknpay when an active course subscription passes ``expires_at``.
Unbooked sessions are lost at expiry; this handler informs the customer
(WhatsApp if verified, SMS fallback).
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


def _has_lost(c: dict) -> bool:
    try:
        return int(c["lost_sessions"]) > 0
    except (TypeError, ValueError):
        return False


def _whatsapp_en(c: dict, name: str) -> str:
    lines = [
        "⌛ *Course Expired* — USHSPA",
        "",
        f"Dear {name or 'Valued Customer'},",
        "",
        f"Your course *{c['course_name']}* expired"
        + (f" on {c['expires_date']}." if c["expires_date"] else "."),
    ]
    if _has_lost(c):
        lines.append(f"{c['lost_sessions']} unbooked session(s) are no longer available.")
    lines.append("Sessions already booked before the expiry date remain valid.")
    if c["subscription_number"]:
        lines += ["", f"🔖 *Ref:* {c['subscription_number']}"]
    lines += ["", "We would love to see you again — ask us about our latest courses.", "— The USHSPA Team"]
    return "\n".join(lines)


def _whatsapp_ar(c: dict, name: str) -> str:
    lines = [
        "⌛ *انتهت صلاحية الباقة* — USHSPA",
        "",
        f"عزيزي {name or 'العميل العزيز'}،",
        "",
        f"انتهت صلاحية باقتك *{c['course_name']}*"
        + (f" بتاريخ {c['expires_date']}." if c["expires_date"] else "."),
    ]
    if _has_lost(c):
        lines.append(f"لم تعد {c['lost_sessions']} جلسة/جلسات غير محجوزة متاحة.")
    lines.append("الجلسات المحجوزة قبل تاريخ الانتهاء تبقى صالحة.")
    if c["subscription_number"]:
        lines += ["", f"🔖 *رقم المرجع:* {c['subscription_number']}"]
    lines += ["", "يسعدنا رؤيتك مجدداً — اسألنا عن أحدث باقاتنا.", "— فريق USHSPA"]
    return "\n".join(lines)


def _sms_en(c: dict, first: str) -> str:
    lost = f" {c['lost_sessions']} unbooked session(s) expired." if _has_lost(c) else ""
    return f"USHSPA:\nHi {first or 'Customer'}, your course {c['course_name']} has expired.{lost}"


def _sms_ar(c: dict, first: str) -> str:
    lost = f" انتهت {c['lost_sessions']} جلسة/جلسات غير محجوزة." if _has_lost(c) else ""
    return f"USHSPA:\nمرحباً {first or 'عميلنا'}، انتهت صلاحية باقتك {c['course_name']}.{lost}"


class CourseSubscriptionExpiredHandler:
    """Processes ``course_subscription.expired`` events."""

    event_type: str = "course_subscription.expired"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        c = course_context(data)
        cust = customer_info(data)
        result = await send_course_notification(
            envelope=envelope,
            ctx=ctx,
            data=data,
            template_key="expired",
            entity_id=c["subscription_id"],
            whatsapp_en=_whatsapp_en(c, cust["customer_name"]),
            whatsapp_ar=_whatsapp_ar(c, cust["customer_name"]),
            sms_en=_sms_en(c, cust["first_name"]),
            sms_ar=_sms_ar(c, cust["first_name"]),
        )
        logger.info(
            "course_subscription_expired_processed",
            subscription_id=c["subscription_id"],
            lost_sessions=c["lost_sessions"],
            channel=result,
        )

