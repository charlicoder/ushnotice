"""
Handler for ``course_subscription.session_consumed`` event.

Fired by ushbooknpay when a course redemption booking is completed or marked
no-show (both use up the session).  Sends "X of N sessions remaining" to the
customer (WhatsApp if verified, SMS fallback).  Dedup is per redemption booking,
so every consumed session produces exactly one message.
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


def remaining_sessions(c: dict) -> str:
    """Sessions not yet used (``total - used``); falls back to ``available_sessions``."""
    try:
        return str(max(int(c["total_sessions"]) - int(c["used_sessions"]), 0))
    except (TypeError, ValueError):
        return c["available_sessions"] or ""


def _whatsapp_en(c: dict, name: str, remaining: str) -> str:
    is_no_show = c["reason"] == "no_show"
    lines = [
        "🎟️ *Course Session Used* — USHSPA",
        "",
        f"Dear {name or 'Valued Customer'},",
        "",
        (
            f"Your session #{c['session_number']} of *{c['course_name']}* was marked as a no-show and has been used."
            if is_no_show
            else f"Thank you for visiting us! Session #{c['session_number']} of *{c['course_name']}* has been used."
        ),
        "",
    ]
    if remaining and c["total_sessions"]:
        lines.append(f"📊 *Remaining:* {remaining} of {c['total_sessions']} sessions")
    if c["expires_date"]:
        lines.append(f"📅 *Valid until:* {c['expires_date']}")
    if c["subscription_number"]:
        lines.append(f"🔖 *Ref:* {c['subscription_number']}")
    lines += ["", "— The USHSPA Team"]
    return "\n".join(lines)


def _whatsapp_ar(c: dict, name: str, remaining: str) -> str:
    is_no_show = c["reason"] == "no_show"
    lines = [
        "🎟️ *تم استخدام جلسة من الباقة* — USHSPA",
        "",
        f"عزيزي {name or 'العميل العزيز'}،",
        "",
        (
            f"تم احتساب الجلسة رقم {c['session_number']} من *{c['course_name']}* لعدم الحضور."
            if is_no_show
            else f"شكراً لزيارتك! تم استخدام الجلسة رقم {c['session_number']} من *{c['course_name']}*."
        ),
        "",
    ]
    if remaining and c["total_sessions"]:
        lines.append(f"📊 *المتبقي:* {remaining} من {c['total_sessions']} جلسات")
    if c["expires_date"]:
        lines.append(f"📅 *صالحة حتى:* {c['expires_date']}")
    if c["subscription_number"]:
        lines.append(f"🔖 *رقم المرجع:* {c['subscription_number']}")
    lines += ["", "— فريق USHSPA"]
    return "\n".join(lines)


def _sms_en(c: dict, first: str, remaining: str) -> str:
    rem = f" {remaining} of {c['total_sessions']} sessions remaining." if remaining and c["total_sessions"] else ""
    return f"USHSPA:\nHi {first or 'Customer'}, a session of your course {c['course_name']} was used.{rem}"


def _sms_ar(c: dict, first: str, remaining: str) -> str:
    rem = f" المتبقي {remaining} من {c['total_sessions']} جلسات." if remaining and c["total_sessions"] else ""
    return f"USHSPA:\nمرحباً {first or 'عميلنا'}، تم استخدام جلسة من باقتك {c['course_name']}.{rem}"


class CourseSubscriptionSessionConsumedHandler:
    """Processes ``course_subscription.session_consumed`` events."""

    event_type: str = "course_subscription.session_consumed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        c = course_context(data)
        cust = customer_info(data)
        remaining = remaining_sessions(c)
        # Dedup per consumed session: prefer the redemption booking id.
        entity_id = c["booking_id"] or c["subscription_id"]
        result = await send_course_notification(
            envelope=envelope,
            ctx=ctx,
            data=data,
            template_key="session_consumed",
            entity_id=entity_id,
            whatsapp_en=_whatsapp_en(c, cust["customer_name"], remaining),
            whatsapp_ar=_whatsapp_ar(c, cust["customer_name"], remaining),
            sms_en=_sms_en(c, cust["first_name"], remaining),
            sms_ar=_sms_ar(c, cust["first_name"], remaining),
            extra_context={"remaining_sessions": remaining},
        )
        logger.info(
            "course_subscription_session_consumed_processed",
            subscription_id=c["subscription_id"],
            booking_id=c["booking_id"],
            remaining=remaining,
            channel=result,
        )

