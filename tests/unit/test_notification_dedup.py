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


def _sent_notification() -> Notification:
    return Notification(
        event_id="evt-1", channel="SMS", recipient="x", status=NotificationStatus.SENT.value
    )


@pytest.mark.asyncio
async def test_redelivered_event_sends_sms_only_once():
    svc = NotificationService(MagicMock())
    impl = AsyncMock(return_value=_sent_notification())

    with patch.object(svc, "_send_impl", impl):
        first = await svc.send(_req())
        second = await svc.send(_req())  # redelivery of the same event
        third = await svc.send(_req(event_id="evt-2"))  # re-published under a new event_id

    assert impl.await_count == 1
    for n in (first, second, third):
        assert n.status == NotificationStatus.SENT.value


@pytest.mark.asyncio
async def test_failed_send_releases_claim_so_retry_can_resend():
    svc = NotificationService(MagicMock())
    failed = Notification(event_id="evt-1", channel="SMS", recipient="x", status=NotificationStatus.FAILED.value)
    impl = AsyncMock(side_effect=[failed, _sent_notification()])

    with patch.object(svc, "_send_impl", impl):
        await svc.send(_req())
        await svc.send(_req())

    assert impl.await_count == 2


@pytest.mark.asyncio
async def test_db_backed_dedup_suppresses_already_sent_notification(handler_context):
    # Create an already-SENT notification in the database
    n = await handler_context.notification_repo.create(
        event_id="evt-db-1",
        channel=NotificationChannel.SMS.value,
        recipient="+96541028985",
        template_name="voucher/active_recipient_sms",
        booking_id="voucher-db-1",
    )
    await handler_context.notification_repo.update_status(n, status=NotificationStatus.SENT)

    # Clear in-memory claims to simulate a fresh process/worker restart
    NotificationService.clear_dedup_claims()

    svc = NotificationService(handler_context)
    impl = AsyncMock(return_value=_sent_notification())

    with patch.object(svc, "_send_impl", impl):
        res = await svc.send(_req(event_id="evt-db-2", voucher_id="voucher-db-1"))

    assert impl.await_count == 0
    assert res.status == NotificationStatus.SENT.value
