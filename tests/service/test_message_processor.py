"""
Service test for SQS MessageProcessor end-to-end processing.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.consumer.message_processor import MessageProcessor
from app.events.router import EventRouter
from app.notifications.domain.enums import EventStatus, NotificationStatus
from app.notifications.infrastructure.repositories import EventRepository, NotificationRepository


@pytest.mark.asyncio
async def test_process_user_registered_sqs_message(
    test_db_session: AsyncSession,
    default_router: EventRouter,
) -> None:
    processor = MessageProcessor(default_router)

    event_id = str(uuid.uuid4())
    raw_payload = json.dumps({
        "event_id": event_id,
        "event_type": "user.registered",
        "version": 1,
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": "ushauth",
        "data": {
            "phone": "+96598765432",
            "name": "Ahmed",
            "otp": "456789",
            "expiry_minutes": 5,
        },
    })

    event = await processor.process_raw_message(
        raw_body=raw_payload,
        sqs_message_id="sqs-msg-12345",
        db=test_db_session,
    )

    assert event is not None
    assert event.event_type == "user.registered"
    assert event.status == EventStatus.PROCESSED.value

    # Verify notification record created
    notif_repo = NotificationRepository(test_db_session)
    items, total = await notif_repo.list_notifications()
    assert total >= 1
    assert any(n.event_id == event.id for n in items)


@pytest.mark.asyncio
async def test_process_duplicate_message_idempotency(
    test_db_session: AsyncSession,
    default_router: EventRouter,
) -> None:
    processor = MessageProcessor(default_router)

    event_id = str(uuid.uuid4())
    raw_payload = json.dumps({
        "event_id": event_id,
        "event_type": "user.verified",
        "version": 1,
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": "ushauth",
        "data": {
            "phone": "+96598765432",
            "name": "Ahmed",
        },
    })

    # First attempt
    event1 = await processor.process_raw_message(
        raw_body=raw_payload,
        db=test_db_session,
    )
    assert event1 is not None

    notif_repo = NotificationRepository(test_db_session)
    items1, total1 = await notif_repo.list_notifications()

    # Second attempt (same message / event_id)
    event2 = await processor.process_raw_message(
        raw_body=raw_payload,
        db=test_db_session,
    )
    assert event2 is not None

    # Verify no duplicate notifications were created for the same event
    items2, total2 = await notif_repo.list_notifications()
    assert total2 == total1


@pytest.mark.asyncio
async def test_process_booking_confirmed_with_email(
    test_db_session: AsyncSession,
    default_router: EventRouter,
) -> None:
    processor = MessageProcessor(default_router)

    event_id = str(uuid.uuid4())
    raw_payload = json.dumps({
        "event_id": event_id,
        "event_type": "booking.confirmed",
        "version": 1,
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": "ushbooknpay",
        "data": {
            "booking_id": "b-12345",
            "booking_reference": "BK-9988",
            "customer_id": "c-100",
            "customer_name": "Fatima",
            "customer_phone": "+96599112233",
            "customer_email": "fatima@example.com",
            "service_name": "Deep Tissue Massage",
            "branch_name": "Salmiya",
            "appointment_date": "2026-09-01",
            "appointment_starttime": "10:00",
            "appointment_endtime": "11:00",
            "total_amount": "35.000",
            "currency": "KWD",
        },
    })

    event = await processor.process_raw_message(
        raw_body=raw_payload,
        db=test_db_session,
    )

    assert event is not None
    assert event.status == EventStatus.PROCESSED.value

    # Verify email notification was created
    notif_repo = NotificationRepository(test_db_session)
    items, total = await notif_repo.list_notifications(channel="email")
    assert any(n.event_id == event.id and n.recipient == "fatima@example.com" for n in items)


@pytest.mark.asyncio
async def test_process_email_verification_requested(
    test_db_session: AsyncSession,
    default_router: EventRouter,
) -> None:
    processor = MessageProcessor(default_router)

    event_id = str(uuid.uuid4())
    raw_payload = json.dumps({
        "event_id": event_id,
        "event_type": "email.verification.requested",
        "version": 1,
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": "ushauth",
        "data": {
            "user_id": "u-456",
            "customer_name": "Sara",
            "email": "sara@example.com",
            "otp": "987654",
            "expiry_minutes": 10,
        },
    })

    event = await processor.process_raw_message(
        raw_body=raw_payload,
        db=test_db_session,
    )

    assert event is not None
    notif_repo = NotificationRepository(test_db_session)
    items, total = await notif_repo.list_notifications(channel="email")
    assert any(n.event_id == event.id and n.recipient == "sara@example.com" for n in items)


@pytest.mark.asyncio
async def test_process_payment_success_with_email(
    test_db_session: AsyncSession,
    default_router: EventRouter,
) -> None:
    processor = MessageProcessor(default_router)

    event_id = str(uuid.uuid4())
    raw_payload = json.dumps({
        "event_id": event_id,
        "event_type": "payment.success",
        "version": 1,
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": "ushbooknpay",
        "data": {
            "payment_id": "pay-777",
            "booking_id": "b-12345",
            "booking_reference": "BK-9988",
            "customer_id": "c-100",
            "customer_name": "Fatima",
            "phone": "+96599112233",
            "email": "fatima@example.com",
            "amount": "35.000",
            "currency": "KWD",
        },
    })

    event = await processor.process_raw_message(
        raw_body=raw_payload,
        db=test_db_session,
    )

    assert event is not None
    notif_repo = NotificationRepository(test_db_session)
    items, total = await notif_repo.list_notifications(channel="email")
    assert any(n.event_id == event.id and n.recipient == "fatima@example.com" for n in items)
