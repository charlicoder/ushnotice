"""
tests/unit/test_course_subscription_handlers.py
────────────────────────────────────────────────
Unit tests for the ``course_subscription.*`` notification handlers.

Covers:
- activated → purchase confirmation with course name, sessions, expiry, invoice.
- session_consumed → "X of N sessions remaining".
- expired / refunded → customer notification.
- created → payment link only for payment_option=payment_link with payment_url.
- No-op handlers send nothing.
- Business-level idempotency (already-sent notification → no re-send).
- Registry maps every course event type (dot + underscore aliases).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SUB_ID = "c0ffee00-0000-4000-8000-000000000001"

CUSTOMER_EN = {
    "id": "b92c374d-fdb5-48ff-96d5-bb4dc2abc452",
    "name": "Sara Ahmed",
    "first_name": "Sara",
    "last_name": "Ahmed",
    "phone_number": "+96541028983",
    "email": "sara@example.com",
    "language_preference": "en",
}

BASE = {
    "subscription_id": SUB_ID,
    "subscription_number": "CRS/2026/11/000001",
    "customer_id": CUSTOMER_EN["id"],
    "customer_data": CUSTOMER_EN,
    "course_id": "11111111-0000-4000-8000-000000000001",
    "course_code": "COURSE-001",
    "course_name": "Relaxation Package",
    "service_name": "Relaxing Massage",
    "total_sessions": 5,
    "price": "100.000",
    "currency": "KWD",
}

ACTIVATED = {
    **BASE,
    "payment_option": "pay_at_desk",
    "payment_url": None,
    "payment_number": "PMT/2026/11/000010",
    "invoice_number": "INV/2026/00042",
    "activated_at": "2026-11-06T10:00:00+00:00",
    "expires_at": "2027-02-04T10:00:00+00:00",
}


def _envelope(event_type: str, data: dict):
    from app.events.schemas.envelope import EventEnvelope

    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type=event_type,
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="ushbooknpay",
        data=data,
    )


def _ctx(already_sent: bool = False):
    from app.events.handlers.base import HandlerContext

    ctx = MagicMock(spec=HandlerContext)
    repo = AsyncMock()
    repo.has_booking_notification = AsyncMock(return_value=already_sent)
    ctx.notification_repo = repo
    return ctx


async def _run(handler, event_type: str, data: dict, *, already_sent: bool = False):
    service = AsyncMock()
    service.send = AsyncMock(return_value=MagicMock(status="SENT"))
    ctx = _ctx(already_sent)
    with patch("app.events.handlers.course._common.NotificationService", return_value=service):
        await handler.handle(_envelope(event_type, data), ctx)
    return [c.args[0] for c in service.send.await_args_list], ctx


# ── activated ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activated_sends_sms_with_course_details():
    from app.events.handlers.course.course_subscription_activated import (
        CourseSubscriptionActivatedHandler,
    )

    reqs, _ = await _run(CourseSubscriptionActivatedHandler(), "course_subscription.activated", ACTIVATED)

    assert len(reqs) == 1
    req = reqs[0]
    assert req.template_name == "course_subscription/activated_sms"
    assert req.recipient.address == "+96541028983"
    assert req.recipient.language == "en"
    assert req.booking_id == SUB_ID
    body = req.template_context["message_body"]
    assert "Relaxation Package" in body
    assert "5 sessions" in body
    assert "2027-02-04" in body
    assert "INV/2026/00042" in body


@pytest.mark.asyncio
async def test_activated_uses_whatsapp_when_verified_and_arabic_by_default():
    from app.events.handlers.course.course_subscription_activated import (
        CourseSubscriptionActivatedHandler,
    )

    customer = {k: v for k, v in CUSTOMER_EN.items() if k != "language_preference"}
    data = {**ACTIVATED, "customer_data": {**customer, "whatsapp_verified": True}}
    with patch(
        "app.notifications.application.channel_resolver.get_settings",
        return_value=MagicMock(DEFAULT_LANGUAGE="ar"),
    ):
        reqs, _ = await _run(CourseSubscriptionActivatedHandler(), "course_subscription.activated", data)

    assert len(reqs) == 1
    req = reqs[0]
    assert req.template_name == "course_subscription/activated_whatsapp"
    assert req.recipient.language == "ar"
    body = req.template_context["message_body"]
    assert "تم تأكيد شراء الباقة" in body
    assert "INV/2026/00042" in body
    assert "Relaxation Package" in body


@pytest.mark.asyncio
async def test_activated_is_idempotent_when_already_sent():
    from app.events.handlers.course.course_subscription_activated import (
        CourseSubscriptionActivatedHandler,
    )

    reqs, ctx = await _run(
        CourseSubscriptionActivatedHandler(), "course_subscription.activated", ACTIVATED, already_sent=True
    )

    assert reqs == []
    ctx.notification_repo.has_booking_notification.assert_awaited_once()
    kwargs = ctx.notification_repo.has_booking_notification.await_args.kwargs
    assert kwargs["booking_id"] == SUB_ID
    assert kwargs["template_prefix"] == "course_subscription/activated"


@pytest.mark.asyncio
async def test_activated_without_phone_sends_nothing():
    from app.events.handlers.course.course_subscription_activated import (
        CourseSubscriptionActivatedHandler,
    )

    data = {**ACTIVATED, "customer_data": {"name": "No Phone", "language_preference": "en"}}
    reqs, _ = await _run(CourseSubscriptionActivatedHandler(), "course_subscription.activated", data)
    assert reqs == []


# ── session_consumed ──────────────────────────────────────────────────────────

SESSION_CONSUMED = {
    **BASE,
    "booking_id": "a1b45593-c5d7-4295-bfdd-1b862db330f4",
    "booking_number": "BOK/2026/11/000123",
    "session_number": 2,
    "appointment_start": "2026-11-20T09:30:00+00:00",
    "available_sessions": 2,
    "used_sessions": 2,
    "total_sessions": 5,
    "reason": "completed",
}


@pytest.mark.asyncio
async def test_session_consumed_sends_remaining_sessions():
    from app.events.handlers.course.course_subscription_session_consumed import (
        CourseSubscriptionSessionConsumedHandler,
    )

    reqs, ctx = await _run(
        CourseSubscriptionSessionConsumedHandler(), "course_subscription.session_consumed", SESSION_CONSUMED
    )

    assert len(reqs) == 1
    req = reqs[0]
    assert req.template_name == "course_subscription/session_consumed_sms"
    assert "3 of 5 sessions remaining" in req.template_context["message_body"]
    # Dedup is per redemption booking, so each consumed session gets one message.
    assert req.booking_id == SESSION_CONSUMED["booking_id"]
    assert ctx.notification_repo.has_booking_notification.await_args.kwargs["booking_id"] == (
        SESSION_CONSUMED["booking_id"]
    )


@pytest.mark.asyncio
async def test_session_consumed_is_idempotent():
    from app.events.handlers.course.course_subscription_session_consumed import (
        CourseSubscriptionSessionConsumedHandler,
    )

    reqs, _ = await _run(
        CourseSubscriptionSessionConsumedHandler(),
        "course_subscription.session_consumed",
        SESSION_CONSUMED,
        already_sent=True,
    )
    assert reqs == []


def test_session_consumed_no_show_whatsapp_wording():
    from app.events.handlers.course._common import course_context
    from app.events.handlers.course.course_subscription_session_consumed import (
        _whatsapp_en,
        remaining_sessions,
    )

    c = course_context({**SESSION_CONSUMED, "reason": "no_show"})
    body = _whatsapp_en(c, "Sara", remaining_sessions(c))
    assert "no-show" in body
    assert "3 of 5 sessions" in body


# ── expired / refunded ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expired_sends_notification_with_lost_sessions():
    from app.events.handlers.course.course_subscription_expired import CourseSubscriptionExpiredHandler

    data = {**BASE, "expires_at": "2027-02-04T10:00:00+00:00", "lost_sessions": 3}
    reqs, _ = await _run(CourseSubscriptionExpiredHandler(), "course_subscription.expired", data)

    assert len(reqs) == 1
    assert reqs[0].template_name == "course_subscription/expired_sms"
    body = reqs[0].template_context["message_body"]
    assert "expired" in body
    assert "3 unbooked session(s)" in body


@pytest.mark.asyncio
async def test_refunded_sends_notification():
    from app.events.handlers.course.course_subscription_refunded import CourseSubscriptionRefundedHandler

    data = {**BASE, "refund_id": "r-1", "credit_note_number": "RINV/2026/00003"}
    reqs, _ = await _run(CourseSubscriptionRefundedHandler(), "course_subscription.refunded", data)

    assert len(reqs) == 1
    assert reqs[0].template_name == "course_subscription/refunded_sms"
    body = reqs[0].template_context["message_body"]
    assert "refunded" in body
    assert "100.000 KWD" in body


@pytest.mark.asyncio
async def test_refunded_is_idempotent():
    from app.events.handlers.course.course_subscription_refunded import CourseSubscriptionRefundedHandler

    reqs, _ = await _run(
        CourseSubscriptionRefundedHandler(), "course_subscription.refunded", BASE, already_sent=True
    )
    assert reqs == []


# ── created ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_created_payment_link_sends_link():
    from app.events.handlers.course.course_subscription_created import CourseSubscriptionCreatedHandler

    data = {**BASE, "payment_option": "payment_link", "payment_url": "https://pay.example.com/abc"}
    reqs, _ = await _run(CourseSubscriptionCreatedHandler(), "course_subscription.created", data)

    assert len(reqs) == 1
    req = reqs[0]
    assert req.template_name == "course_subscription/created_payment_link_sms"
    assert "https://pay.example.com/abc" in req.template_context["message_body"]
    assert req.template_context["payment_link"] == "https://pay.example.com/abc"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"payment_option": "pay_at_desk", "payment_url": None},
        {"payment_option": "pay_at_desk", "payment_url": "https://pay.example.com/abc"},
        {"payment_option": "payment_link", "payment_url": None},
        {"payment_option": "payment_link", "payment_url": ""},
    ],
)
async def test_created_without_payment_link_sends_nothing(extra):
    from app.events.handlers.course.course_subscription_created import CourseSubscriptionCreatedHandler

    reqs, ctx = await _run(CourseSubscriptionCreatedHandler(), "course_subscription.created", {**BASE, **extra})
    assert reqs == []
    ctx.notification_repo.has_booking_notification.assert_not_awaited()


# ── no-op handlers ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        "course_subscription.payment_failed",
        "course_subscription.invoice_created",
        "course_subscription.session_reserved",
        "course_subscription.session_released",
    ],
)
async def test_noop_handlers_send_nothing(event_type):
    from app.events.registry.handler_registry import build_default_registry

    registry = build_default_registry()
    (handler,) = registry.resolve(event_type)
    reqs, _ = await _run(handler, event_type, {**BASE, "reason": "x"})
    assert reqs == []


# ── registry ──────────────────────────────────────────────────────────────────

COURSE_EVENT_HANDLERS = {
    "course_subscription.created": "CourseSubscriptionCreatedHandler",
    "course_subscription.activated": "CourseSubscriptionActivatedHandler",
    "course_subscription.payment_failed": "CourseSubscriptionNoopHandler",
    "course_subscription.invoice_created": "CourseSubscriptionNoopHandler",
    "course_subscription.session_reserved": "CourseSubscriptionNoopHandler",
    "course_subscription.session_consumed": "CourseSubscriptionSessionConsumedHandler",
    "course_subscription.session_released": "CourseSubscriptionNoopHandler",
    "course_subscription.expired": "CourseSubscriptionExpiredHandler",
    "course_subscription.refunded": "CourseSubscriptionRefundedHandler",
}


@pytest.mark.parametrize("event_type,handler_name", sorted(COURSE_EVENT_HANDLERS.items()))
def test_registry_maps_course_event_types(event_type, handler_name):
    from app.events.registry.handler_registry import build_default_registry

    registry = build_default_registry()
    for alias in (
        event_type,
        event_type.replace(".", "_"),
        event_type.replace("course_subscription.", "coursesubscription_"),
    ):
        handlers = registry.resolve(alias)
        assert len(handlers) == 1, alias
        assert type(handlers[0]).__name__ == handler_name

