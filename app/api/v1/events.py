"""
Event audit and history endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import DBSession, RequireAuth
from app.common.pagination import PaginatedResponse, PaginationParams
from app.notifications.infrastructure.repositories import EventRepository
from app.notifications.interfaces.schemas import EventResponse

router = APIRouter(prefix="/events", tags=["Events"], dependencies=[Depends(RequireAuth)])


@router.get("", response_model=PaginatedResponse[EventResponse], summary="List Events with Filters")
async def list_events(
    db: DBSession,
    pagination: Annotated[PaginationParams, Depends()],
    event_type: Annotated[str | None, Query(description="Filter by event type")] = None,
    status_filter: Annotated[str | None, Query(alias="status", description="Filter by event status")] = None,
    source: Annotated[str | None, Query(description="Filter by source service")] = None,
    correlation_id: Annotated[str | None, Query(description="Filter by correlation ID")] = None,
    date_from: Annotated[datetime | None, Query(description="Filter by received_at >= date_from")] = None,
    date_to: Annotated[datetime | None, Query(description="Filter by received_at <= date_to")] = None,
) -> PaginatedResponse[EventResponse]:
    repo = EventRepository(db)
    items, total = await repo.list_events(
        event_type=event_type,
        status=status_filter,
        source=source,
        correlation_id=correlation_id,
        date_from=date_from,
        date_to=date_to,
        offset=pagination.offset,
        limit=pagination.page_size,
    )

    response_items = [EventResponse.model_validate(item) for item in items]
    return PaginatedResponse.build(response_items, total, pagination)


@router.get("/{event_id}", response_model=EventResponse, summary="Get Event Details")
async def get_event(
    event_id: str,
    db: DBSession,
) -> EventResponse:
    repo = EventRepository(db)
    event = await repo.get_by_event_id(event_id)
    if not event:
        # Fallback to PK lookup
        event = await repo.get_by_id(event_id)

    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    return EventResponse.model_validate(event)
