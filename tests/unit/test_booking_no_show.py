import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import ServiceClientError
from app.events.handlers.booking.booking_no_show import BookingNoShowHandler
from app.events.handlers.booking.booking_updated import BookingUpdatedHandler
from app.events.registry.handler_registry import build_default_registry
from app.events.schemas.envelope import EventEnvelope

BOOKING_ID = "b1111111-1111-1111-1111-111111111111"
PATCH_CLIENT = "app.events.handlers.booking.booking_no_show.UshAuthClient"


def _envelope(data: dict | None = None) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="booking.no_show",
        source="ushbooknpay",
        occurred_at=datetime.now(timezone.utc),
        data={"booking_id": BOOKING_ID} if data is None else data,
    )


@pytest.mark.asyncio
async def test_no_show_deletes_appointment_cache():
    handler = BookingNoShowHandler()
    assert handler.event_type == "booking.no_show"
    envelope = _envelope()

    with patch(PATCH_CLIENT) as MockClient:
        client = AsyncMock()
        client.delete_appointment_cache_by_booking_id.return_value = {"deleted_count": 1}
        MockClient.return_value = client

        await handler.handle(envelope, ctx=None)

        client.delete_appointment_cache_by_booking_id.assert_awaited_once_with(
            booking_id=BOOKING_ID, correlation_id=envelope.correlation_id_str
        )
        client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_booking_id_is_skipped():
    with patch(PATCH_CLIENT) as MockClient:
        await BookingNoShowHandler().handle(_envelope({}), ctx=None)
        MockClient.assert_not_called()


@pytest.mark.asyncio
async def test_404_is_tolerated():
    with patch(PATCH_CLIENT) as MockClient:
        client = AsyncMock()
        client.delete_appointment_cache_by_booking_id.side_effect = ServiceClientError(
            "not found", service="ushauth", status_code=404
        )
        MockClient.return_value = client
        await BookingNoShowHandler().handle(_envelope(), ctx=None)
        client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_errors_propagate_for_retry():
    with patch(PATCH_CLIENT) as MockClient:
        client = AsyncMock()
        client.delete_appointment_cache_by_booking_id.side_effect = RuntimeError("boom")
        MockClient.return_value = client
        with pytest.raises(RuntimeError):
            await BookingNoShowHandler().handle(_envelope(), ctx=None)
        client.aclose.assert_awaited_once()


def test_registry_routes_no_show_to_dedicated_handler():
    registry = build_default_registry()
    for key in ("booking.no_show", "booking_no_show"):
        handlers = registry.resolve(key)
        assert len(handlers) == 1
        assert isinstance(handlers[0], BookingNoShowHandler)


def test_completed_keeps_cache_via_updated_handler_not_deletion():
    """Completed bookings stay on the schedule: handled by BookingUpdatedHandler (status update)."""
    registry = build_default_registry()
    for key in ("booking.completed", "booking_completed"):
        handlers = registry.resolve(key)
        assert len(handlers) == 1
        assert isinstance(handlers[0], BookingUpdatedHandler)

