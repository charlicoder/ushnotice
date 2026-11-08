"""
tests/unit/test_course_redemption_bookings.py
──────────────────────────────────────────────
Course-subscription redemption bookings must behave like gift-voucher
redemptions in every money-related branch of the booking handlers:

- booking.confirmed → no Payment record (POST /payments/), no invoice, no
  loyalty credit; the confirmation message never shows a paid amount.
- booking.cancelled → no refund request, no loyalty reversal; the ushauth cache
  is still deleted and the status is still synced.
- payment_link.paid for a course_subscription target → no booking side-effects.
- booking.updated_payment_status_to_success → no invoice.
- trigger_booking_invoice guard → returns before calling ushanr.
- Normal and gift-voucher bookings keep their existing behaviour.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.course_redemption import course_redemption_info, is_course_redemption


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


def _ctx():
    from app.events.handlers.base import HandlerContext

    ctx = MagicMock(spec=HandlerContext)
    repo = AsyncMock()
    repo.has_booking_notification = AsyncMock(return_value=False)
    ctx.notification_repo = repo
    return ctx


BASE_BOOKING = {
    "booking_id": "a1b45593-c5d7-4295-bfdd-1b862db330f4",
    "booking_number": "BOK/2026/11/000123",
    "customer_id": "b92c374d-fdb5-48ff-96d5-bb4dc2abc452",
    "service_id": "d1e23456-abcd-4567-89ab-cdef01234567",
    "service_name": "Relaxing Massage",
    "service_arrangement_id": "f2345678-abcd-4567-89ab-cdef01234567",
    "customer_name": "Sara Ahmed",
    "customer_phone": "+96541028983",
    "currency": "KWD",
    "appointment_date": "2026-11-20",
    "appointment_starttime": "12:30",
    "appointment_endtime": "13:30",
    "whatsapp_verified": False,
    "is_eligible_for_loyalty": True,
    "loyalty_points": 50,
    "payment_status": "success",
}

COURSE_BOOKING = {
    **BASE_BOOKING,
    "booking_type": "course_redemption",
    "payment_type": "course_subscription",
    "payment_method": "course_subscription",
    "payment_provider": "Course Subscription",
    "payment_through": "ushdesk",
    "course_subscription_id": "c0ffee00-0000-4000-8000-000000000001",
    "course_subscription_number": "CRS/2026/11/000001",
    "course_session_number": 2,
    "is_course_redemption": True,
    "is_eligible_for_loyalty": False,
    "total_amount": "0.000",
    "course_data": {
        "subscription_id": "c0ffee00-0000-4000-8000-000000000001",
        "subscription_number": "CRS/2026/11/000001",
        "course_id": "11111111-0000-4000-8000-000000000001",
        "course_code": "COURSE-001",
        "course_name": "Relaxation Package",
        "session_number": 2,
        "total_sessions": 5,
    },
}

NORMAL_PAID_BOOKING = {
    **BASE_BOOKING,
    "booking_type": "branch",
    "total_amount": "45.000",
    "payment_data": {"status": "success", "is_paid": True, "payment_id": "pay-1"},
}

GIFT_VOUCHER_BOOKING = {
    **BASE_BOOKING,
    "booking_type": "branch",
    "payment_type": "gift_voucher",
    "payment_method": "gift_voucher",
    "voucher_id": "b20e155d-6070-49c4-bdf2-e1eb2b6b15f1",
    "total_amount": "45.000",
}


# ── Detection helper ──────────────────────────────────────────────────────────

class TestIsCourseRedemption:
    def test_full_payload(self):
        assert is_course_redemption(COURSE_BOOKING) is True

    @pytest.mark.parametrize(
        "data",
        [
            {"is_course_redemption": True},
            {"is_course_redemption": "true"},
            {"booking_type": "course_redemption"},
            {"payment_type": "COURSE_SUBSCRIPTION"},
            {"payment_method": "course_subscription"},
            {"course_subscription_id": "c0ffee00-0000-4000-8000-000000000001"},
            {"course_data": {"subscription_id": "c0ffee00-0000-4000-8000-000000000001"}},
        ],
    )
    def test_any_single_signal(self, data):
        assert is_course_redemption(data) is True

    @pytest.mark.parametrize(
        "data",
        [
            NORMAL_PAID_BOOKING,
            GIFT_VOUCHER_BOOKING,
            {"course_subscription_id": None, "is_course_redemption": False},
            {"course_subscription_id": "None"},
            {},
            None,
        ],
    )
    def test_non_course(self, data):
        assert is_course_redemption(data) is False

    def test_info_reads_course_data(self):
        info = course_redemption_info(COURSE_BOOKING)
        assert info["course_name"] == "Relaxation Package"
        assert info["course_session_number"] == 2
        assert info["course_total_sessions"] == 5
        assert info["course_subscription_number"] == "CRS/2026/11/000001"


# ── booking.confirmed ─────────────────────────────────────────────────────────

async def _run_confirmed(data: dict):
    from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler

    ushauth = AsyncMock()
    ushauth.create_appointment_cache = AsyncMock(return_value={})
    ushauth.aclose = AsyncMock()

    booknpay = MagicMock()
    booknpay._client.post = AsyncMock(return_value={"success": True})
    booknpay.credit_loyalty_points = AsyncMock()
    booknpay.aclose = AsyncMock()

    notification_service = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_confirmed.NotificationService",
              return_value=notification_service),
        patch("app.events.handlers.booking.booking_confirmed.UshAuthClient", return_value=ushauth),
        patch("app.events.handlers.booking.booking_confirmed.UshBookNPayClient",
              return_value=booknpay) as booknpay_cls,
        patch("app.events.handlers.booking.booking_confirmed.trigger_booking_invoice",
              new_callable=AsyncMock) as trigger_invoice,
    ):
        await BookingConfirmedHandler().handle(_envelope("booking.confirmed", data), _ctx())

    return {
        "ushauth": ushauth,
        "booknpay": booknpay,
        "booknpay_cls": booknpay_cls,
        "trigger_invoice": trigger_invoice,
        "notification_service": notification_service,
    }


@pytest.mark.asyncio
async def test_confirmed_course_redemption_creates_no_payment_invoice_or_loyalty():
    r = await _run_confirmed(COURSE_BOOKING)

    r["booknpay"]._client.post.assert_not_awaited()
    r["booknpay"].credit_loyalty_points.assert_not_awaited()
    r["booknpay_cls"].assert_not_called()
    r["trigger_invoice"].assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_course_redemption_still_caches_and_notifies_without_amount():
    r = await _run_confirmed(COURSE_BOOKING)

    # Appointment cache is still created, with an ushauth-valid booking type.
    r["ushauth"].create_appointment_cache.assert_awaited_once()
    assert r["ushauth"].create_appointment_cache.await_args.kwargs["booking_type"] == "branch"

    # Exactly one confirmation SMS, mentioning the course session but no amount.
    sends = r["notification_service"].send.await_args_list
    sms = [c.args[0] for c in sends if c.args[0].template_name == "booking/confirmed_sms"]
    assert len(sms) == 1
    ctx = sms[0].template_context
    assert ctx["total_amount"] == ""
    assert "Relaxation Package" in ctx["message_body"]
    assert "2/5" in ctx["message_body"]
    assert "Relaxation Package (2/5)" in ctx["service_name"]


@pytest.mark.asyncio
async def test_confirmed_course_redemption_whatsapp_has_no_paid_line():
    from app.events.handlers.booking.booking_confirmed import (
        _build_booking_context,
        _whatsapp_message,
        _whatsapp_message_ar,
    )

    context = _build_booking_context(COURSE_BOOKING)
    en = _whatsapp_message(context, "Sara")
    ar = _whatsapp_message_ar(context, "Sara")
    assert "Total Paid" not in en
    assert "المبلغ المدفوع" not in ar
    assert "Relaxation Package" in en and "Relaxation Package" in ar


@pytest.mark.asyncio
async def test_confirmed_normal_paid_booking_unchanged():
    r = await _run_confirmed(NORMAL_PAID_BOOKING)

    payment_calls = [c for c in r["booknpay"]._client.post.await_args_list if "/api/v1/payments/" in str(c)]
    assert len(payment_calls) == 1
    r["booknpay"].credit_loyalty_points.assert_awaited_once()
    r["trigger_invoice"].assert_awaited_once()


@pytest.mark.asyncio
async def test_confirmed_gift_voucher_booking_unchanged():
    r = await _run_confirmed(GIFT_VOUCHER_BOOKING)

    r["booknpay"]._client.post.assert_not_awaited()
    r["booknpay"].credit_loyalty_points.assert_not_awaited()
    r["trigger_invoice"].assert_not_awaited()
    # Gift-voucher redemptions still skip the generic WhatsApp/SMS.
    sends = r["notification_service"].send.await_args_list
    assert not [c for c in sends if c.args[0].template_name.startswith("booking/confirmed_sms")]


# ── booking.cancelled ─────────────────────────────────────────────────────────

async def _run_cancelled(data: dict):
    from app.events.handlers.booking.booking_cancelled import BookingCancelledHandler

    ushauth = AsyncMock()
    ushauth.delete_appointment_cache_by_booking_id = AsyncMock(return_value={"deleted_count": 1})
    ushauth.aclose = AsyncMock()

    booknpay = AsyncMock()
    booknpay.cancel_loyalty_points = AsyncMock()
    booknpay.create_refund_request = AsyncMock()
    booknpay.update_booking_status = AsyncMock()
    booknpay.cancel_payment_link = AsyncMock()
    booknpay.get_booking = AsyncMock(return_value={})
    booknpay.aclose = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_cancelled.UshAuthClient", return_value=ushauth),
        patch("app.events.handlers.booking.booking_cancelled.UshBookNPayClient", return_value=booknpay),
    ):
        await BookingCancelledHandler().handle(_envelope("booking.cancelled", data), _ctx())
    return ushauth, booknpay


@pytest.mark.asyncio
async def test_cancelled_course_redemption_no_refund_no_loyalty_reversal():
    data = {
        **COURSE_BOOKING,
        "is_eligible_for_loyalty": True,  # even if a producer sends it, no reversal
        "refund_required": True,
        "refund_amount": "0.000",
    }
    ushauth, booknpay = await _run_cancelled(data)

    booknpay.cancel_loyalty_points.assert_not_awaited()
    booknpay.create_refund_request.assert_not_awaited()
    booknpay.cancel_payment_link.assert_not_awaited()
    # Cache cleanup + status sync still happen.
    ushauth.delete_appointment_cache_by_booking_id.assert_awaited_once()
    booknpay.update_booking_status.assert_awaited_once()
    assert booknpay.update_booking_status.await_args.kwargs["status"] == "cancelled"
    assert booknpay.update_booking_status.await_args.kwargs["payment_status"] == "success"


@pytest.mark.asyncio
async def test_cancelled_normal_paid_booking_still_requests_refund_and_reverses_loyalty():
    data = {**NORMAL_PAID_BOOKING, "refund_required": True, "refund_amount": "45.000"}
    _, booknpay = await _run_cancelled(data)

    booknpay.cancel_loyalty_points.assert_awaited_once()
    booknpay.create_refund_request.assert_awaited_once()


# ── payment_link.paid ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payment_link_paid_course_subscription_target_has_no_booking_side_effects():
    from app.events.handlers.payment.payment_link_paid import PaymentLinkPaidHandler

    data = {
        "payment_link_id": "pl-1",
        "target_type": "course_subscription",
        "target_id": "c0ffee00-0000-4000-8000-000000000001",
        "amount": "100.000",
    }
    with (
        patch("app.events.handlers.payment.payment_link_paid.UshBookNPayClient") as booknpay_cls,
        patch("app.events.handlers.payment.payment_link_paid.UshAuthClient") as ushauth_cls,
        patch("app.events.handlers.payment.payment_link_paid.UshanrClient") as ushanr_cls,
        patch("app.events.handlers.payment.payment_link_paid.BookingConfirmedHandler") as confirmed_cls,
    ):
        await PaymentLinkPaidHandler().handle(_envelope("payment_link.paid", data), _ctx())

    booknpay_cls.assert_not_called()
    ushauth_cls.assert_not_called()
    ushanr_cls.assert_not_called()
    confirmed_cls.assert_not_called()


# ── booking.updated_payment_status_to_success ─────────────────────────────────

@pytest.mark.asyncio
async def test_payment_status_success_course_redemption_skips_invoice():
    from app.events.handlers.booking.booking_payment_status_success import (
        BookingPaymentStatusSuccessHandler,
    )

    ushauth = AsyncMock()
    ushauth.update_appointment_cache_payment_status = AsyncMock(return_value={"records_updated": 1})
    ushauth.aclose = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_payment_status_success.UshAuthClient",
              return_value=ushauth),
        patch("app.events.handlers.booking.booking_payment_status_success.trigger_booking_invoice",
              new_callable=AsyncMock) as trigger_invoice,
        patch("app.events.handlers.booking.booking_confirmed.BookingConfirmedHandler") as confirmed_cls,
    ):
        confirmed_cls.return_value.handle = AsyncMock()
        await BookingPaymentStatusSuccessHandler().handle(
            _envelope("booking.updated_payment_status_to_success", COURSE_BOOKING), _ctx()
        )

    trigger_invoice.assert_not_awaited()


# ── trigger_booking_invoice guard ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_booking_invoice_skips_course_redemption():
    from app.invoicing import triggers

    with (
        patch.object(triggers, "_invoicing_enabled", return_value=True),
        patch.object(triggers, "UshanrClient") as ushanr_cls,
        patch.object(triggers, "build_booking_invoice_lines") as build_lines,
    ):
        await triggers.trigger_booking_invoice(COURSE_BOOKING, correlation_id="c-1")

    ushanr_cls.assert_not_called()
    build_lines.assert_not_called()

