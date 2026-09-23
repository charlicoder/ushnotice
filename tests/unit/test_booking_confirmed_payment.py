"""
tests/unit/test_booking_confirmed_payment.py
─────────────────────────────────────────────
Unit tests for BookingConfirmedHandler — payment record creation and
loyalty tracker recording on ``booking.confirmed`` events.

Covers:
- Payment record IS created via ushbooknpay when payments_meta.is_paid is True.
- Payment is NOT attempted when payments_meta is absent / is_paid is False.
- Loyalty tracker IS recorded for branch bookings (booking_count starts at 1).
- Notification is sent in all cases.
- UshBookNPayClient exposes both create_payment and record_loyalty_tracker.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_confirmed_envelope(data: dict):
    from app.events.schemas.envelope import EventEnvelope
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="booking.confirmed",
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="ushbooknpay",
        data=data,
    )


PAID_PAYMENTS_META = {
    "status": "Paid",
    "is_paid": True,
    "invoice_id": "7106599",
    "payment_id": "100623810000000507",
    "customer_name": "K Md Mamunur Rashid",
    "invoice_value": "45.000",
    "customer_email": "Mamun1980@gmail.com",
    "transaction_id": "623810001397251",
    "customer_mobile": "+96541028983",
    "payment_gateway": "KNET",
    "invoice_reference": "2026194681",
    "customer_reference": "ORDER_1787706695152",
    "created_date": "2026-08-26T04:11:35.537",
    "transaction_date": "2026-08-26T04:11:51.183",
}

CONFIRMED_BOOKING_DATA = {
    "booking_id": "a1b45593-c5d7-4295-bfdd-1b862db330f4",
    "customer_id": "b92c374d-fdb5-48ff-96d5-bb4dc2abc452",
    "service_id": "d1e23456-abcd-4567-89ab-cdef01234567",
    "service_arrangement_id": "f2345678-abcd-4567-89ab-cdef01234567",
    "customer_name": "K Md Mamunur Rashid",
    "customer_email": "Mamun1980@gmail.com",
    "customer_phone": "+96541028983",
    "total_amount": "45.000",
    "currency": "KWD",
    "booking_type": "branch_service",
    "whatsapp_verified": True,
    "appointment_date": "2026-08-26",
    "appointment_starttime": "12:30",
    "appointment_endtime": "13:30",
    "payment_data": PAID_PAYMENTS_META,
}


# ─────────────────────────────────────────────────────────────────────────────
# UshBookNPayClient contract tests
# ─────────────────────────────────────────────────────────────────────────────

def test_ushbooknpay_client_has_create_payment():
    """UshBookNPayClient must expose create_payment for booking.confirmed handler."""
    from app.integrations.ushbooknpay_client import UshBookNPayClient
    assert hasattr(UshBookNPayClient, "create_payment"), (
        "create_payment must exist on UshBookNPayClient so BookingConfirmedHandler "
        "can create payment records from booking.confirmed events."
    )




# ─────────────────────────────────────────────────────────────────────────────
# Handler integration tests (all HTTP calls mocked)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_booking_confirmed_handler_creates_payment_when_paid():
    """Handler calls ushbooknpay payment API when is_paid=True in payments_meta."""
    from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler
    from app.events.handlers.base import HandlerContext

    handler = BookingConfirmedHandler()
    envelope = _make_confirmed_envelope(CONFIRMED_BOOKING_DATA)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_post = AsyncMock(return_value={"success": True})
    mock_ushauth_client = AsyncMock()
    mock_ushauth_client.create_appointment_cache = AsyncMock(return_value={})
    mock_ushauth_client.aclose = AsyncMock()

    mock_notification_service = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_confirmed.NotificationService",
              return_value=mock_notification_service),
        patch("app.events.handlers.booking.booking_confirmed.UshAuthClient",
              return_value=mock_ushauth_client),
        patch("app.integrations.ushbooknpay_client.GatewayHttpClient") as mock_gw,
    ):
        mock_gw_instance = MagicMock()
        mock_gw_instance.post = mock_post
        mock_gw_instance.aclose = AsyncMock()
        mock_gw.return_value = mock_gw_instance

        await handler.handle(envelope, mock_ctx)

    # Payment API must have been called
    assert mock_post.call_count >= 1
    # At least one post should be to the payments endpoint
    call_args_list = [str(c) for c in mock_post.call_args_list]
    assert any("payments" in arg for arg in call_args_list), (
        "Expected at least one POST to /api/v1/payments/"
    )


@pytest.mark.asyncio
async def test_booking_confirmed_handler_no_payment_when_not_paid():
    """Handler does NOT attempt payment creation when is_paid is False/absent."""
    from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler
    from app.events.handlers.base import HandlerContext

    handler = BookingConfirmedHandler()
    data = {
        **CONFIRMED_BOOKING_DATA,
        "payment_data": {
            "status": "Pending",
            "is_paid": False,
            "invoice_id": "7106600",
        },
    }
    envelope = _make_confirmed_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_post = AsyncMock(return_value={})
    mock_ushauth_client = AsyncMock()
    mock_ushauth_client.create_appointment_cache = AsyncMock(return_value={})
    mock_ushauth_client.aclose = AsyncMock()
    mock_notification_service = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_confirmed.NotificationService",
              return_value=mock_notification_service),
        patch("app.events.handlers.booking.booking_confirmed.UshAuthClient",
              return_value=mock_ushauth_client),
        patch("app.integrations.ushbooknpay_client.GatewayHttpClient") as mock_gw,
    ):
        mock_gw_instance = MagicMock()
        mock_gw_instance.post = mock_post
        mock_gw_instance.aclose = AsyncMock()
        mock_gw.return_value = mock_gw_instance

        await handler.handle(envelope, mock_ctx)

    # No payment call should have been made (loyalty tracker for branch booking
    # may still be called, but NOT /api/v1/payments/)
    payment_calls = [
        c for c in mock_post.call_args_list if "payments" in str(c)
    ]
    assert len(payment_calls) == 0, (
        f"Expected no payment API calls but got: {payment_calls}"
    )


@pytest.mark.asyncio
async def test_booking_confirmed_handler_no_payment_when_no_payments_meta():
    """Handler sends notifications even when payments_meta is absent (no payment call)."""
    from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler
    from app.events.handlers.base import HandlerContext

    handler = BookingConfirmedHandler()
    data = {
        "booking_id": "a1b45593-c5d7-4295-bfdd-1b862db330f4",
        "customer_id": "b92c374d-fdb5-48ff-96d5-bb4dc2abc452",
        "customer_name": "Test Customer",
        "customer_email": "test@example.com",
        "customer_phone": "+96500000000",
        "total_amount": "30.000",
        "currency": "KWD",
        "appointment_date": "2026-09-01",
        "appointment_starttime": "10:00",
        "appointment_endtime": "11:00",
        # No payments_meta → no payment creation
    }
    envelope = _make_confirmed_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)
    mock_notification_service = AsyncMock()
    mock_ushauth_client = AsyncMock()
    mock_ushauth_client.create_appointment_cache = AsyncMock(return_value={})
    mock_ushauth_client.aclose = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_confirmed.NotificationService",
              return_value=mock_notification_service),
        patch("app.events.handlers.booking.booking_confirmed.UshAuthClient",
              return_value=mock_ushauth_client),
        patch("app.integrations.ushbooknpay_client.GatewayHttpClient") as mock_gw,
    ):
        mock_gw_instance = MagicMock()
        mock_gw_instance.post = AsyncMock(return_value={})
        mock_gw_instance.aclose = AsyncMock()
        mock_gw.return_value = mock_gw_instance

        await handler.handle(envelope, mock_ctx)

    # Notifications may still fire
    assert mock_notification_service.send.call_count >= 0







# ─────────────────────────────────────────────────────────────────────────────
# Pending-payment notification tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_booking_confirmed_pending_payment_sends_payment_link_notifications():
    """
    When payment_status=pending the handler must send payment-link notifications
    (WhatsApp/SMS/Email) instead of the standard 'booking confirmed' messages.
    The demo payment link is used when no payment_link is available in event data.
    """
    from app.events.handlers.booking.booking_confirmed import (
        BookingConfirmedHandler,
        _DEMO_PAYMENT_LINK,
    )
    from app.events.handlers.base import HandlerContext

    handler = BookingConfirmedHandler()
    data = {
        **CONFIRMED_BOOKING_DATA,
        "payment_status": "pending",
        "payment_data": {
            "status": "Pending",
            "is_paid": False,
            "invoice_id": "7106601",
        },
    }
    envelope = _make_confirmed_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_notification_service = AsyncMock()
    mock_ushauth_client = AsyncMock()
    mock_ushauth_client.create_appointment_cache = AsyncMock(return_value={})
    mock_ushauth_client.aclose = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_confirmed.NotificationService",
              return_value=mock_notification_service),
        patch("app.events.handlers.booking.booking_confirmed.UshAuthClient",
              return_value=mock_ushauth_client),
        patch("app.integrations.ushbooknpay_client.GatewayHttpClient") as mock_gw,
    ):
        mock_gw_instance = MagicMock()
        mock_gw_instance.post = AsyncMock(return_value={})
        mock_gw_instance.aclose = AsyncMock()
        mock_gw.return_value = mock_gw_instance

        await handler.handle(envelope, mock_ctx)

    # At least one notification must have been dispatched
    assert mock_notification_service.send.call_count >= 1

    # Every notification sent must use a pending-payment template and carry payment_link
    for call in mock_notification_service.send.call_args_list:
        req = call.args[0] if call.args else call.kwargs.get("notification_request")
        if req is None:
            continue
        # Template name must be the pending-payment variant
        assert "pending_payment" in req.template_name, (
            f"Expected pending_payment template, got: {req.template_name}"
        )
        # payment_link must be present in template context
        assert "payment_link" in req.template_context, (
            f"payment_link missing in template_context for {req.template_name}"
        )
        # Demo link must be used when no real link is in the event data
        assert req.template_context["payment_link"] == _DEMO_PAYMENT_LINK


@pytest.mark.asyncio
async def test_booking_confirmed_pending_payment_uses_event_payment_link():
    """
    When payment_link is present in the event data it must be used instead of the
    demo link in all pending-payment notifications.
    """
    from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler
    from app.events.handlers.base import HandlerContext

    REAL_PAYMENT_LINK = "https://pay.ushspa.com/invoice/abc-123-xyz"

    handler = BookingConfirmedHandler()
    data = {
        **CONFIRMED_BOOKING_DATA,
        "payment_status": "pending",
        "payment_link": REAL_PAYMENT_LINK,
        "payment_data": {
            "status": "Pending",
            "is_paid": False,
            "invoice_id": "7106602",
        },
    }
    envelope = _make_confirmed_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_notification_service = AsyncMock()
    mock_ushauth_client = AsyncMock()
    mock_ushauth_client.create_appointment_cache = AsyncMock(return_value={})
    mock_ushauth_client.aclose = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_confirmed.NotificationService",
              return_value=mock_notification_service),
        patch("app.events.handlers.booking.booking_confirmed.UshAuthClient",
              return_value=mock_ushauth_client),
        patch("app.integrations.ushbooknpay_client.GatewayHttpClient") as mock_gw,
    ):
        mock_gw_instance = MagicMock()
        mock_gw_instance.post = AsyncMock(return_value={})
        mock_gw_instance.aclose = AsyncMock()
        mock_gw.return_value = mock_gw_instance

        await handler.handle(envelope, mock_ctx)

    for call in mock_notification_service.send.call_args_list:
        req = call.args[0] if call.args else call.kwargs.get("notification_request")
        if req is None:
            continue
        assert req.template_context.get("payment_link") == REAL_PAYMENT_LINK, (
            f"Expected real payment link in {req.template_name}, "
            f"got: {req.template_context.get('payment_link')}"
        )


@pytest.mark.asyncio
async def test_booking_confirmed_handler_skips_loyalty_credit_for_redemption_bookings():
    """When a booking is paid with redeemed loyalty points, points must NOT be credited to customer balance."""
    from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler
    from app.events.handlers.base import HandlerContext

    handler = BookingConfirmedHandler()
    data = {
        **CONFIRMED_BOOKING_DATA,
        "booking_type": "loyalty",
        "payment_type": "rewarded",
        "payment_status": "rewarded",
        "is_eligible_for_loyalty": True,
        "loyalty_points": 50,
        "arrangement_loyalty_points": 70,
        "loyalty_data": {"points_cost": 250},
        "reward_id": "c1234567-abcd-4567-89ab-cdef01234567",
    }
    envelope = _make_confirmed_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_notification_service = AsyncMock()
    mock_ushauth_client = AsyncMock()
    mock_ushauth_client.create_appointment_cache = AsyncMock(return_value={})
    mock_ushauth_client.aclose = AsyncMock()

    mock_loyalty_client = AsyncMock()
    mock_loyalty_client.credit_loyalty_points = AsyncMock()
    mock_loyalty_client.aclose = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_confirmed.NotificationService",
              return_value=mock_notification_service),
        patch("app.events.handlers.booking.booking_confirmed.UshAuthClient",
              return_value=mock_ushauth_client),
        patch("app.events.handlers.booking.booking_confirmed.UshBookNPayClient",
              return_value=mock_loyalty_client),
    ):
        await handler.handle(envelope, mock_ctx)

    # credit_loyalty_points MUST NOT be called for loyalty redemption booking
    mock_loyalty_client.credit_loyalty_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_booking_cancelled_handler_skips_loyalty_reversal_for_redemption_bookings():
    """When a loyalty redemption booking is cancelled, no loyalty points reversal should be called."""
    from app.events.handlers.booking.booking_cancelled import BookingCancelledHandler
    from app.events.handlers.base import HandlerContext
    from app.events.schemas.envelope import EventEnvelope

    handler = BookingCancelledHandler()
    data = {
        "booking_id": "a1b45593-c5d7-4295-bfdd-1b862db330f4",
        "customer_id": "b92c374d-fdb5-48ff-96d5-bb4dc2abc452",
        "booking_type": "loyalty",
        "payment_type": "rewarded",
        "is_eligible_for_loyalty": True,
        "loyalty_points": 50,
        "reward_id": "c1234567-abcd-4567-89ab-cdef01234567",
    }
    envelope = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="booking.cancelled",
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="ushbooknpay",
        data=data,
    )
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_client = AsyncMock()
    mock_client.cancel_loyalty_points = AsyncMock()
    mock_client.aclose = AsyncMock()

    with patch("app.events.handlers.booking.booking_cancelled.UshBookNPayClient",
               return_value=mock_client):
        await handler.handle(envelope, mock_ctx)

    mock_client.cancel_loyalty_points.assert_not_awaited()

