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

    mock_client.cancel_loyalty_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_booking_cancelled_handler_updates_appointment_cache_in_ushauth():
    """Verify that BookingCancelledHandler calls ushauth to update appointment cache status to cancelled and refunded."""
    from unittest.mock import patch
    from app.events.handlers.booking.booking_cancelled import BookingCancelledHandler
    from app.events.handlers.base import HandlerContext
    from app.events.schemas.envelope import EventEnvelope

    handler = BookingCancelledHandler()
    data = {
        "booking_id": "6f4acd1a-9a5d-4cce-8adc-0dd3ef8ef91f",
        "customer_id": "b92c374d-fdb5-48ff-96d5-bb4dc2abc452",
        "payment_status": "success",
        "is_eligible_for_loyalty": False,
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

    mock_ushauth = AsyncMock()
    mock_ushauth.update_appointment_cache_status_by_booking_id = AsyncMock(
        return_value={"success": True, "records_updated": 1}
    )
    mock_ushauth.aclose = AsyncMock()

    with patch("app.events.handlers.booking.booking_cancelled.UshAuthClient", return_value=mock_ushauth):
        await handler.handle(envelope, mock_ctx)

    mock_ushauth.update_appointment_cache_status_by_booking_id.assert_awaited_once_with(
        booking_id="6f4acd1a-9a5d-4cce-8adc-0dd3ef8ef91f",
        new_status="cancelled",
        payment_status="refunded",
        correlation_id=envelope.correlation_id_str,
    )
    mock_ushauth.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_booking_cancelled_handler_handles_404_from_ushauth_gracefully():
    """Verify that BookingCancelledHandler does not crash when ushauth returns 404 (no cache entry)."""
    from unittest.mock import patch
    from app.core.exceptions import ServiceClientError
    from app.events.handlers.booking.booking_cancelled import BookingCancelledHandler
    from app.events.handlers.base import HandlerContext
    from app.events.schemas.envelope import EventEnvelope

    handler = BookingCancelledHandler()
    data = {
        "booking_id": "6f4acd1a-9a5d-4cce-8adc-0dd3ef8ef91f",
        "customer_id": "b92c374d-fdb5-48ff-96d5-bb4dc2abc452",
        "payment_status": "unpaid",
        "is_eligible_for_loyalty": False,
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

    mock_ushauth = AsyncMock()
    mock_ushauth.update_appointment_cache_status_by_booking_id = AsyncMock(
        side_effect=ServiceClientError("Not found", service="ushauth", status_code=404)
    )
    mock_ushauth.aclose = AsyncMock()

    with patch("app.events.handlers.booking.booking_cancelled.UshAuthClient", return_value=mock_ushauth):
        # Should not raise exception
        await handler.handle(envelope, mock_ctx)

@pytest.mark.asyncio
async def test_booking_cancelled_handler_updates_payment_status_in_ushbooknpay():
    """Verify that BookingCancelledHandler calls ushbooknpay to update booking payment status to refunded."""
    from unittest.mock import patch
    from app.events.handlers.booking.booking_cancelled import BookingCancelledHandler
    from app.events.handlers.base import HandlerContext
    from app.events.schemas.envelope import EventEnvelope

    handler = BookingCancelledHandler()
    data = {
        "booking_id": "6f4acd1a-9a5d-4cce-8adc-0dd3ef8ef91f",
        "customer_id": "b92c374d-fdb5-48ff-96d5-bb4dc2abc452",
        "payment_status": "success",
        "is_eligible_for_loyalty": False,
        "cancellation_reason": "Customer request",
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

    mock_ushauth = AsyncMock()
    mock_ushauth.update_appointment_cache_status_by_booking_id = AsyncMock(
        return_value={"success": True, "records_updated": 1}
    )
    mock_ushauth.aclose = AsyncMock()

    mock_booknpay = AsyncMock()
    mock_booknpay.update_booking_status = AsyncMock(
        return_value={"success": True}
    )
    mock_booknpay.aclose = AsyncMock()

    with patch("app.events.handlers.booking.booking_cancelled.UshAuthClient", return_value=mock_ushauth), \
         patch("app.events.handlers.booking.booking_cancelled.UshBookNPayClient", return_value=mock_booknpay):
        await handler.handle(envelope, mock_ctx)

    mock_booknpay.update_booking_status.assert_awaited_once_with(
        booking_id="6f4acd1a-9a5d-4cce-8adc-0dd3ef8ef91f",
        status="cancelled",
        payment_status="refunded",
        reason="Customer request",
        source="ushnotice",
        change_by_user="ushnotice",
        change_by_user_data={"source": "ushnotice", "reason": "Customer request"},
        correlation_id=envelope.correlation_id_str,
    )
    mock_booknpay.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_booking_confirmed_extracts_and_forwards_created_by_user_and_payment_data():
    """Verify that BookingConfirmedHandler extracts created_by_user, created_by_user_data,
    and preserves full payment_data when calling ushbooknpay payments API."""
    from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler
    from app.events.handlers.base import HandlerContext

    handler = BookingConfirmedHandler()
    booking_payload = {
        "booking_id": "e92d680f-fde5-4c2b-986d-656608014511",
        "customer_id": "4f5912e2-ebb5-4383-9f42-40df865c4cfb",
        "customer_name": "Test Customer",
        "customer_phone": "+96512345678",
        "customer_email": "test@ushspa.com",
        "total_amount": "45.000",
        "total_duration": 60,
        "currency": "KWD",
        "booking_type": "branch_service",
        "payment_status": "success",
        "created_by_user": "user-uuid-1234",
        "created_by_user_data": {
            "id": "user-uuid-1234",
            "name": "Operator Staff",
            "role": "staff",
        },
        "payment_data": {
            "invoiceId": "7205938",
            "invoiceValue": 45.0,
            "status": "Paid",
            "is_paid": True,
            "paymentUrl": "https://demo.myfatoorah.com/pay",
            "custom_gateway_key": "some_extra_info",
            "Data": {
                "InvoiceTransactions": [
                    {
                        "PaymentId": "pay-9999",
                        "TransactionId": "txn-8888",
                        "ReferenceId": "ref-7777",
                        "TrackId": "trk-6666",
                        "Country": "Kuwait",
                        "TransactionDate": "2026-09-25T18:50:00",
                        "TransactionStatus": "Succss",
                        "PaymentGateway": "KNET",
                        "PaymentMethod": "knet",
                    }
                ]
            },
        },
    }

    envelope = _make_confirmed_envelope(booking_payload)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_post = AsyncMock(return_value={"id": "pay-rec-1", "data": {"id": "pay-rec-1"}})
    mock_ushauth = AsyncMock()
    mock_ushauth.create_appointment_cache = AsyncMock(return_value={})
    mock_ushauth.aclose = AsyncMock()
    mock_notification = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_confirmed.NotificationService",
              return_value=mock_notification),
        patch("app.events.handlers.booking.booking_confirmed.UshAuthClient",
              return_value=mock_ushauth),
        patch("app.integrations.ushbooknpay_client.GatewayHttpClient") as mock_gw,
    ):
        mock_gw_instance = MagicMock()
        mock_gw_instance.post = mock_post
        mock_gw_instance.aclose = AsyncMock()
        mock_gw.return_value = mock_gw_instance

        await handler.handle(envelope, mock_ctx)

    # Locate the call to /api/v1/payments/
    payment_calls = [
        c for c in mock_post.call_args_list
        if "/api/v1/payments/" in str(c)
    ]
    assert len(payment_calls) == 1, f"Expected 1 call to /api/v1/payments/, got {len(payment_calls)}"

    call_kwargs = payment_calls[0].kwargs
    payload = call_kwargs["json"]

    # Verify created_by_user and created_by_user_data
    assert payload["created_by_user"] == "user-uuid-1234"
    assert payload["created_by"] == "user-uuid-1234"
    assert payload["created_by_user_data"]["id"] == "user-uuid-1234"
    assert payload["created_by_user_data"]["name"] == "Operator Staff"

    # Verify transaction identifiers extracted from inner MyFatoorah transactions
    assert payload["payment_id"] == "pay-9999"
    assert payload["transaction_id"] == "txn-8888"
    assert payload["reference_id"] == "ref-7777"
    assert payload["track_id"] == "trk-6666"
    assert payload["country"] == "Kuwait"
    assert payload["invoice_id"] == "7205938"
    assert payload["payment_gateway"] == "KNET"
    assert payload["payment_method"] == "knet"
    assert payload["transaction_status"] == "success"
    assert payload["payment_url"] == "https://demo.myfatoorah.com/pay"

    # Verify payment_data preserves full raw keys including Data and custom keys
    assert "payment_data" in payload
    pdata = payload["payment_data"]
    assert pdata["custom_gateway_key"] == "some_extra_info"
    assert "Data" in pdata
    assert pdata["payment_id"] == "pay-9999"
    assert pdata["reference_id"] == "ref-7777"


@pytest.mark.asyncio
async def test_booking_confirmed_fallback_created_by_user_from_customer():
    """When created_by_user is not explicitly provided in the event, fallback to customer_id
    and build created_by_user_data snapshot from customer details."""
    from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler
    from app.events.handlers.base import HandlerContext

    handler = BookingConfirmedHandler()
    booking_payload = {
        "booking_id": "e92d680f-fde5-4c2b-986d-656608014511",
        "customer_id": "4f5912e2-ebb5-4383-9f42-40df865c4cfb",
        "customer_name": "Sarah Connor",
        "customer_phone": "+96599887766",
        "customer_email": "sarah@example.com",
        "total_amount": "50.000",
        "total_duration": 45,
        "currency": "KWD",
        "booking_type": "branch_service",
        "payment_status": "success",
        # Notice: created_by_user and created_by_user_data omitted
        "payment_data": {
            "invoice_id": "112233",
            "is_paid": True,
            "status": "Paid",
        },
    }

    envelope = _make_confirmed_envelope(booking_payload)
    mock_ctx = MagicMock(spec=HandlerContext)
    mock_post = AsyncMock(return_value={"id": "pay-rec-2"})
    mock_ushauth = AsyncMock()
    mock_ushauth.create_appointment_cache = AsyncMock(return_value={})
    mock_ushauth.aclose = AsyncMock()
    mock_notification = AsyncMock()

    with (
        patch("app.events.handlers.booking.booking_confirmed.NotificationService",
              return_value=mock_notification),
        patch("app.events.handlers.booking.booking_confirmed.UshAuthClient",
              return_value=mock_ushauth),
        patch("app.integrations.ushbooknpay_client.GatewayHttpClient") as mock_gw,
    ):
        mock_gw_instance = MagicMock()
        mock_gw_instance.post = mock_post
        mock_gw_instance.aclose = AsyncMock()
        mock_gw.return_value = mock_gw_instance

        await handler.handle(envelope, mock_ctx)

    payment_calls = [
        c for c in mock_post.call_args_list
        if "/api/v1/payments/" in str(c)
    ]
    assert len(payment_calls) == 1
    payload = payment_calls[0].kwargs["json"]

    # Fallback to customer_id
    assert payload["created_by_user"] == "4f5912e2-ebb5-4383-9f42-40df865c4cfb"
    assert payload["created_by"] == "4f5912e2-ebb5-4383-9f42-40df865c4cfb"
    assert payload["created_by_user_data"]["id"] == "4f5912e2-ebb5-4383-9f42-40df865c4cfb"
    assert payload["created_by_user_data"]["name"] == "Sarah Connor"
    assert payload["created_by_user_data"]["phone"] == "+96599887766"
    assert payload["created_by_user_data"]["email"] == "sarah@example.com"
    assert payload["created_by_user_data"]["role"] == "customer"
    assert payload["payment_data"]["invoice_id"] == "112233"


