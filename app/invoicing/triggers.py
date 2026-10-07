"""
app/invoicing/triggers.py
───────────────────────────
Async trigger functions called by ushnotice event handlers to create invoices
and credit notes in ushanr. All functions are non-blocking on failure — they
log a warning and return None rather than raising, so notification flow is
never interrupted by accounting errors.

Functions
─────────
  trigger_booking_invoice(data, correlation_id)
  trigger_shop_order_invoice(data, correlation_id)
  trigger_gift_purchase_invoice(data, correlation_id)
  trigger_voucher_invoice(data, correlation_id)
  trigger_credit_note(source_type, source_id, notes, correlation_id)
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.ushanr_client import UshanrClient
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.invoicing.invoice_helper import (
    build_booking_invoice_lines,
    build_gift_purchase_invoice_lines,
    build_shop_order_invoice_lines,
    build_voucher_invoice_lines,
)

logger = get_logger(__name__)


def _get_ids() -> tuple[str, str, str]:
    """Return (company_id, ar_journal_id, revenue_account_id) from settings."""
    s = get_settings()
    return s.USHANR_COMPANY_ID, s.USHANR_AR_JOURNAL_ID, s.USHANR_REVENUE_ACCOUNT_ID


def _invoicing_enabled() -> bool:
    """True when all three required ushanr UUID settings are configured."""
    company_id, journal_id, revenue_acct = _get_ids()
    return bool(company_id and journal_id and revenue_acct)


async def _link_invoice_to_payments(
    source_type: str, source_id: str, result: dict[str, Any], correlation_id: str | None = None
) -> None:
    """Push the new invoice number onto the payments of the source document (non-blocking)."""
    invoice_number = result.get("invoice_name")
    if not invoice_number:
        return
    pay_client = UshBookNPayClient()
    try:
        await pay_client.link_payment_invoice(
            source_type=source_type,
            source_id=str(source_id),
            invoice_number=invoice_number,
            correlation_id=correlation_id,
        )
        if source_type == "booking":
            try:
                await pay_client.link_booking_invoice(
                    booking_id=str(source_id),
                    invoice_number=invoice_number,
                    correlation_id=correlation_id,
                )
            except Exception as b_exc:
                logger.debug("booking_invoice_direct_link_warning", booking_id=str(source_id), error=str(b_exc))
    except Exception as exc:

        logger.warning(
            "payment_invoice_link_failed",
            source_type=source_type,
            source_id=str(source_id),
            error=str(exc),
        )
    finally:
        try:
            await pay_client.aclose()
        except Exception:
            pass


async def trigger_booking_invoice(
    data: dict[str, Any],
    correlation_id: str | None = None,
) -> None:
    """
    Create an invoice in ushanr for a confirmed, paid booking.

    Non-blocking: any error is logged as a warning and swallowed.
    """
    if not _invoicing_enabled():
        logger.debug("ushanr_invoicing_skipped_not_configured")
        return

    booking_id = str(data.get("booking_id") or "")
    if not booking_id:
        return

    company_id, journal_id, _ = _get_ids()
    settings = get_settings()

    customer_id = str(data.get("customer_id") or "")
    customer_data: dict = data.get("customer_data") or {}
    customer_name = (
        data.get("customer_name")
        or customer_data.get("name")
        or ""
    )
    customer_phone = (
        data.get("customer_phone")
        or customer_data.get("phone")
        or customer_data.get("phone_number")
        or customer_data.get("contact_number")
        or ""
    )
    customer_email = (
        data.get("customer_email")
        or customer_data.get("email")
        or ""
    )

    booking_number = str(data.get("booking_number") or "")
    appointment_date = (
        data.get("appointment_date")
        or data.get("appointment_start", "")
    )

    # Invoice date is always the date the invoice record is created
    try:
        if appointment_date:
            inv_date_str = date.today().isoformat()  # invoice date = creation date
        else:
            inv_date_str = date.today().isoformat()
    except Exception:
        inv_date_str = date.today().isoformat()

    lines = build_booking_invoice_lines(data)
    if not lines:
        logger.warning("trigger_booking_invoice_no_lines", booking_id=booking_id)
        return

    notes = f"Booking {booking_number}" if booking_number else f"Booking {booking_id}"
    service_name = data.get("service_name") or ""
    if service_name:
        notes += f" — {service_name}"

    client = UshanrClient()
    try:
        # 1. Ensure the customer has a Partner record in ushanr
        partner_id = await client.ensure_partner(
            company_id=company_id,
            external_id=customer_id,
            name=customer_name,
            phone=customer_phone,
            email=customer_email,
        )

        # 2. Create + post the invoice
        result = await client.create_invoice(
            company_id=company_id,
            partner_id=partner_id,
            journal_id=journal_id,
            invoice_date=inv_date_str,
            source_document_type="booking",
            source_document_id=booking_id,
            source_document_ref=booking_number,
            currency_code="KWD",
            notes=notes,
            lines=lines,
        )
        await _link_invoice_to_payments("booking", booking_id, result)
        logger.info(
            "booking_invoice_created",
            invoice_id=result.get("invoice_id"),
            invoice_name=result.get("invoice_name"),
            booking_id=booking_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.warning(
            "booking_invoice_failed",
            booking_id=booking_id,
            error=str(exc),
            correlation_id=correlation_id,
        )
    finally:
        await client.aclose()


async def trigger_shop_order_invoice(
    data: dict[str, Any],
    correlation_id: str | None = None,
) -> None:
    """
    Create an invoice in ushanr for a paid shop order.

    Non-blocking: any error is logged as a warning and swallowed.
    """
    if not _invoicing_enabled():
        return

    order_id = str(data.get("order_id") or "")
    if not order_id:
        return

    company_id, journal_id, _ = _get_ids()
    customer_id = str(data.get("customer_id") or "")
    customer_data: dict = data.get("customer_data") or {}
    customer_name = data.get("customer_name") or customer_data.get("name") or ""
    customer_phone = (
        data.get("customer_phone")
        or customer_data.get("phone")
        or customer_data.get("contact_number")
        or ""
    )

    order_number = str(data.get("order_number") or "")
    lines = build_shop_order_invoice_lines(data)
    if not lines:
        logger.warning("trigger_shop_order_invoice_no_lines", order_id=order_id)
        return

    client = UshanrClient()
    try:
        partner_id = await client.ensure_partner(
            company_id=company_id,
            external_id=customer_id,
            name=customer_name,
            phone=customer_phone,
        )
        result = await client.create_invoice(
            company_id=company_id,
            partner_id=partner_id,
            journal_id=journal_id,
            invoice_date=date.today().isoformat(),
            source_document_type="shop_order",
            source_document_id=order_id,
            source_document_ref=order_number,
            currency_code=str(data.get("currency") or "KWD"),
            notes=f"Shop Order {order_number}" if order_number else f"Shop Order {order_id}",
            lines=lines,
        )
        await _link_invoice_to_payments("shop_order", order_id, result)
        logger.info(
            "shop_order_invoice_created",
            invoice_id=result.get("invoice_id"),
            invoice_name=result.get("invoice_name"),
            order_id=order_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.warning(
            "shop_order_invoice_failed",
            order_id=order_id,
            error=str(exc),
            correlation_id=correlation_id,
        )
    finally:
        await client.aclose()


async def trigger_gift_purchase_invoice(
    data: dict[str, Any],
    correlation_id: str | None = None,
) -> None:
    """
    Create an invoice for a gift voucher purchase (gift.purchase.completed).

    Non-blocking: any error is logged as a warning and swallowed.
    """
    if not _invoicing_enabled():
        return

    purchase_id = str(data.get("id") or "")
    if not purchase_id:
        return

    company_id, journal_id, _ = _get_ids()

    sender_id = str(data.get("sender_id") or "")
    sender_data: dict = data.get("sender_data") or {}
    sender_name = sender_data.get("name") or data.get("sender_name") or ""
    sender_phone = sender_data.get("phone_number") or sender_data.get("phone") or ""

    public_token = str(data.get("public_token") or "")
    lines = build_gift_purchase_invoice_lines(data)
    if not lines:
        return

    client = UshanrClient()
    try:
        partner_id = await client.ensure_partner(
            company_id=company_id,
            external_id=sender_id or purchase_id,
            name=sender_name,
            phone=sender_phone,
        )
        result = await client.create_invoice(
            company_id=company_id,
            partner_id=partner_id,
            journal_id=journal_id,
            invoice_date=date.today().isoformat(),
            source_document_type="gift_voucher_purchase",
            source_document_id=purchase_id,
            source_document_ref=public_token,
            currency_code="KWD",
            notes=f"Gift Voucher Purchase{f' — {public_token}' if public_token else ''}",
            lines=lines,
        )
        await _link_invoice_to_payments("gift_voucher_purchase", purchase_id, result)
        logger.info(
            "gift_purchase_invoice_created",
            invoice_id=result.get("invoice_id"),
            invoice_name=result.get("invoice_name"),
            purchase_id=purchase_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.warning(
            "gift_purchase_invoice_failed",
            purchase_id=purchase_id,
            error=str(exc),
            correlation_id=correlation_id,
        )
    finally:
        await client.aclose()


async def trigger_voucher_invoice(
    data: dict[str, Any],
    correlation_id: str | None = None,
) -> None:
    """
    Create an invoice for a classic gift voucher purchase (voucher.active).

    Non-blocking: any error is logged as a warning and swallowed.
    """
    if not _invoicing_enabled():
        return

    voucher_id = str(data.get("id") or data.get("voucher_id") or "")
    if not voucher_id:
        return

    company_id, journal_id, _ = _get_ids()

    sender_details: dict = data.get("sender_details") or data.get("sender_data") or {}
    sender_id = str(data.get("sender_id") or sender_details.get("id") or "")
    sender_name = sender_details.get("name") or data.get("sender_name") or ""
    sender_phone = (
        sender_details.get("phone_number")
        or sender_details.get("phone")
        or ""
    )

    voucher_number = str(data.get("voucher_number") or "")
    lines = build_voucher_invoice_lines(data)
    if not lines:
        return

    client = UshanrClient()
    try:
        partner_id = await client.ensure_partner(
            company_id=company_id,
            external_id=sender_id or voucher_id,
            name=sender_name,
            phone=sender_phone,
        )
        result = await client.create_invoice(
            company_id=company_id,
            partner_id=partner_id,
            journal_id=journal_id,
            invoice_date=date.today().isoformat(),
            source_document_type="gift_voucher",
            source_document_id=voucher_id,
            source_document_ref=voucher_number,
            currency_code="KWD",
            notes=f"Gift Voucher{f' {voucher_number}' if voucher_number else ''}",
            lines=lines,
        )
        await _link_invoice_to_payments("gift_voucher", voucher_id, result)
        logger.info(
            "voucher_invoice_created",
            invoice_id=result.get("invoice_id"),
            invoice_name=result.get("invoice_name"),
            voucher_id=voucher_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.warning(
            "voucher_invoice_failed",
            voucher_id=voucher_id,
            error=str(exc),
            correlation_id=correlation_id,
        )
    finally:
        await client.aclose()


async def trigger_credit_note(
    *,
    source_document_type: str,
    source_document_id: str,
    notes: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """
    Create a credit note in ushanr reversing the invoice for the given source document.

    Called on booking cancellations and payment refunds.
    Non-blocking: any error is logged as a warning and swallowed.
    """
    if not _invoicing_enabled():
        return

    if not source_document_id:
        return

    company_id, journal_id, _ = _get_ids()

    client = UshanrClient()
    try:
        result = await client.create_credit_note(
            company_id=company_id,
            journal_id=journal_id,
            source_document_type=source_document_type,
            source_document_id=source_document_id,
            notes=notes,
            cancellation_date=date.today(),
        )
        logger.info(
            "credit_note_created",
            credit_note_id=result.get("invoice_id"),
            credit_note_name=result.get("invoice_name"),
            source_type=source_document_type,
            source_id=source_document_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.warning(
            "credit_note_failed",
            source_type=source_document_type,
            source_id=source_document_id,
            error=str(exc),
            correlation_id=correlation_id,
        )
    finally:
        await client.aclose()
