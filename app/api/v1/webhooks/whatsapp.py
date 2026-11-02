"""
Meta WhatsApp Webhook Endpoint.

Provides:
- GET  /v1/webhooks/whatsapp  — Webhook verification handshake with Meta.
- POST /v1/webhooks/whatsapp  — Delivery status updates and inbound message callbacks.

Documentation:
https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview/
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import verify_meta_wa_signature
from app.notifications.application.whatsapp_webhook_service import WhatsAppWebhookService

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["Webhooks"])


@router.get("", response_class=Response, summary="Meta WhatsApp Webhook Verification")
@router.get("/", response_class=Response, include_in_schema=False)
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
) -> Response:
    """Handle Meta WhatsApp Webhook verification handshake (hub.challenge).

    When you configure your callback URL in the Meta App Dashboard, Meta makes a
    GET request to this endpoint with:
      - hub.mode=subscribe
      - hub.verify_token=<token>
      - hub.challenge=<integer/string>

    If the verify token matches settings.META_WA_VERIFY_TOKEN and hub.mode is
    'subscribe', return the hub.challenge value as plain text with HTTP 200.
    Otherwise return HTTP 403 Forbidden.
    """
    settings = get_settings()
    expected_token = settings.META_WA_VERIFY_TOKEN.get_secret_value()

    # In production or when token is configured, enforce strict token matching
    if expected_token:
        if hub_mode == "subscribe" and hub_verify_token == expected_token:
            logger.info("Meta WhatsApp webhook challenge verified successfully")
            return Response(
                content=hub_challenge or "",
                media_type="text/plain",
                status_code=status.HTTP_200_OK,
            )
        logger.warning(
            "Meta WhatsApp webhook verification failed (token mismatch or invalid mode)",
            hub_mode=hub_mode,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification token mismatch or invalid mode",
        )

    # In non-production environments when token is not yet configured, allow verification
    if hub_mode == "subscribe" and not settings.is_production:
        logger.warning(
            "META_WA_VERIFY_TOKEN is not configured; allowing verification challenge in non-production mode",
            hub_mode=hub_mode,
        )
        return Response(
            content=hub_challenge or "",
            media_type="text/plain",
            status_code=status.HTTP_200_OK,
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token is not configured or mode is invalid",
    )


@router.post("", status_code=status.HTTP_200_OK, summary="Meta WhatsApp Webhook Callback Handler")
@router.post("/", status_code=status.HTTP_200_OK, include_in_schema=False)
async def handle_webhook_notification(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    """Handle incoming WhatsApp webhook notifications from Meta.

    Meta delivers:
      1. Delivery status updates: sent, delivered, read, failed.
      2. Inbound customer messages: text, interactive buttons, list selections.

    Authenticates request via X-Hub-Signature-256 (HMAC-SHA256 with META_WA_APP_SECRET).
    Always returns HTTP 200 quickly to prevent Meta from retrying or dropping the webhook.
    """
    raw_body = await request.body()
    settings = get_settings()
    app_secret = settings.META_WA_APP_SECRET.get_secret_value()

    # 1. Cryptographic HMAC-SHA256 signature verification
    if app_secret:
        if not verify_meta_wa_signature(raw_body, x_hub_signature_256, app_secret):
            logger.warning(
                "Rejected WhatsApp webhook callback with invalid X-Hub-Signature-256 signature"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )

    # 2. Parse JSON payload
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as exc:
        logger.error("Failed to decode WhatsApp webhook JSON payload", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    # 3. Process event statuses and messages
    service = WhatsAppWebhookService(db)
    result = await service.process_payload(payload)

    return result
