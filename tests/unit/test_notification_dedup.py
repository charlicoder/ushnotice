"""
Regression tests: the same SMS must never be sent repeatedly when an event is
redelivered (SQS) or a handler is retried after the DB session was rolled back.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationChannel, NotificationStatus
from app.notifications.domain.models import Notification
from app.notifications.domain.value_objects import NotificationRequest, Recipient


def _req(event_id: str = "evt-1", voucher_id: str = "voucher-1") -> NotificationRequest:
    return NotificationRequest(
        event_id=event_id,
        recipient=Recipient(channel=NotificationChannel.SMS, address="+96541028985", name="Rashid"),
        template_name="voucher/active_recipient_sms",
        template_context={"message_body": "hi"},
        booking_id=voucher_id,
    )


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


def _sent_notification() -> Notification:
    return Notification(
        event_id="evt-1", channel="SMS", recipient="x", status=NotificationStatus.SENT.value
    )


@pytest.mark.asyncio
async def test_redelivered_event_sends_sms_only_once():
    svc = NotificationService(MagicMock())
    redis = _FakeRedis()
    impl = AsyncMock(return_value=_sent_notification())

    with patch("app.core.redis.get_redis", return_value=redis), patch.object(svc, "_send_impl", impl):
        first = await svc.send(_req())
        second = await svc.send(_req())  # redelivery of the same event
        third = await svc.send(_req(event_id="evt-2"))  # re-published under a new event_id

    assert impl.await_count == 1
    for n in (first, second, third):
        assert n.status == NotificationStatus.SENT.value


@pytest.mark.asyncio
async def test_failed_send_releases_claim_so_retry_can_resend():
    svc = NotificationService(MagicMock())
    redis = _FakeRedis()
    failed = Notification(event_id="evt-1", channel="SMS", recipient="x", status=NotificationStatus.FAILED.value)
    impl = AsyncMock(side_effect=[failed, _sent_notification()])

    with patch("app.core.redis.get_redis", return_value=redis), patch.object(svc, "_send_impl", impl):
        await svc.send(_req())
        await svc.send(_req())

    assert impl.await_count == 2


@pytest.mark.asyncio
async def test_sends_normally_when_redis_unavailable():
    svc = NotificationService(MagicMock())
    impl = AsyncMock(return_value=_sent_notification())

    with patch("app.core.redis.get_redis", side_effect=RuntimeError("no redis")), patch.object(svc, "_send_impl", impl):
        await svc.send(_req())
        await svc.send(_req())

    assert impl.await_count == 2
