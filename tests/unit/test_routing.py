"""
Unit tests for Event Registry, Router, and Idempotency Guard.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.events.handlers.base import HandlerContext
from app.events.registry.handler_registry import HandlerRegistry
from app.events.router import EventRouter
from app.events.schemas.envelope import EventEnvelope
from app.notifications.domain.models import Event


class DummyHandler:
    event_type = "dummy.event"
    invoked = False

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        self.invoked = True


@pytest.mark.asyncio
async def test_registry_and_router_execution(handler_context: HandlerContext) -> None:
    registry = HandlerRegistry()
    handler = DummyHandler()
    registry.register(handler)

    assert registry.has_handler("dummy.event")

    router = EventRouter(registry)

    envelope = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="dummy.event",
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="test",
        data={"key": "val"},
    )

    event_record = await handler_context.event_repo.create(
        event_id=envelope.event_id_str,
        event_type=envelope.event_type,
        version=1,
        source="test",
        correlation_id=None,
        causation_id=None,
        payload=envelope.data,
    )

    await router.route(envelope, event_record, handler_context)
    assert handler.invoked is True
