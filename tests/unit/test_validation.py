"""
Unit tests for EventEnvelope schema validation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.events.schemas.envelope import EventEnvelope


def test_valid_event_envelope() -> None:
    data = {
        "event_id": str(uuid.uuid4()),
        "event_type": "user.registered",
        "version": 1,
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": "ushauth",
        "correlation_id": str(uuid.uuid4()),
        "causation_id": None,
        "data": {"phone": "+96598765432", "otp": "123456"},
    }

    envelope = EventEnvelope.model_validate(data)
    assert envelope.event_type == "user.registered"
    assert envelope.source == "ushauth"
    assert envelope.data["otp"] == "123456"


def test_invalid_event_type_characters() -> None:
    data = {
        "event_id": str(uuid.uuid4()),
        "event_type": "user.registered; DROP TABLE events;--",
        "version": 1,
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": "ushauth",
        "data": {},
    }

    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(data)


def test_missing_required_fields() -> None:
    data = {
        "event_type": "booking.confirmed",
        # missing event_id, occurred_at
    }

    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(data)


def test_flat_booking_confirmed_payload_normalization() -> None:
    """Ensure flat payload without source and with schema_version validates cleanly and populates data."""
    raw_payload = {
        "event_id": "b3893ee5-224c-4ba9-82ef-b0276c48881a",
        "occurred_at": "2026-08-25T14:44:32.881243+00:00",
        "schema_version": "1.0",
        "event_name": "Booking.Confirmed",
        "event_type": "booking.confirmed",
        "booking_id": "c577b342-417a-4d32-9b1f-d77452e48d74",
        "customer_id": "b92c374d-fdb5-48ff-96d5-bb4dc2abc452",
        "branch_id": "c53023e8-0ed8-45ba-8eda-e4c3b522e497",
        "service_id": "44729558-26f3-4a52-a454-99ed8a7c0362",
        "service_arrangement_id": "f08f405b-eef0-4f74-b576-103016e5c836",
        "therapist_id": "fc68823e-0c63-48cb-a77e-31f9533b3c20",
        "appointment_start": "2026-08-26T11:00:00+00:00",
        "appointment_end": "2026-08-26T12:30:00+00:00",
        "appointment_date": "2026-08-26",
        "appointment_starttime": "11:00",
        "appointment_endtime": "12:30",
        "customer_name": "K Md Mamunur Rashid",
        "customer_phone": "+96541028983",
        "customer_email": "Mamun1980@gmail.com",
        "total_amount": "42.000",
        "currency": "KWD",
    }

    envelope = EventEnvelope.model_validate(raw_payload)
    assert envelope.event_id_str == "b3893ee5-224c-4ba9-82ef-b0276c48881a"
    assert envelope.event_type == "booking.confirmed"
    assert envelope.source == "ushbooknpay"
    assert envelope.version == 1
    assert envelope.data["booking_id"] == "c577b342-417a-4d32-9b1f-d77452e48d74"
    assert envelope.data["customer_name"] == "K Md Mamunur Rashid"
    assert envelope.data["service_arrangement_id"] == "f08f405b-eef0-4f74-b576-103016e5c836"
    assert envelope.data["therapist_id"] == "fc68823e-0c63-48cb-a77e-31f9533b3c20"

