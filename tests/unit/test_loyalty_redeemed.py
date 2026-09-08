"""
tests/unit/test_loyalty_redeemed.py
────────────────────────────────────
Unit tests for LoyaltyRedeemedHandler in ushnotice.

Covers:
- Handler is registered in build_default_registry for both aliases
- Booking update is called with correct payload
- Appointment-cache is created from booking response data
- Missing booking_id skips all API calls
- Missing therapist_id/service_arrangement_id skips cache creation
- API failures are handled gracefully (non-blocking)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

BOOKING_ID = "b5555555-5555-5555-5555-555555555555"
REWARD_ID = "e405581a-1a5c-4be7-a76e-01213ebc7640"
CUSTOMER_ID = "c3333333-3333-3333-3333-333333333333"
THERAPIST_ID = "t7777777-7777-7777-7777-777777777777"
SERVICE_ARRANGEMENT_ID = "a8888888-8888-8888-8888-888888888888"
SERVICE_ID = "s4444444-4444-4444-4444-444444444444"


REWARD_DICT = {
    "id": REWARD_ID,
    "customer_id": CUSTOMER_ID,
    "service_id": SERVICE_ID,
    "service_arrangement_id": SERVICE_ARRANGEMENT_ID,
    "service_name": "24K Gold Luxury Rejuvenating Facial",
    "status": "redeemed",
    "earned_from_booking_id": "496a35f6-3b99-4a93-9f53-5ce98125d680",
    "redeemed_in_booking_id": BOOKING_ID,
    "redeemed_at": "2026-08-31T16:21:21.614703Z",
    "expires_at": "2026-10-30T16:21:21Z",
    "created_at": "2026-08-31T16:06:53.481671Z",
    "therapist_id": THERAPIST_ID,
    "appointment_date": "2026-09-01",
    "appointment_time": "10:30",
    "duration": 60,
}

REDEEMED_EVENT_DATA = {
    "reward": REWARD_DICT,
    "reward_id": REWARD_ID,
    "customer_id": CUSTOMER_ID,
    "service_id": SERVICE_ID,
    "service_arrangement_id": SERVICE_ARRANGEMENT_ID,
    "status": "redeemed",
    "redeemed_in_booking_id": BOOKING_ID,
    "therapist_id": THERAPIST_ID,
    "appointment_date": "2026-09-01",
    "appointment_time": "10:30",
    "duration": 60,
}

# Simulated ushbooknpay booking response after update.
# BookingDetailResponse returns appointment_start (ISO datetime), NOT separate
# appointment_date / appointment_starttime fields — matches real API shape.
BOOKING_RESPONSE = {
    "success": True,
    "data": {
        "id": BOOKING_ID,
        "customer_id": CUSTOMER_ID,
        "therapist_id": THERAPIST_ID,
        "service_id": SERVICE_ID,
        "service_arrangement_id": SERVICE_ARRANGEMENT_ID,
        "appointment_start": "2026-09-01T10:30:00+00:00",
        "appointment_end": "2026-09-01T11:30:00+00:00",
        "duration_minutes": 60,
        "booking_type": "branch",
        "branch_id": None,
        "customer_name": "Amina Ali",
        "status": "confirmed",
        "payment_status": "rewarded",
        "reward_id": REWARD_ID,
    },
}


def _make_loyalty_redeemed_envelope(data: dict):
    from app.events.schemas.envelope import EventEnvelope
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="loyalty.redeedmed",
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="ushbooknpay",
        data=data,
    )


# ── Registration tests ────────────────────────────────────────────────────────

def test_loyalty_redeemed_handler_is_registered():
    """LoyaltyRedeemedHandler is registered in build_default_registry."""
    from app.events.registry.handler_registry import build_default_registry
    registry = build_default_registry()
    assert registry.has_handler("loyalty.redeedmed")
    assert registry.has_handler("loyalty_redeedmed")


# ── Handler behaviour tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_loyalty_redeemed_updates_booking_and_creates_cache():
    """Handler updates booking then creates appointment-cache from response."""
    from app.events.handlers.loyalty.loyalty_redeemed import LoyaltyRedeemedHandler
    from app.events.handlers.base import HandlerContext

    handler = LoyaltyRedeemedHandler()
    envelope = _make_loyalty_redeemed_envelope(REDEEMED_EVENT_DATA)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_booknpay = AsyncMock()
    mock_booknpay.update_booking_loyalty_status = AsyncMock(return_value=BOOKING_RESPONSE)
    mock_booknpay.aclose = AsyncMock()

    mock_ushauth = AsyncMock()
    mock_ushauth.create_appointment_cache = AsyncMock(return_value={"success": True})
    mock_ushauth.aclose = AsyncMock()

    with (
        patch("app.events.handlers.loyalty.loyalty_redeemed.UshBookNPayClient", return_value=mock_booknpay),
        patch("app.events.handlers.loyalty.loyalty_redeemed.UshAuthClient", return_value=mock_ushauth),
    ):
        await handler.handle(envelope, mock_ctx)

    # Booking update assertions
    mock_booknpay.update_booking_loyalty_status.assert_awaited_once()
    call_kwargs = mock_booknpay.update_booking_loyalty_status.call_args
    assert call_kwargs.kwargs["booking_id"] == BOOKING_ID
    assert call_kwargs.kwargs["status"] == "confirmed"
    assert call_kwargs.kwargs["payment_status"] == "rewarded"
    assert call_kwargs.kwargs["reward_id"] == REWARD_ID
    assert call_kwargs.kwargs["loyalty_data"] == REWARD_DICT

    # Appointment-cache assertions
    mock_ushauth.create_appointment_cache.assert_awaited_once()
    cache_kwargs = mock_ushauth.create_appointment_cache.call_args.kwargs
    assert cache_kwargs["booking_id"] == BOOKING_ID
    assert cache_kwargs["therapist_id"] == THERAPIST_ID
    assert cache_kwargs["service_arrangement_id"] == SERVICE_ARRANGEMENT_ID
    assert cache_kwargs["status"] == "confirmed"
    assert cache_kwargs["payment_status"] == "success"  # ushauth only accepts: unpaid|success|pending|refunded|failed
    assert cache_kwargs["appointment_date"] == "2026-09-01"
    assert cache_kwargs["appointment_time"] == "10:30:00"   # padded to HH:MM:SS
    assert cache_kwargs["duration"] == 60


@pytest.mark.asyncio
async def test_loyalty_redeemed_skips_all_calls_when_no_booking_id():
    """Handler logs a warning and returns early when redeemed_in_booking_id is absent."""
    from app.events.handlers.loyalty.loyalty_redeemed import LoyaltyRedeemedHandler
    from app.events.handlers.base import HandlerContext

    handler = LoyaltyRedeemedHandler()
    data = {**REDEEMED_EVENT_DATA, "reward": {**REWARD_DICT, "redeemed_in_booking_id": None}, "redeemed_in_booking_id": None}
    envelope = _make_loyalty_redeemed_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_booknpay = AsyncMock()
    mock_ushauth = AsyncMock()

    with (
        patch("app.events.handlers.loyalty.loyalty_redeemed.UshBookNPayClient", return_value=mock_booknpay),
        patch("app.events.handlers.loyalty.loyalty_redeemed.UshAuthClient", return_value=mock_ushauth),
    ):
        await handler.handle(envelope, mock_ctx)

    mock_booknpay.update_booking_loyalty_status.assert_not_awaited()
    mock_ushauth.create_appointment_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_loyalty_redeemed_skips_cache_when_missing_therapist_id():
    """Cache creation is skipped when therapist_id is absent from both event and booking response."""
    from app.events.handlers.loyalty.loyalty_redeemed import LoyaltyRedeemedHandler
    from app.events.handlers.base import HandlerContext

    handler = LoyaltyRedeemedHandler()
    reward_no_therapist = {**REWARD_DICT, "therapist_id": None}
    data = {**REDEEMED_EVENT_DATA, "reward": reward_no_therapist, "therapist_id": None}
    envelope = _make_loyalty_redeemed_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    booking_resp_no_therapist = {
        "success": True,
        "data": {**BOOKING_RESPONSE["data"], "therapist_id": None},
    }

    mock_booknpay = AsyncMock()
    mock_booknpay.update_booking_loyalty_status = AsyncMock(return_value=booking_resp_no_therapist)
    mock_booknpay.aclose = AsyncMock()

    mock_ushauth = AsyncMock()
    mock_ushauth.create_appointment_cache = AsyncMock()
    mock_ushauth.aclose = AsyncMock()

    with (
        patch("app.events.handlers.loyalty.loyalty_redeemed.UshBookNPayClient", return_value=mock_booknpay),
        patch("app.events.handlers.loyalty.loyalty_redeemed.UshAuthClient", return_value=mock_ushauth),
    ):
        await handler.handle(envelope, mock_ctx)

    # Booking update should still happen
    mock_booknpay.update_booking_loyalty_status.assert_awaited_once()
    # Cache creation should be skipped
    mock_ushauth.create_appointment_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_loyalty_redeemed_booking_update_failure_is_non_blocking():
    """Handler continues to attempt cache creation even if booking update raises."""
    from app.events.handlers.loyalty.loyalty_redeemed import LoyaltyRedeemedHandler
    from app.events.handlers.base import HandlerContext

    handler = LoyaltyRedeemedHandler()
    envelope = _make_loyalty_redeemed_envelope(REDEEMED_EVENT_DATA)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_booknpay = AsyncMock()
    mock_booknpay.update_booking_loyalty_status = AsyncMock(
        side_effect=Exception("ushbooknpay connection refused")
    )
    mock_booknpay.aclose = AsyncMock()

    mock_ushauth = AsyncMock()
    mock_ushauth.create_appointment_cache = AsyncMock(return_value={"success": True})
    mock_ushauth.aclose = AsyncMock()

    with (
        patch("app.events.handlers.loyalty.loyalty_redeemed.UshBookNPayClient", return_value=mock_booknpay),
        patch("app.events.handlers.loyalty.loyalty_redeemed.UshAuthClient", return_value=mock_ushauth),
    ):
        # Should not raise
        await handler.handle(envelope, mock_ctx)

    # Cache attempt still runs (even with empty booking_response)
    mock_ushauth.create_appointment_cache.assert_awaited_once()
