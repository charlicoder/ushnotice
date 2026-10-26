import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from app.events.handlers.booking.booking_updated import BookingUpdatedHandler
from app.events.schemas.envelope import EventEnvelope


@pytest.mark.asyncio
async def test_booking_updated_handler_calls_ushauth():
    handler = BookingUpdatedHandler()
    assert handler.event_type == "booking.updated"

    envelope = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="booking.updated",
        source="ushbooknpay",
        occurred_at=datetime.now(timezone.utc),
        data={
            "booking_id": "b1111111-1111-1111-1111-111111111111",
            "appointment_date": "2026-10-05",
            "appointment_time": "14:30:00",
            "duration": 60,
            "therapist_id": "t2222222-2222-2222-2222-222222222222",
            "status": "confirmed",
            "payment_status": "success",
            "branch_id": "br333333-3333-3333-3333-333333333333",
            "service_arrangement_id": "sa444444-4444-4444-4444-444444444444",
        },
    )

    with patch("app.events.handlers.booking.booking_updated.UshAuthClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.update_appointment_cache_by_booking_id.return_value = {
            "success": True,
            "records_updated": 1,
        }
        MockClient.return_value = mock_instance

        await handler.handle(envelope, ctx=None)

        mock_instance.update_appointment_cache_by_booking_id.assert_awaited_once_with(
            booking_id="b1111111-1111-1111-1111-111111111111",
            appointment_date="2026-10-05",
            appointment_time="14:30:00",
            duration=60,
            therapist_id="t2222222-2222-2222-2222-222222222222",
            status="confirmed",
            payment_status="success",
            branch_id="br333333-3333-3333-3333-333333333333",
            service_arrangement_id="sa444444-4444-4444-4444-444444444444",
            correlation_id=envelope.event_id_str,
        )
        mock_instance.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_booking_updated_handler_maps_payment_pending_status():
    handler = BookingUpdatedHandler()

    envelope = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="booking.updated",
        source="ushbooknpay",
        occurred_at=datetime.now(timezone.utc),
        data={
            "booking_id": "b1111111-1111-1111-1111-111111111111",
            "appointment_date": "2026-10-05",
            "appointment_time": "14:30:00",
            "duration": 60,
            "therapist_id": "t2222222-2222-2222-2222-222222222222",
            "status": "payment_pending",
            "payment_status": "",
            "branch_id": "br333333-3333-3333-3333-333333333333",
            "service_arrangement_id": "sa444444-4444-4444-4444-444444444444",
        },
    )

    with patch("app.events.handlers.booking.booking_updated.UshAuthClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.update_appointment_cache_by_booking_id.return_value = {
            "success": True,
            "records_updated": 1,
        }
        MockClient.return_value = mock_instance

        await handler.handle(envelope, ctx=None)

        mock_instance.update_appointment_cache_by_booking_id.assert_awaited_once_with(
            booking_id="b1111111-1111-1111-1111-111111111111",
            appointment_date="2026-10-05",
            appointment_time="14:30:00",
            duration=60,
            therapist_id="t2222222-2222-2222-2222-222222222222",
            status="confirmed",
            payment_status="pending",
            branch_id="br333333-3333-3333-3333-333333333333",
            service_arrangement_id="sa444444-4444-4444-4444-444444444444",
            correlation_id=envelope.event_id_str,
        )
        mock_instance.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_booking_updated_handler_skips_intermediate_checked_in_status():
    handler = BookingUpdatedHandler()
    envelope = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="booking.status_updated",
        source="ushbooknpay",
        occurred_at=datetime.now(timezone.utc),
        data={
            "booking_id": "b1111111-1111-1111-1111-111111111111",
            "status": "checked_in",
            "payment_status": "success",
        },
    )
    with patch("app.events.handlers.booking.booking_updated.UshAuthClient") as MockClient:
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance

        await handler.handle(envelope, ctx=None)

        mock_instance.update_appointment_cache_by_booking_id.assert_not_called()


@pytest.mark.asyncio
async def test_booking_updated_handler_skips_intermediate_in_progress_status():
    handler = BookingUpdatedHandler()
    envelope = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="booking.status_updated",
        source="ushbooknpay",
        occurred_at=datetime.now(timezone.utc),
        data={
            "booking_id": "b1111111-1111-1111-1111-111111111111",
            "status": "in_progress",
            "payment_status": "success",
        },
    )
    with patch("app.events.handlers.booking.booking_updated.UshAuthClient") as MockClient:
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance

        await handler.handle(envelope, ctx=None)

        mock_instance.update_appointment_cache_by_booking_id.assert_not_called()


@pytest.mark.asyncio
async def test_booking_updated_handler_handles_completed_status():
    handler = BookingUpdatedHandler()
    envelope = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="booking.completed",
        source="ushbooknpay",
        occurred_at=datetime.now(timezone.utc),
        data={
            "booking_id": "b1111111-1111-1111-1111-111111111111",
            "appointment_date": "2026-10-05",
            "appointment_time": "14:30:00",
            "duration_minutes": 60,
            "therapist_id": "t2222222-2222-2222-2222-222222222222",
            "status": "completed",
            "payment_status": "success",
        },
    )
    with patch("app.events.handlers.booking.booking_updated.UshAuthClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.update_appointment_cache_by_booking_id.return_value = {
            "success": True,
            "records_updated": 1,
        }
        MockClient.return_value = mock_instance

        await handler.handle(envelope, ctx=None)

        mock_instance.update_appointment_cache_by_booking_id.assert_awaited_once_with(
            booking_id="b1111111-1111-1111-1111-111111111111",
            appointment_date="2026-10-05",
            appointment_time="14:30:00",
            duration=60,
            therapist_id="t2222222-2222-2222-2222-222222222222",
            status="completed",
            payment_status="success",
            branch_id=None,
            service_arrangement_id=None,
            correlation_id=envelope.event_id_str,
        )
        mock_instance.aclose.assert_awaited_once()

