"""
Handler for ``booking.updated`` and ``booking.rescheduled`` events.

Fired by ushbooknpay when a booking is updated or rescheduled.

Actions:
  1. Updates appointment cache in ushauth:
     POST /api/v1/update-appointment-cache-by-booking-id/
  2. Sends WhatsApp / SMS notification to customer:
     - Notification language MUST be Arabic unless explicitly told for English.
     - WhatsApp if verified, otherwise SMS fallback.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.events.handlers.booking._services import _cache_services
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


def _build_update_context(data: dict) -> dict:
    """Extract and normalize updated booking context."""
    appointment_date = (
        data.get("appointment_date")
        or data.get("date")
        or ""
    )
    appointment_time = (
        data.get("appointment_starttime")
        or data.get("appointment_time")
        or data.get("time")
        or ""
    )
    appointment_end_time = (
        data.get("appointment_endtime")
        or data.get("end_time")
        or ""
    )
    booking_reference = (
        data.get("booking_reference")
        or data.get("booking_number")
        or data.get("reference")
        or data.get("booking_id")
        or ""
    )

    branch_data: dict = data.get("branch_data") or {}
    branch_name = data.get("branch_name") or branch_data.get("name") or "USHSPA"

    service_data: dict = data.get("service_data") or {}
    service_name = data.get("service_name") or service_data.get("name") or "Spa Service"

    therapist_data: dict = data.get("therapist_data") or {}
    therapist_name = (
        data.get("therapist_name")
        or therapist_data.get("name")
        or f"{therapist_data.get('first_name', '')} {therapist_data.get('last_name', '')}".strip()
        or ""
    )

    return {
        "booking_id": data.get("booking_id") or "",
        "booking_reference": booking_reference,
        "booking_number": data.get("booking_number") or booking_reference,
        "branch_name": branch_name,
        "service_name": service_name,
        "appointment_date": appointment_date,
        "appointment_time": appointment_time,
        "appointment_end_time": appointment_end_time,
        "therapist_name": therapist_name,
    }


def _whatsapp_updated_message_ar(context: dict, customer_name: str) -> str:
    """Compose Arabic WhatsApp booking updated/rescheduled message."""
    name = customer_name or "العميل العزيز"
    date_str = context.get("appointment_date") or ""
    time_str = context.get("appointment_time") or ""
    end_time_str = context.get("appointment_end_time") or ""
    time_range = f"{time_str}–{end_time_str}" if end_time_str else time_str

    lines = [
        "🔄 *تم تحديث موعد الحجز* — USHSPA",
        "",
        f"عزيزي {name}،",
        "",
        "تم تحديث تفاصيل حجزك بنجاح. التفاصيل الجديدة للموعد:",
        "",
        f"📅 *التاريخ الجديد:* {date_str}",
        f"🕐 *الوقت الجديد:* {time_range}",
    ]
    if context.get("therapist_name"):
        lines.append(f"💆 *المعالج/ة:* {context['therapist_name']}")
    if context.get("service_name"):
        lines.append(f"🧴 *الخدمة:* {context['service_name']}")
    if context.get("branch_name"):
        lines.append(f"📍 *الفرع:* {context['branch_name']}")
    ref = context.get("booking_number") or context.get("booking_reference")
    if ref:
        lines.append(f"🔖 *رقم الحجز:* {ref}")

    lines += [
        "",
        "نتطلع للترحيب بك في موعدك الجديد! يرجى الحضور قبل الموعد بـ 5 دقائق.",
        "",
        "لأي استفسار أو مساعدة، يمكنك التواصل معنا في أي وقت.",
        "— فريق USHSPA",
    ]
    return "\n".join(lines)


def _sms_updated_message_ar(context: dict, customer_name: str) -> str:
    """Compose concise Arabic SMS booking updated/rescheduled message."""
    name = customer_name.split()[0] if customer_name else "عميلنا"
    date_str = context.get("appointment_date") or ""
    time_str = context.get("appointment_time") or ""
    ref = context.get("booking_number") or context.get("booking_reference") or ""
    ref_part = f" رقم الحجز: {ref}" if ref else ""
    return (
        f"USHSPA:\n"
        f"مرحباً {name}، تم تحديث موعد حجزك إلى {date_str} الساعة {time_str}.{ref_part} نتشرف بزيارتكم!"
    )


def _whatsapp_updated_message_en(context: dict, customer_name: str) -> str:
    """Compose English WhatsApp booking updated/rescheduled message."""
    name = customer_name or "Valued Customer"
    date_str = context.get("appointment_date") or ""
    time_str = context.get("appointment_time") or ""
    end_time_str = context.get("appointment_end_time") or ""
    time_range = f"{time_str}–{end_time_str}" if end_time_str else time_str

    lines = [
        "🔄 *Booking Updated* — USHSPA",
        "",
        f"Dear {name},",
        "",
        "Your booking details have been successfully updated. Here are your updated appointment details:",
        "",
        f"📅 *New Date:* {date_str}",
        f"🕐 *New Time:* {time_range}",
    ]
    if context.get("therapist_name"):
        lines.append(f"💆 *Therapist:* {context['therapist_name']}")
    if context.get("service_name"):
        lines.append(f"🧴 *Service:* {context['service_name']}")
    if context.get("branch_name"):
        lines.append(f"📍 *Branch:* {context['branch_name']}")
    ref = context.get("booking_number") or context.get("booking_reference")
    if ref:
        lines.append(f"🔖 *Booking Ref:* {ref}")

    lines += [
        "",
        "We look forward to welcoming you at your updated appointment! Please arrive 5 minutes early.",
        "",
        "For assistance, feel free to contact us at any time.",
        "— The USHSPA Team",
    ]
    return "\n".join(lines)


def _sms_updated_message_en(context: dict, customer_name: str) -> str:
    """Compose concise English SMS booking updated/rescheduled message."""
    name = customer_name.split()[0] if customer_name else "Customer"
    date_str = context.get("appointment_date") or ""
    time_str = context.get("appointment_time") or ""
    ref = context.get("booking_number") or context.get("booking_reference") or ""
    ref_part = f" Booking Ref:{ref}" if ref else ""
    return (
        f"USHSPA:\n"
        f"Hi {name}, your booking has been UPDATED to {date_str} at {time_str}.{ref_part} See you soon!"
    )


class BookingUpdatedHandler:
    """Processes ``booking.updated`` and ``booking.rescheduled`` events.

    1. Updates appointment cache in ushauth.
    2. Sends SMS/WhatsApp notification to customer in Arabic (unless explicitly English).
    """

    event_type: str = "booking.updated"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = dict(envelope.data)
        booking_id: str = str(data.get("booking_id") or "")
        correlation_id: str = envelope.event_id_str or ""

        if not booking_id:
            logger.warning("booking_updated_missing_booking_id", event_id=correlation_id)
            return

        # ── Fetch full booking data if customer or service info missing ────
        if not data.get("customer_phone") or not data.get("customer_name") or not data.get("service_name"):
            booknpay_client = UshBookNPayClient()
            try:
                b_resp = await booknpay_client.get_booking(booking_id, correlation_id=correlation_id)
                b_data = b_resp.get("data") if isinstance(b_resp, dict) and "data" in b_resp else b_resp
                if isinstance(b_data, dict):
                    data = {**b_data, **data}
            except Exception as b_exc:
                logger.debug("booking_updated_fetch_booking_failed", booking_id=booking_id, error=str(b_exc))
            finally:
                await booknpay_client._client.aclose()

        appointment_date: str = str(data.get("appointment_date") or "")
        appointment_time: str = str(data.get("appointment_time") or "")
        duration: int = int(data.get("duration") or 0)
        therapist_id: str = str(data.get("therapist_id") or "")
        raw_status: str = str(data.get("status") or "").lower().strip()
        raw_payment_status: str = str(data.get("payment_status") or "").lower().strip()
        branch_id: str | None = data.get("branch_id")
        service_arrangement_id: str | None = data.get("service_arrangement_id")

        _STATUS_MAP = {
            "draft": "confirmed",
            "confirmed": "confirmed",
            "completed": "completed",
            "cancelled": "cancelled",
            "payment_pending": "confirmed",
            "requested": "pending",
            "reschedule_requested": "pending",
            "reschedule_approved": "confirmed",
            "payment_failed": "cancelled",
            "no_show": "completed",
            "blocked": "blocked",
            "approved": "approved",
            "pending": "pending",
        }
        _PAYMENT_STATUS_MAP = {
            "unpaid": "unpaid",
            "not_initiated": "unpaid",
            "pending": "pending",
            "initiated": "pending",
            "payment_pending": "pending",
            "success": "success",
            "paid": "success",
            "completed": "success",
            "refunded": "refunded",
            "rewarded": "rewarded",
            "failed": "failed",
            "cancelled": "cancelled",
        }

        cache_status: str = _STATUS_MAP.get(raw_status, raw_status or "confirmed")
        if raw_payment_status:
            cache_payment_status = _PAYMENT_STATUS_MAP.get(raw_payment_status, raw_payment_status)
        elif raw_status in ("payment_pending", "draft"):
            cache_payment_status = "pending"
        elif raw_status == "confirmed":
            cache_payment_status = "success"
        else:
            cache_payment_status = "unpaid"

        logger.info(
            "booking_updated_event_received",
            booking_id=booking_id,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            status=cache_status,
            payment_status=cache_payment_status,
            event_id=correlation_id,
        )

        # ── 1. Update appointment cache in ushauth ─────────────────────────
        ushauth_client = UshAuthClient()
        try:
            response = await ushauth_client.update_appointment_cache_by_booking_id(
                booking_id=booking_id,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                duration=duration,
                therapist_id=therapist_id,
                status=cache_status,
                payment_status=cache_payment_status,
                branch_id=branch_id,
                service_arrangement_id=service_arrangement_id,
                correlation_id=correlation_id,
                # Only sent for multi-service bookings (keeps the legacy call shape intact)
                **({"services": _svcs} if (_svcs := _cache_services(data)) else {}),
            )
            logger.info(
                "appointment_cache_updated_on_booking_updated_event",
                booking_id=booking_id,
                records_updated=response.get("records_updated", 0),
                event_id=correlation_id,
            )
        except Exception as exc:
            logger.error(
                "appointment_cache_update_failed_on_booking_updated_event",
                booking_id=booking_id,
                error=str(exc),
                event_id=correlation_id,
            )
            raise
        finally:
            await ushauth_client.aclose()

        # ── 2. Send SMS / WhatsApp notification to customer ────────────────
        # If the update changed the status to cancelled, cancel payment link if pending and skip notifications
        if cache_status == "cancelled" or raw_status in ("cancelled", "canceled"):
            payment_url = (
                data.get("payment_url")
                or (data.get("payment_data") or {}).get("payment_url")
                or (data.get("payments_data") or {}).get("payment_url")
                or data.get("invoice_url")
            )
            has_payment_link = bool(
                payment_url
                and str(payment_url).strip()
                and str(payment_url).strip().lower() not in ("none", "null")
            )
            old_pay_status = str(data.get("old_payment_status") or data.get("previous_payment_status") or "").lower().strip()
            is_paid_booking = (
                bool(data.get("refund_required"))
                or bool(data.get("refund_issued"))
                or raw_payment_status in ("success", "paid")
                or old_pay_status in ("success", "paid")
            )
            is_pending_payment = (
                old_pay_status in ("pending", "unpaid")
                or raw_payment_status in ("pending", "unpaid", "cancelled", "payment_pending")
            ) and not is_paid_booking

            if not has_payment_link and is_pending_payment and booking_id:
                try:
                    b_client = UshBookNPayClient()
                    b_resp = await b_client.get_booking(booking_id, correlation_id=correlation_id)
                    await b_client.aclose()
                    b_data = b_resp.get("data") if isinstance(b_resp, dict) and "data" in b_resp else b_resp
                    if isinstance(b_data, dict):
                        fetched_url = (
                            b_data.get("payment_url")
                            or (b_data.get("payment_data") or {}).get("payment_url")
                            or b_data.get("invoice_url")
                        )
                        if fetched_url and str(fetched_url).strip() and str(fetched_url).strip().lower() not in ("none", "null"):
                            has_payment_link = True
                except Exception as exc:
                    logger.debug("booking_updated_check_payment_url_failed", booking_id=booking_id, error=str(exc))

            if is_pending_payment and has_payment_link and booking_id:
                booknpay_client = UshBookNPayClient()
                try:
                    await booknpay_client.cancel_payment_link(
                        booking_id=booking_id,
                        correlation_id=correlation_id,
                    )
                    logger.info(
                        "booking_updated_cancelled_payment_link_cancelled",
                        booking_id=booking_id,
                        correlation_id=correlation_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "booking_updated_cancelled_payment_link_cancel_failed",
                        booking_id=booking_id,
                        error=str(exc),
                        correlation_id=correlation_id,
                    )
                finally:
                    await booknpay_client.aclose()

            logger.info("booking_updated_notification_skipped_cancelled", booking_id=booking_id)
            return

        # Check payment status: only send customer SMS/WhatsApp when payment_status=success
        is_payment_success = raw_payment_status in (
            "success",
            "paid",
            "completed",
            "successful",
            "rewarded",
        )
        if not is_payment_success:
            logger.info(
                "booking_updated_notification_skipped_payment_not_success",
                booking_id=booking_id,
                payment_status=raw_payment_status,
                message="SMS/WhatsApp messages sent only when payment_status=success",
            )
            return

        try:
            await self._send_updated_notifications(
                envelope=envelope,
                ctx=ctx,
                data=data,
                booking_id=booking_id,
                correlation_id=correlation_id,
            )
        except Exception as notify_exc:
            logger.warning(
                "booking_updated_notification_dispatch_failed",
                booking_id=booking_id,
                error=str(notify_exc),
            )

    async def _send_updated_notifications(
        self,
        *,
        envelope: EventEnvelope,
        ctx: HandlerContext,
        data: dict,
        booking_id: str,
        correlation_id: str,
    ) -> None:
        service = NotificationService(ctx)
        context = _build_update_context(data)
        customer_id: str = str(data.get("customer_id") or "")
        customer_name: str = str(
            data.get("customer_name")
            or (data.get("customer_data") or {}).get("name")
            or ""
        )

        # Language rule: Language must be Arabic unless explicitly told for English
        lang = ChannelResolver.extract_language(data, default_lang="ar")
        is_english = lang == "en"

        # Check WhatsApp verification
        whatsapp_verified = (
            bool(data.get("whatsapp_verified"))
            or bool(data.get("is_whatsapp_verified"))
            or bool(data.get("is_whatsapp_opted_in"))
            or bool((data.get("customer_data") or {}).get("whatsapp_verified"))
            or bool((data.get("customer_data") or {}).get("is_whatsapp_verified"))
        )
        if not whatsapp_verified and customer_id:
            try:
                auth_cli = UshAuthClient()
                prof = await auth_cli.get_customer_profile(customer_id, correlation_id=correlation_id)
                whatsapp_verified = bool(prof.get("whatsapp_verified") or prof.get("is_whatsapp_verified"))
                await auth_cli.aclose()
            except Exception:
                pass

        default_lang_to_use = "en" if is_english else "ar"
        whatsapp_sent = False

        if whatsapp_verified:
            wa_recipient = ChannelResolver.resolve_whatsapp_recipient(data, default_lang=default_lang_to_use)
            if wa_recipient:
                name = wa_recipient.name or customer_name
                wa_body = (
                    _whatsapp_updated_message_en(context, name)
                    if is_english
                    else _whatsapp_updated_message_ar(context, name)
                )
                req_wa = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=wa_recipient,
                    template_name="booking/updated_whatsapp",
                    template_context={
                        **context,
                        "customer_name": name,
                        "message_body": wa_body,
                        "language": "en" if is_english else "ar",
                    },
                    customer_id=customer_id or None,
                    booking_id=booking_id or None,
                    correlation_id=correlation_id,
                )
                try:
                    await service.send(req_wa)
                    whatsapp_sent = True
                    logger.info("booking_updated_whatsapp_sent", booking_id=booking_id, language=default_lang_to_use)
                except Exception as exc:
                    logger.warning("booking_updated_whatsapp_failed", booking_id=booking_id, error=str(exc))

        # Fallback to SMS if WhatsApp was not sent
        if not whatsapp_sent:
            sms_recipient = ChannelResolver.resolve_sms_recipient(data, default_lang=default_lang_to_use)
            if sms_recipient:
                name = sms_recipient.name or customer_name
                sms_body = (
                    _sms_updated_message_en(context, name)
                    if is_english
                    else _sms_updated_message_ar(context, name)
                )
                req_sms = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=sms_recipient,
                    template_name="booking/updated_sms",
                    template_context={
                        **context,
                        "customer_name": name,
                        "message_body": sms_body,
                        "language": "en" if is_english else "ar",
                    },
                    customer_id=customer_id or None,
                    booking_id=booking_id or None,
                    correlation_id=correlation_id,
                )
                try:
                    await service.send(req_sms)
                    logger.info("booking_updated_sms_sent", booking_id=booking_id, language=default_lang_to_use)
                except Exception as exc:
                    logger.warning("booking_updated_sms_failed", booking_id=booking_id, error=str(exc))
            else:
                logger.info("booking_updated_sms_skipped_no_recipient", booking_id=booking_id)
