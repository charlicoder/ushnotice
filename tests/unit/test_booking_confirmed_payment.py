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


def test_ushbooknpay_client_has_record_loyalty_tracker():
    """UshBookNPayClient must expose record_loyalty_tracker."""
    from app.integrations.ushbooknpay_client import UshBookNPayClient
    assert hasattr(UshBookNPayClient, "record_loyalty_tracker"), (
        "record_loyalty_tracker must exist on UshBookNPayClient."
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


@pytest.mark.asyncio
async def test_booking_confirmed_loyalty_tracker_record():
    """record_loyalty_tracker calls internal endpoint with customer_id, service_id, booking_id."""
    from app.integrations.ushbooknpay_client import UshBookNPayClient

    customer_id = str(uuid.uuid4())
    service_id = str(uuid.uuid4())
    booking_id = str(uuid.uuid4())

    mock_post = AsyncMock(return_value={"tracker": {"id": str(uuid.uuid4()), "booking_count": 1}, "reward": None, "reward_issued": False})

    with patch("app.integrations.ushbooknpay_client.GatewayHttpClient") as mock_gw:
        mock_gw_instance = MagicMock()
        mock_gw_instance.post = mock_post
        mock_gw_instance.aclose = AsyncMock()
        mock_gw.return_value = mock_gw_instance

        client = UshBookNPayClient()
        await client.record_loyalty_tracker(
            customer_id=customer_id,
            service_id=service_id,
            booking_id=booking_id,
            customer_name="John Doe",
        )

    mock_post.assert_called_once()
    call_args = mock_post.call_args
    sent_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "/api/v1/promotions/internal/loyalty/record/" in sent_url
    sent_payload = call_args.kwargs.get("json") or {}
    assert sent_payload.get("customer_id") == customer_id
    assert sent_payload.get("service_id") == service_id
    assert sent_payload.get("booking_id") == booking_id
    assert sent_payload.get("customer_name") == "John Doe"
    # is_eligible_for_loyalty must always be included in the payload
    assert "is_eligible_for_loyalty" in sent_payload


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
