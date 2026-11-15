"""
Handler for `voucher.delivery_due` event.
─────────────────────────────────────────
Sent to SQS by AWS EventBridge Scheduler at the customer's scheduled delivery datetime.

On voucher.delivery_due, this handler:
  1. Extracts voucher_id from the envelope.
  2. Calls ushbooknpay: POST /api/v1/vouchers/{voucher_id}/schedule/activate/
  3. ushbooknpay transitions voucher status to 'active', sets delivered_at, calculates
     the 60-day expiry starting from delivery date, and emits `voucher.active`.
  4. VoucherActiveHandler then delivers the secret code, SMS, and WhatsApp to the recipient.
"""

from __future__ import annotations

import uuid

from app.core.exceptions import ServiceClientError
from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushbooknpay_client import UshBookNPayClient

logger = get_logger(__name__)


class VoucherDeliveryDueHandler(EventHandler):
    """
    Processes voucher.delivery_due events received from EventBridge Scheduler via SQS.
    """

    event_type: str = "voucher.delivery_due"

    async def handle(self, envelope: EventEnvelope, context: HandlerContext) -> None:
        data = envelope.data
        correlation_id = envelope.correlation_id_str
        raw_vid = data.get("voucher_id") or data.get("id")

        if not raw_vid or str(raw_vid).strip().lower() in ("none", "null", ""):
            logger.error("voucher_delivery_due_missing_voucher_id", data=data)
            return

        try:
            valid_uuid = uuid.UUID(str(raw_vid).strip())
            voucher_id = str(valid_uuid)
        except (ValueError, TypeError, AttributeError):
            logger.error(
                "voucher_delivery_due_invalid_voucher_id",
                raw_voucher_id=raw_vid,
                data=data,
            )
            return

        logger.info(
            "voucher_delivery_due_activating",
            voucher_id=voucher_id,
            correlation_id=correlation_id,
        )

        try:
            client = UshBookNPayClient()
            result = await client.activate_scheduled_voucher(
                voucher_id,
                delivery_result={
                    "delivered": True,
                    "source": "eventbridge_scheduler",
                    "event_id": envelope.event_id_str,
                },
                correlation_id=correlation_id,
            )
            await client._client.aclose()
            logger.info(
                "voucher_delivery_due_activated_successfully",
                voucher_id=voucher_id,
                result=result,
            )
        except ServiceClientError as exc:
            if exc.status_code in (404, 422):
                logger.error(
                    "voucher_delivery_due_unrecoverable_client_error",
                    voucher_id=voucher_id,
                    status_code=exc.status_code,
                    error=str(exc),
                )
                return
            logger.error(
                "voucher_delivery_due_activation_failed",
                voucher_id=voucher_id,
                error=str(exc),
            )
            raise
        except Exception as exc:
            logger.error(
                "voucher_delivery_due_activation_failed",
                voucher_id=voucher_id,
                error=str(exc),
            )
            raise

