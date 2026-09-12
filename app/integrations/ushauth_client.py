"""
UshAuth service client — retrieves user and branch contact information.

All calls go through the API Gateway at ``/uauth``.
"""
from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.integrations.http_client import GatewayHttpClient


class UshAuthClient:
    """Client for the ushauth microservice via the API Gateway.

    Used by handlers that need to look up:
    - User details (preferred language, contact info)
    - Branch contacts (manager email/phone for reschedule notifications)
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = GatewayHttpClient(
            base_url=settings.API_GATEWAY_BASE_URL,
            service_path=settings.USHAUTH_BASE_PATH,
            service_name="ushauth",
            timeout=settings.GATEWAY_TIMEOUT,
        )

    async def get_user(
        self,
        user_id: str,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve user profile by ID.

        Returns a dict with at least: ``id``, ``name``, ``phone``, ``email``,
        ``preferred_language``.
        """
        return await self._client.get(
            f"/internal/users/{user_id}/",
            correlation_id=correlation_id,
        )

    async def get_branch_contacts(
        self,
        branch_id: str,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve branch contact information for notifications.

        Returns a dict with: ``branch_id``, ``name``, ``manager_email``,
        ``manager_phone``, ``callcenter_email``, ``callcenter_phone``.

        Branch contact ownership lives in ushauth — ushnotice must never
        duplicate or hard-code this data.
        """
        return await self._client.get(
            f"/internal/branches/{branch_id}/contacts/",
            correlation_id=correlation_id,
        )

    async def update_appointment_cache_status(
        self,
        *,
        therapist_id: str,
        service_arrangement_id: str,
        appointment_start: str,
        appointment_end: str,
        new_status: str,
        booking_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        """Update the status of both UshSpaAppointmentCache and TherapistBookingCache.

        Args:
            therapist_id: UUID of the therapist.
            service_arrangement_id: UUID of the service arrangement.
            appointment_start: ISO-8601 datetime with timezone (e.g. '2026-08-25T09:00:00+00:00').
            appointment_end: ISO-8601 datetime with timezone.
            new_status: Target status — 'confirmed' | 'cancelled' | 'completed' | 'pending'.
            booking_id: Optional booking UUID to further narrow the lookup.
            correlation_id: Distributed tracing correlation ID.
        """
        payload: dict = {
            "therapist_id": therapist_id,
            "service_arrangement_id": service_arrangement_id,
            "appointment_start": appointment_start,
            "appointment_end": appointment_end,
            "new_status": new_status,
        }
        if booking_id:
            payload["booking_id"] = booking_id

        return await self._client.post(
            "/api/v1/update-appointment-cache-status/",
            json=payload,
            correlation_id=correlation_id,
        )

    async def create_appointment_cache(
        self,
        *,
        service_arrangement_id: str,
        therapist_id: str,
        booking_id: str,
        appointment_date: str,
        appointment_time: str,
        booking_type: str = "branch_service",
        duration: int = 60,
        status: str = "confirmed",
        customer_id: str | None = None,
        customer_name: str | None = None,
        branch_id: str | None = None,
        service_id: str | None = None,
        payment_status: str | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        """Create an appointment cache record in ushauth on booking confirmation.

        Called by BookingConfirmedHandler so that the availability calendar
        and therapist schedule are updated without ushbooknpay making the call.

        Args:
            service_arrangement_id: UUID of the service arrangement.
            therapist_id: UUID of the therapist.
            booking_id: UUID of the confirmed booking.
            appointment_date: Date string ``YYYY-MM-DD``.
            appointment_time: Time string ``HH:MM``.
            booking_type: ``"branch"`` or ``"home"`` (default ``"branch"``).
            duration: Appointment duration in minutes (default 60).
            status: Appointment cache status (default ``"confirmed"``).
            customer_id: Optional customer UUID.
            customer_name: Optional customer display name.
            branch_id: Optional branch UUID.
            service_id: Optional service UUID.
            payment_status: Optional payment status string.
            correlation_id: Distributed tracing correlation ID.
        """
        payload: dict = {
            # service_arrangement_id is optional (null for home-service).
            # Send null (not "") when absent — UUIDField rejects empty strings.
            "service_arrangement_id": service_arrangement_id or None,
            "therapist_id": therapist_id,
            "booking_id": booking_id,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "booking_type": booking_type,
            "duration": duration,
            "status": status,
        }
        if customer_id:
            payload["customer_id"] = customer_id
        if customer_name:
            payload["customer_name"] = customer_name
        if branch_id:
            payload["branch_id"] = branch_id
        if service_id:
            payload["service_id"] = service_id
        if payment_status:
            payload["payment_status"] = payment_status

        return await self._client.post(
            "/api/v1/create-appointment-cache/",
            json=payload,
            correlation_id=correlation_id,
        )

    async def update_appointment_cache_payment_status(
        self,
        *,
        booking_id: str,
        payment_status: str,
        correlation_id: str | None = None,
    ) -> dict:
        """Update the payment_status of all UshSpaAppointmentCache records for a booking.

        Args:
            booking_id: UUID of the booking (maps to bookings_id column).
            payment_status: New payment status value (e.g. 'success').
            correlation_id: Distributed tracing correlation ID.
        """
        payload: dict = {
            "booking_id": booking_id,
            "payment_status": payment_status,
        }
        return await self._client.post(
            "/api/v1/update-appointment-cache-payment-status/",
            json=payload,
            correlation_id=correlation_id,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
