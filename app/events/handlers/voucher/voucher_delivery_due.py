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
        voucher_id = str(data.get("voucher_id") or data.get("id") or "")

        if not voucher_id:
            logger.error("voucher_delivery_due_missing_voucher_id", data=data)
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
        except Exception as exc:
            logger.error(
                "voucher_delivery_due_activation_failed",
                voucher_id=voucher_id,
                error=str(exc),
            )
            raise

