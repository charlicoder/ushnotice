"""
Unit and service tests for Meta WhatsApp webhook endpoints and processing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.notifications.domain.enums import AttemptStatus, NotificationChannel, NotificationStatus
from app.notifications.domain.models import Event, InboundMessage, Notification, NotificationAttempt
from app.notifications.infrastructure.repositories import (
    InboundMessageRepository,
    NotificationAttemptRepository,
    NotificationRepository,
)


def _sign_payload(body: bytes, secret: str) -> str:
    """Compute Meta X-Hub-Signature-256 header value."""
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


@pytest.mark.asyncio
async def test_webhook_verification_success(async_client: AsyncClient) -> None:
    """GET webhook verification succeeds when verify token and mode match."""
    with patch.object(get_settings(), "META_WA_VERIFY_TOKEN", SecretStr("my_test_verify_token")):
        response = await async_client.get(
            "/v1/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "1158201444",
                "hub.verify_token": "my_test_verify_token",
            },
        )
        assert response.status_code == 200
        assert response.text == "1158201444"
        assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_webhook_verification_with_trailing_slash(async_client: AsyncClient) -> None:
    """GET webhook verification works cleanly with trailing slash."""
    with patch.object(get_settings(), "META_WA_VERIFY_TOKEN", SecretStr("my_test_verify_token")):
        response = await async_client.get(
            "/v1/webhooks/whatsapp/",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "987654321",
                "hub.verify_token": "my_test_verify_token",
            },
        )
        assert response.status_code == 200
        assert response.text == "987654321"


@pytest.mark.asyncio
async def test_webhook_verification_token_mismatch(async_client: AsyncClient) -> None:
    """GET webhook verification fails (403) on incorrect verify token."""
    with patch.object(get_settings(), "META_WA_VERIFY_TOKEN", SecretStr("my_test_verify_token")):
        response = await async_client.get(
            "/v1/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "1158201444",
                "hub.verify_token": "wrong_token",
            },
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_verification_invalid_mode(async_client: AsyncClient) -> None:
    """GET webhook verification fails (403) when hub.mode is not 'subscribe'."""
    with patch.object(get_settings(), "META_WA_VERIFY_TOKEN", SecretStr("my_test_verify_token")):
        response = await async_client.get(
            "/v1/webhooks/whatsapp",
            params={
                "hub.mode": "unsubscribe",
                "hub.challenge": "1158201444",
                "hub.verify_token": "my_test_verify_token",
            },
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_post_signature_validation(async_client: AsyncClient) -> None:
    """POST webhook validates HMAC signature when META_WA_APP_SECRET is set."""
    app_secret = "test_meta_app_secret_123"
    payload = {"object": "whatsapp_business_account", "entry": []}
    body_bytes = json.dumps(payload).encode("utf-8")
    valid_sig = _sign_payload(body_bytes, app_secret)

    with patch.object(get_settings(), "META_WA_APP_SECRET", SecretStr(app_secret)):
        # 1. Valid signature -> 200
        res_valid = await async_client.post(
            "/v1/webhooks/whatsapp",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": valid_sig,
            },
        )
        assert res_valid.status_code == 200

        # 2. Invalid signature -> 401
        res_invalid = await async_client.post(
            "/v1/webhooks/whatsapp",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=bad_hex_signature",
            },
        )
        assert res_invalid.status_code == 401

        # 3. Missing signature header -> 401
        res_missing = await async_client.post(
            "/v1/webhooks/whatsapp",
            content=body_bytes,
            headers={"Content-Type": "application/json"},
        )
        assert res_missing.status_code == 401


@pytest.mark.asyncio
async def test_webhook_status_delivered_updates_database(
    async_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """Status callback 'delivered' updates Notification and NotificationAttempt."""
    # 1. Seed test database with a WhatsApp notification & attempt
    event_id = str(uuid.uuid4())
    event = Event(
        id=event_id,
        event_id=event_id,
        event_type="booking.confirmed",
        version=1,
        source="test",
        status="PROCESSED",
    )
    test_db_session.add(event)

    notif_id = str(uuid.uuid4())
    notification = Notification(
        id=notif_id,
        event_id=event_id,
        channel=NotificationChannel.WHATSAPP.value,
        recipient="+96598765432",
        status=NotificationStatus.SENT.value,
        provider="meta",
    )
    test_db_session.add(notification)

    attempt_id = str(uuid.uuid4())
    wamid = "wamid.HBgLMTY1MDU1NTAwMTAVAgARGBI2ODU5N0QyMTNFQjAyRDRFAAA="
    attempt = NotificationAttempt(
        id=attempt_id,
        notification_id=notif_id,
        provider="meta",
        attempt_number=1,
        status=AttemptStatus.IN_PROGRESS.value,
        provider_message_id=wamid,
    )
    test_db_session.add(attempt)
    await test_db_session.commit()

    # 2. Send 'delivered' status callback
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1234567890",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "16505551111",
                                "phone_number_id": "100200300",
                            },
                            "statuses": [
                                {
                                    "id": wamid,
                                    "status": "delivered",
                                    "timestamp": "1730548800",
                                    "recipient_id": "96598765432",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    response = await async_client.post("/v1/webhooks/whatsapp", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["processed_statuses"] == 1

    # 3. Verify database state
    await test_db_session.refresh(notification)
    await test_db_session.refresh(attempt)

    assert notification.status == NotificationStatus.DELIVERED.value
    assert attempt.status == AttemptStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_webhook_status_read_and_failed(
    async_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """Status callback 'read' and 'failed' updates Notification and records errors."""
    event_id = str(uuid.uuid4())
    event = Event(id=event_id, event_id=event_id, event_type="test", source="test")
    test_db_session.add(event)

    notif_id = str(uuid.uuid4())
    notification = Notification(
        id=notif_id,
        event_id=event_id,
        channel=NotificationChannel.WHATSAPP.value,
        recipient="+96598765432",
        status=NotificationStatus.DELIVERED.value,
        provider="meta",
    )
    test_db_session.add(notification)

    attempt_id = str(uuid.uuid4())
    wamid = "wamid.HBgLMTY1MDU1NTAwMTAXAgARGBI2ODU5N0QyMTNFQjAyRDRFAAA="
    attempt = NotificationAttempt(
        id=attempt_id,
        notification_id=notif_id,
        provider="meta",
        attempt_number=1,
        status=AttemptStatus.SUCCESS.value,
        provider_message_id=wamid,
    )
    test_db_session.add(attempt)
    await test_db_session.commit()

    # 1. Test 'read'
    read_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1234567890",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [
                                {
                                    "id": wamid,
                                    "status": "read",
                                    "timestamp": "1730548900",
                                    "recipient_id": "96598765432",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    res_read = await async_client.post("/v1/webhooks/whatsapp", json=read_payload)
    assert res_read.status_code == 200

    await test_db_session.refresh(notification)
    assert notification.status == NotificationStatus.READ.value

    # 2. Test 'failed' on a different message
    failed_wamid = "wamid.FAILED_MESSAGE_123"
    attempt_failed = NotificationAttempt(
        id=str(uuid.uuid4()),
        notification_id=notif_id,
        provider="meta",
        attempt_number=2,
        status=AttemptStatus.IN_PROGRESS.value,
        provider_message_id=failed_wamid,
    )
    test_db_session.add(attempt_failed)
    await test_db_session.commit()

    fail_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1234567890",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [
                                {
                                    "id": failed_wamid,
                                    "status": "failed",
                                    "timestamp": "1730549000",
                                    "recipient_id": "96598765432",
                                    "errors": [
                                        {
                                            "code": 131026,
                                            "title": "Message undeliverable",
                                            "message": "Receiver is incapable of receiving this message",
                                        }
                                    ],
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    res_fail = await async_client.post("/v1/webhooks/whatsapp", json=fail_payload)
    assert res_fail.status_code == 200

    await test_db_session.refresh(notification)
    await test_db_session.refresh(attempt_failed)

    assert notification.status == NotificationStatus.FAILED.value
    assert attempt_failed.status == AttemptStatus.FAILED.value
    assert attempt_failed.error_code == "131026"
    assert "Receiver is incapable" in (attempt_failed.error_message or "")


@pytest.mark.asyncio
async def test_webhook_inbound_customer_message(
    async_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """Inbound customer message is stored in inbound_messages table."""
    inbound_wamid = "wamid.INBOUND_USER_MSG_001"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1234567890",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "16505551111",
                                "phone_number_id": "998877",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Fatima Al-Sabah"},
                                    "wa_id": "96598765432",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "96598765432",
                                    "id": inbound_wamid,
                                    "timestamp": "1730549100",
                                    "text": {"body": "I would like to confirm my appointment for tomorrow."},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    response = await async_client.post("/v1/webhooks/whatsapp", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["processed_messages"] == 1

    # Verify inbound message in repository
    inbound_repo = InboundMessageRepository(test_db_session)
    msg = await inbound_repo.get_by_provider_message_id(inbound_wamid)
    assert msg is not None
    assert msg.from_number == "96598765432"
    assert msg.sender_name == "Fatima Al-Sabah"
    assert msg.body == "I would like to confirm my appointment for tomorrow."
    assert msg.message_type == "text"
    assert msg.provider == "meta"
    assert msg.to_number_id == "998877"
