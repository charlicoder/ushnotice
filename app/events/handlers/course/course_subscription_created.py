"""
Handler for ``course_subscription.created`` event.

Fired by ushbooknpay when a course is sold at the desk (status
``payment_pending``).  Only when the desk chose ``payment_option=payment_link``
and a ``payment_url`` is present, the payment link is sent to the customer
(WhatsApp if verified, SMS fallback).  ``pay_at_desk`` sales are acknowledged
without a message — the confirmation goes out on ``course_subscription.activated``.
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
        "📋 *Course Reserved – Payment Required* — USHSPA",
        "",
        f"Dear {name or 'Valued Customer'},",
        "",
        f"Your course *{c['course_name']}* is ready. Please complete your payment to activate it.",
        "",
    ]
    if c["service_name"]:
        lines.append(f"🧴 *Service:* {c['service_name']}")
    if c["total_sessions"]:
        lines.append(f"🎟️ *Sessions:* {c['total_sessions']}")
    if c["price"]:
        lines.append(f"💰 *Amount Due:* {c['price']} {c['currency']}")
    if c["subscription_number"]:
        lines.append(f"🔖 *Ref:* {c['subscription_number']}")
    lines += [
        "",
        f"💳 *Pay Now:* {c['payment_url']}",
        "",
        "The course validity starts once the payment is completed.",
        "— The USHSPA Team",
    ]
    return "\n".join(lines)


def _whatsapp_ar(c: dict, name: str) -> str:
    lines = [
        "📋 *حجز باقة – يلزم الدفع* — USHSPA",
        "",
        f"عزيزي {name or 'عميلنا الكريم'}،",
        "",
        f"باقتك *{c['course_name']}* جاهزة. يرجى إتمام الدفع لتفعيلها.",
        "",
    ]
    if c["service_name"]:
        lines.append(f"🧴 *الخدمة:* {c['service_name']}")
    if c["total_sessions"]:
        lines.append(f"🎟️ *عدد الجلسات:* {c['total_sessions']}")
    if c["price"]:
        lines.append(f"💰 *المبلغ المستحق:* {c['price']} {c['currency']}")
    if c["subscription_number"]:
        lines.append(f"🔖 *رقم المرجع:* {c['subscription_number']}")
    lines += [
        "",
        f"💳 *ادفع الآن:* {c['payment_url']}",
        "",
        "تبدأ صلاحية الباقة بعد إتمام الدفع.",
        "— فريق USHSPA",
    ]
    return "\n".join(lines)


def _sms_en(c: dict, first: str) -> str:
    amount = f" {c['price']} {c['currency']}" if c["price"] else ""
    return (
        f"USHSPA: Hi {first or 'Customer'}, please pay{amount} to activate your course "
        f"{c['course_name']}: {c['payment_url']}"
    )


def _sms_ar(c: dict, first: str) -> str:
    amount = f" {c['price']} {c['currency']}" if c["price"] else ""
    return (
        f"USHSPA: مرحباً {first or 'عميلنا'}، يرجى إتمام الدفع{amount} لتفعيل باقتك "
        f"{c['course_name']}: {c['payment_url']}"
    )


class CourseSubscriptionCreatedHandler:
    """Processes ``course_subscription.created`` events (payment link delivery)."""

    event_type: str = "course_subscription.created"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        c = course_context(data)
        if c["payment_option"] != "payment_link" or not c["payment_url"]:
            logger.info(
                "course_subscription_created_no_payment_link_ack",
                subscription_id=c["subscription_id"],
                payment_option=c["payment_option"],
                has_payment_url=bool(c["payment_url"]),
            )
            return

        cust = customer_info(data)
        result = await send_course_notification(
            envelope=envelope,
            ctx=ctx,
            data=data,
            template_key="created_payment_link",
            entity_id=c["subscription_id"],
            whatsapp_en=_whatsapp_en(c, cust["customer_name"]),
            whatsapp_ar=_whatsapp_ar(c, cust["customer_name"]),
            sms_en=_sms_en(c, cust["first_name"]),
            sms_ar=_sms_ar(c, cust["first_name"]),
            extra_context={"payment_link": c["payment_url"]},
        )
        logger.info(
            "course_subscription_created_payment_link_processed",
            subscription_id=c["subscription_id"],
            channel=result,
        )

