"""
app/invoicing/invoice_helper.py
─────────────────────────────────
Shared helpers for building ushanr invoice payloads from ushbooknpay event data.

All functions are pure (no I/O) and return structured dicts that the
UshanrClient.create_invoice() method accepts.

Configuration (from env):
  USHANR_COMPANY_ID     — UUID of the USHSPA company in ushanr
  USHANR_AR_JOURNAL_ID  — UUID of the Accounts Receivable journal in ushanr
  USHANR_REVENUE_ACCOUNT_ID — UUID of the primary revenue account (services)
  USHANR_ADDON_ACCOUNT_ID   — UUID of the add-ons revenue account (optional,
                               falls back to USHANR_REVENUE_ACCOUNT_ID)
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.timezone import local_today

logger = get_logger(__name__)

_ZERO = Decimal("0")
_PREC = Decimal("0.001")


def _d(value: Any, default: Decimal = _ZERO) -> Decimal:
    """Safely coerce a value to Decimal, returning default on failure."""
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value)).quantize(_PREC)
    except (InvalidOperation, ValueError):
        return default


def _today_str() -> str:
    return local_today().isoformat()


def _parse_date(value: Any) -> str:
    """Return an ISO date string from various input formats."""
    if not value:
        return _today_str()
    try:
        if isinstance(value, (date, datetime)):
            return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
        # Try ISO string
        s = str(value).strip()
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
        # date-only
        return date.fromisoformat(s[:10]).isoformat()
    except Exception:
        return _today_str()


# ── Booking invoice builder ────────────────────────────────────────────────────


def build_booking_invoice_lines(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build invoice line items from a booking.confirmed event payload.

    Lines included (all using USHANR_REVENUE_ACCOUNT_ID):
      - Main service (arrangement_price or total_amount)
      - Add-ons (addon_price, if > 0)
      - Extra time (price_for_extra_minutes, if > 0)
      - Discount (negative line, if discount > 0)
      - Fees (fees, if > 0)

    For loyalty / rewarded bookings: one line at 0.000 KWD.
    For gift voucher bookings: service line + negative redemption line (net zero).
    """
    settings = get_settings()
    revenue_acct = settings.USHANR_REVENUE_ACCOUNT_ID
    addon_acct = getattr(settings, "USHANR_ADDON_ACCOUNT_ID", None) or revenue_acct

    payment_type = str(data.get("payment_type") or "").lower()
    pricing: dict = data.get("pricing") or {}
    service_data: dict = data.get("service_data") or {}
    service_name = (
        data.get("service_name")
        or service_data.get("name")
        or "Spa Service"
    )

    lines: list[dict[str, Any]] = []

    if payment_type == "rewarded":
        # Loyalty redemption — full service value, zero charge
        total = _d(
            data.get("total_amount")
            or pricing.get("total")
            or data.get("arrangement_price")
        )
        lines.append({
            "account_id": revenue_acct,
            "name": f"{service_name} — Loyalty Reward Redemption",
            "quantity": "1",
            "unit_price": str(total),
            "discount": "100",  # 100% discount → zero net
        })
        return lines

    if payment_type == "gift_voucher":
        # Gift voucher redemption — service at full price then offset by voucher line
        total = _d(
            data.get("total_amount")
            or pricing.get("total")
            or data.get("arrangement_price")
        )
        lines.append({
            "account_id": revenue_acct,
            "name": service_name,
            "quantity": "1",
            "unit_price": str(total),
        })
        voucher_number = str(data.get("voucher_number") or data.get("voucher_id") or "")
        redemption_label = f"Gift Voucher Redemption{f' — {voucher_number}' if voucher_number else ''}"
        lines.append({
            "account_id": revenue_acct,
            "name": redemption_label,
            "quantity": "1",
            "unit_price": str(total),
            "discount": "100",  # effectively a zero-out credit
        })
        return lines

    # Standard paid booking
    arrangement_price = _d(
        data.get("arrangement_price")
        or pricing.get("arrangement_price")
        or data.get("total_amount")
        or pricing.get("total")
    )
    lines.append({
        "account_id": revenue_acct,
        "name": service_name,
        "quantity": "1",
        "unit_price": str(arrangement_price),
    })

    addon_price = _d(data.get("addon_price") or pricing.get("addon_price"))
    if addon_price > _ZERO:
        addons = data.get("addons") or []
        addon_name = (
            ", ".join(a.get("name", "") for a in addons if isinstance(a, dict) and a.get("name"))
            if addons
            else "Add-ons"
        )
        lines.append({
            "account_id": addon_acct,
            "name": addon_name,
            "quantity": "1",
            "unit_price": str(addon_price),
        })

    extra_price = _d(
        data.get("price_for_extra_minutes")
        or data.get("price_for_extra_time")
        or pricing.get("price_for_extra_minutes")
    )
    if extra_price > _ZERO:
        extra_mins = data.get("extra_minutes") or data.get("extra_time") or ""
        lines.append({
            "account_id": revenue_acct,
            "name": f"Extra Time{f' ({extra_mins} min)' if extra_mins else ''}",
            "quantity": "1",
            "unit_price": str(extra_price),
        })

    discount = _d(data.get("discount") or pricing.get("discount"))
    if discount > _ZERO:
        lines.append({
            "account_id": revenue_acct,
            "name": "Discount",
            "quantity": "1",
            "unit_price": str(discount),
            "discount": "100",  # the discount line is itself a 100% -off line
        })

    fees = _d(data.get("fees") or pricing.get("fees"))
    if fees > _ZERO:
        lines.append({
            "account_id": revenue_acct,
            "name": "Service Fees",
            "quantity": "1",
            "unit_price": str(fees),
        })

    return lines


# ── Shop order invoice builder ─────────────────────────────────────────────────


def build_shop_order_invoice_lines(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build invoice line items from a shop.order_created event payload.

    One line per order item, plus a discount line if applicable.
    """
    settings = get_settings()
    revenue_acct = settings.USHANR_REVENUE_ACCOUNT_ID

    items: list[dict] = data.get("items") or []
    lines: list[dict[str, Any]] = []

    if items:
        for item in items:
            if not isinstance(item, dict):
                continue
            product_name = (
                item.get("product_name")
                or item.get("name")
                or "Product"
            )
            unit_price = _d(item.get("unit_price") or item.get("price"))
            quantity = _d(item.get("quantity") or "1", Decimal("1"))
            lines.append({
                "account_id": revenue_acct,
                "name": product_name,
                "quantity": str(quantity),
                "unit_price": str(unit_price),
                "product_id": str(item.get("product_id") or ""),
            })
    else:
        # Fallback: single line for total amount
        total = _d(data.get("total_amount"))
        lines.append({
            "account_id": revenue_acct,
            "name": "Shop Order",
            "quantity": "1",
            "unit_price": str(total),
        })

    discount = _d(data.get("discount"))
    if discount > _ZERO:
        lines.append({
            "account_id": revenue_acct,
            "name": "Order Discount",
            "quantity": "1",
            "unit_price": str(discount),
            "discount": "100",
        })

    return lines


# ── Gift voucher purchase invoice builder ──────────────────────────────────────


def build_gift_purchase_invoice_lines(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build invoice lines from a gift.purchase.completed event.
    One line per purchase item if available, else one total line.
    """
    settings = get_settings()
    revenue_acct = settings.USHANR_REVENUE_ACCOUNT_ID

    items: list[dict] = data.get("items") or []
    lines: list[dict[str, Any]] = []

    if items:
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("service_name") or item.get("name") or "Gift Voucher"
            unit_price = _d(item.get("price") or item.get("total_amount"))
            lines.append({
                "account_id": revenue_acct,
                "name": f"Gift Voucher — {name}",
                "quantity": "1",
                "unit_price": str(unit_price),
            })
    else:
        total = _d(data.get("total_amount"))
        ref = str(data.get("public_token") or data.get("id") or "")
        lines.append({
            "account_id": revenue_acct,
            "name": f"Gift Voucher Purchase{f' — {ref}' if ref else ''}",
            "quantity": "1",
            "unit_price": str(total),
        })

    return lines


# ── Classic voucher invoice builder ────────────────────────────────────────────


def build_voucher_invoice_lines(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build invoice lines from a voucher.active event (classic single GiftVoucher).
    """
    settings = get_settings()
    revenue_acct = settings.USHANR_REVENUE_ACCOUNT_ID

    service_data: dict = data.get("service_data") or {}
    service_name = (
        data.get("service_name")
        or service_data.get("name")
        or "Gift Voucher"
    )
    total = _d(data.get("total_amount"))
    voucher_number = str(data.get("voucher_number") or "")

    return [{
        "account_id": revenue_acct,
        "name": f"Gift Voucher — {service_name}{f' ({voucher_number})' if voucher_number else ''}",
        "quantity": "1",
        "unit_price": str(total),
    }]
