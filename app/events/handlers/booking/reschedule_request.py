"""
Handler for `booking.reschedule_requested` event.

Workflow:
1. Lookup branch contact details from `ushauth`
2. Dispatch WhatsApp / Email notification to branch manager
3. Dispatch confirmation SMS to customer
4. Call `ushbooknpay` to update booking status to RESCHEDULE_REQUESTED
5. Record all API and Notification actions in the audit log
"""
from __future__ import annotations

import time

from app.core.config import get_settings
from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationChannel
from app.notifications.domain.value_objects import NotificationRequest, Recipient

logger = get_logger(__name__)


class RescheduleRequestHandler:
    """Processes reschedule request events with full cross-service orchestration."""

    event_type: str = "booking.reschedule_requested"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)
        booking_id = str(data.get("booking_id") or "")
        branch_id = str(data.get("branch_id") or "")
        customer_id = str(data.get("customer_id") or "")
        correlation_id = envelope.correlation_id_str

        # 1. Fetch branch contacts from ushauth
        auth_client = UshAuthClient()
        branch_contacts: dict = {}
        try:
            if branch_id:
                branch_contacts = await auth_client.get_branch_contacts(
                    branch_id, correlation_id=correlation_id
                )
        except Exception as exc:
            logger.warning(
                "Could not fetch branch contacts from ushauth; falling back to payload",
                error=str(exc),
                branch_id=branch_id,
            )
        finally:
            await auth_client.aclose()

        branch_name = branch_contacts.get("name") or data.get("branch_name") or "Branch"
        branch_manager_phone = branch_contacts.get("manager_phone") or data.get("branch_phone")
        branch_manager_email = branch_contacts.get("manager_email") or data.get("branch_email")

        context = {
            "booking_reference": data.get("booking_reference") or data.get("reference") or "",
            "customer_name": data.get("customer_name") or "Customer",
            "branch_name": branch_name,
            "service_name": data.get("service_name") or "Spa Service",
            "service_arrangement": data.get("service_arrangement"),
            "appointment_date": data.get("appointment_date") or data.get("date") or "",
            "appointment_time": data.get("appointment_time") or data.get("time") or "",
        }

        # 2/3. Notifications (SMS / WhatsApp / Email) to branch manager and customer
        # are intentionally disabled for booking.reschedule_requested events.

        # 4. Call ushbooknpay to update booking status
        if booking_id:
            book_client = UshBookNPayClient()
            api_req = await ctx.api_request_repo.create(
                service="ushbooknpay",
                method="PATCH",
                path=f"/internal/bookings/{booking_id}/status/",
                request_body={"status": "RESCHEDULE_REQUESTED", "reason": "Customer requested reschedule via event"},
                event_id=envelope.event_id_str,
                correlation_id=correlation_id,
            )

            start_t = time.monotonic()
            try:
                resp = await book_client.update_booking_status(
                    booking_id,
                    status="RESCHEDULE_REQUESTED",
                    reason="Customer requested reschedule via event",
                    correlation_id=correlation_id,
                )
                latency = int((time.monotonic() - start_t) * 1000)
                await ctx.api_request_repo.mark_success(
                    api_req,
                    response_status=200,
                    response_body=resp,
                    latency_ms=latency,
                )
                logger.info(
                    "ushbooknpay booking status updated to RESCHEDULE_REQUESTED",
                    booking_id=booking_id,
                    latency_ms=latency,
                )
            except Exception as exc:
                latency = int((time.monotonic() - start_t) * 1000)
                await ctx.api_request_repo.mark_failed(
                    api_req,
                    error=str(exc),
                    latency_ms=latency,
                )
                logger.error(
                    "Failed to update booking status in ushbooknpay",
                    booking_id=booking_id,
                    error=str(exc),
                )
            finally:
                await book_client.aclose()
