"""
Handler for ``course_subscription.refunded`` event.

Fired by ushbooknpay after a full course refund (allowed only when no session
was used or reserved).  The refund record and the credit note are created in
ushbooknpay/ushanr — this handler only notifies the customer
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


def _whatsapp_en(c: dict, name: str) -> str:
    lines = [
        "↩️ *Course Refunded* — USHSPA",
        "",
        f"Dear {name or 'Valued Customer'},",
        "",
        f"Your course *{c['course_name']}* has been refunded and cancelled.",
        "",
    ]
    if c["price"]:
        lines.append(f"💰 *Refund Amount:* {c['price']} {c['currency']}")
    if c["credit_note_number"]:
        lines.append(f"🧾 *Credit Note:* {c['credit_note_number']}")
    if c["subscription_number"]:
        lines.append(f"🔖 *Ref:* {c['subscription_number']}")
    lines += ["", "For assistance, contact us at any time.", "— The USHSPA Team"]
    return "\n".join(lines)


def _whatsapp_ar(c: dict, name: str) -> str:
    lines = [
        "↩️ *تم استرداد قيمة الباقة* — USHSPA",
        "",
        f"عزيزي {name or 'العميل العزيز'}،",
        "",
        f"تم استرداد قيمة باقتك *{c['course_name']}* وإلغاؤها.",
        "",
    ]
    if c["price"]:
        lines.append(f"💰 *المبلغ المسترد:* {c['price']} {c['currency']}")
    if c["credit_note_number"]:
        lines.append(f"🧾 *إشعار دائن:* {c['credit_note_number']}")
    if c["subscription_number"]:
        lines.append(f"🔖 *رقم المرجع:* {c['subscription_number']}")
    lines += ["", "للمساعدة، تواصل معنا في أي وقت.", "— فريق USHSPA"]
    return "\n".join(lines)


def _sms_en(c: dict, first: str) -> str:
    amount = f" Amount: {c['price']} {c['currency']}." if c["price"] else ""
    ref = f" Ref:{c['subscription_number']}" if c["subscription_number"] else ""
    return f"USHSPA:\nHi {first or 'Customer'}, your course {c['course_name']} has been refunded.{amount}{ref}"


def _sms_ar(c: dict, first: str) -> str:
    amount = f" المبلغ: {c['price']} {c['currency']}." if c["price"] else ""
    ref = f" رقم المرجع: {c['subscription_number']}" if c["subscription_number"] else ""
    return f"USHSPA:\nمرحباً {first or 'عميلنا'}، تم استرداد قيمة باقتك {c['course_name']}.{amount}{ref}"


class CourseSubscriptionRefundedHandler:
    """Processes ``course_subscription.refunded`` events."""

    event_type: str = "course_subscription.refunded"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        c = course_context(data)
        cust = customer_info(data)
        result = await send_course_notification(
            envelope=envelope,
            ctx=ctx,
            data=data,
            template_key="refunded",
            entity_id=c["subscription_id"],
            whatsapp_en=_whatsapp_en(c, cust["customer_name"]),
            whatsapp_ar=_whatsapp_ar(c, cust["customer_name"]),
            sms_en=_sms_en(c, cust["first_name"]),
            sms_ar=_sms_ar(c, cust["first_name"]),
        )
        logger.info(
            "course_subscription_refunded_processed",
            subscription_id=c["subscription_id"],
            refund_id=c["refund_id"],
            channel=result,
        )

