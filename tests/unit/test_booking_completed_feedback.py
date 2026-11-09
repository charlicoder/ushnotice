import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.events.handlers.booking.booking_completed_feedback import (
    BookingCompletedFeedbackHandler,
    build_link_request,
    feedback_message,
)
from app.events.registry.handler_registry import build_default_registry
from app.events.schemas.envelope import EventEnvelope

BOOKING_ID = "b1111111-1111-1111-1111-111111111111"
MOD = "app.events.handlers.booking.booking_completed_feedback"
URL = "https://feedback.example/abc"


def _envelope(**over) -> EventEnvelope:
    data = {
        "booking_id": BOOKING_ID,
        "customer_name": "Sara Ali",
        "customer_phone": "+96550000000",
        "customer_language": "en",
        "status": "completed",
        **over,
    }
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="booking.completed",
        source="ushbooknpay",
        occurred_at=datetime.now(timezone.utc),
        data=data,
    )


def _patches(link=None, send_side_effect=None):
    client = AsyncMock()
    client.create_feedback_link.return_value = link or {"feedback_url": URL, "status": "pending"}
    service = AsyncMock()
    service.send.side_effect = send_side_effect
    return client, service


@pytest.mark.asyncio
async def test_whatsapp_sent_no_sms():
    client, service = _patches()
    with patch(f"{MOD}.UshanrClient", return_value=client), patch(f"{MOD}.NotificationService", return_value=service):
        await BookingCompletedFeedbackHandler().handle(_envelope(), ctx=None)
    assert service.send.await_count == 1
    assert service.send.await_args.args[0].template_name == "booking/feedback_whatsapp"
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_sms_only_when_whatsapp_fails():
    client, service = _patches(send_side_effect=[RuntimeError("wa down"), None])
    with patch(f"{MOD}.UshanrClient", return_value=client), patch(f"{MOD}.NotificationService", return_value=service):
        await BookingCompletedFeedbackHandler().handle(_envelope(), ctx=None)
    names = [c.args[0].template_name for c in service.send.await_args_list]
    assert names == ["booking/feedback_whatsapp", "booking/feedback_sms"]


@pytest.mark.asyncio
async def test_already_submitted_is_skipped():
    client, service = _patches(link={"feedback_url": URL, "status": "submitted"})
    with patch(f"{MOD}.UshanrClient", return_value=client), patch(f"{MOD}.NotificationService", return_value=service):
        await BookingCompletedFeedbackHandler().handle(_envelope(), ctx=None)
    service.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_phone_is_skipped():
    with patch(f"{MOD}.UshanrClient") as MockClient:
        await BookingCompletedFeedbackHandler().handle(_envelope(customer_phone=""), ctx=None)
    MockClient.assert_not_called()


@pytest.mark.asyncio
async def test_link_creation_failure_propagates_for_retry():
    client, service = _patches()
    client.create_feedback_link.side_effect = RuntimeError("ushanr down")
    with patch(f"{MOD}.UshanrClient", return_value=client), patch(f"{MOD}.NotificationService", return_value=service):
        with pytest.raises(RuntimeError):
            await BookingCompletedFeedbackHandler().handle(_envelope(), ctx=None)
    service.send.assert_not_awaited()
    client.aclose.assert_awaited_once()


def test_request_drops_non_uuid_ids_and_message_is_localised():
    req = build_link_request({"booking_id": BOOKING_ID, "customer_id": "not-a-uuid", "branch_id": BOOKING_ID}, "ar")
    assert req["customer_id"] is None and req["branch_id"] == BOOKING_ID
    assert "Hello Sara" in feedback_message(URL, "Sara Ali", "en")
    assert "مرحباً" in feedback_message(URL, "", "ar")


def test_registry_runs_both_handlers_on_completed():
    registry = build_default_registry()
    names = {type(h).__name__ for h in registry.resolve("booking.completed")}
    assert {"BookingUpdatedHandler", "BookingCompletedFeedbackHandler"} <= names
