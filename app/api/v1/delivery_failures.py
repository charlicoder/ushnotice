"""
Delivery failures monitoring endpoint.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DBSession, RequireAuth
from app.common.masking import mask_email, mask_phone
from app.common.pagination import PaginatedResponse, PaginationParams
from app.notifications.infrastructure.repositories import NotificationRepository
from app.notifications.interfaces.schemas import DeliveryFailureResponse

router = APIRouter(prefix="/delivery-failures", tags=["Monitoring"], dependencies=[Depends(RequireAuth)])


@router.get("", response_model=PaginatedResponse[DeliveryFailureResponse], summary="List Delivery Failures")
async def list_delivery_failures(
    db: DBSession,
    pagination: Annotated[PaginationParams, Depends()],
) -> PaginatedResponse[DeliveryFailureResponse]:
    repo = NotificationRepository(db)
    items, total = await repo.list_failed(
        offset=pagination.offset,
        limit=pagination.page_size,
    )

    failure_items: list[DeliveryFailureResponse] = []
    for item in items:
        # Mask recipient
        raw_rec = item.recipient or ""
        ch = (item.channel or "").lower()
        if ch in ("sms", "whatsapp"):
            m_rec = mask_phone(raw_rec)
        elif ch == "email":
            m_rec = mask_email(raw_rec)
        else:
            m_rec = "***"

        last_attempt = item.attempts[-1] if item.attempts else None

        failure_items.append(
            DeliveryFailureResponse(
                notification_id=item.id,
                channel=item.channel,
                recipient=m_rec,
                provider=item.provider,
                retry_count=item.retry_count,
                created_at=item.created_at,
                last_error_code=last_attempt.error_code if last_attempt else None,
                last_error_message=last_attempt.error_message if last_attempt else None,
            )
        )

    return PaginatedResponse.build(failure_items, total, pagination)
