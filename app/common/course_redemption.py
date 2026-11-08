"""Helpers for course-subscription redemption bookings.

A course redemption booking is a booking that consumes one session of a
prepaid ``CourseSubscription`` (see ushbooknpay).  The course itself was paid
for — and invoiced — when the subscription was activated, so for these
bookings ushnotice must behave exactly like a gift-voucher redemption in every
money-related branch:

  • no Payment record (ushbooknpay ``POST /payments/``)
  • no booking invoice in ushanr
  • no loyalty credit / reversal
  • no refund request on cancellation
  • no "Total Paid" amount in customer-facing messages

ushbooknpay marks these bookings with ``booking_type="course_redemption"``,
``payment_type="course_subscription"``, ``payment_method="course_subscription"``,
``course_subscription_id`` and ``is_course_redemption=true``.  Any one of these
signals is enough so detection stays robust when a producer only sends a subset
(e.g. a partial booking snapshot embedded in another event).
"""

from __future__ import annotations

from typing import Any

COURSE_BOOKING_TYPE = "course_redemption"
COURSE_PAYMENT_TYPE = "course_subscription"


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _norm(value) in ("true", "1", "yes")


def _has_id(value: Any) -> bool:
    v = _norm(value)
    return bool(v) and v not in ("none", "null")


def is_course_redemption(data: dict[str, Any] | None) -> bool:
    """Return True when the booking event payload is a course-session redemption."""
    if not isinstance(data, dict):
        return False
    if _truthy(data.get("is_course_redemption")):
        return True
    if _norm(data.get("booking_type")) == COURSE_BOOKING_TYPE:
        return True
    if _norm(data.get("payment_type")) == COURSE_PAYMENT_TYPE:
        return True
    if _norm(data.get("payment_method")) == COURSE_PAYMENT_TYPE:
        return True
    if _has_id(data.get("course_subscription_id")):
        return True
    course_data = data.get("course_data")
    return isinstance(course_data, dict) and _has_id(course_data.get("subscription_id"))


def course_redemption_info(data: dict[str, Any]) -> dict[str, Any]:
    """Extract course name / session numbers from a redemption booking payload."""
    course_data: dict[str, Any] = data.get("course_data") if isinstance(data.get("course_data"), dict) else {}
    return {
        "course_name": str(data.get("course_name") or course_data.get("course_name") or ""),
        "course_subscription_number": str(
            data.get("course_subscription_number") or course_data.get("subscription_number") or ""
        ),
        "course_session_number": data.get("course_session_number") or course_data.get("session_number") or "",
        "course_total_sessions": data.get("course_total_sessions") or course_data.get("total_sessions") or "",
    }

