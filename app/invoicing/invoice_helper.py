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

    booking_type = str(data.get("booking_type") or "").lower()
    if payment_type == "rewarded" or booking_type == "loyalty" or bool(data.get("loyalty_data")):
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

    if payment_type == "gift_voucher" or bool(data.get("voucher_id")):
        # Gift vouchers are already paid and invoiced at purchase; no invoice lines are generated for redemption
        return []

    # Standard paid booking
    arrangement_price = _d(
        data.get("arrangement_price")
        or pricing.get("arrangement_price")
        or data.get("total_amount")
        or pricing.get("total")
    )
    # Multi-service booking: one line per service, but only when the per-service
    # prices reconcile with the booking's arrangement price (otherwise a single line).
    svc_entries = [
        e for e in (data.get("services") or []) if isinstance(e, dict)
    ]
    per_service_lines: list[dict[str, Any]] = []
    if len(svc_entries) > 1:
        entry_prices = [_d(e.get("base_price")) for e in svc_entries]
        if sum(entry_prices, _ZERO) > _ZERO and abs(sum(entry_prices, _ZERO) - arrangement_price) <= Decimal("0.001"):
            for e, price in zip(svc_entries, entry_prices):
                per_service_lines.append({
                    "account_id": revenue_acct,
                    "name": str(
                        e.get("service_name")
                        or (e.get("service_data") or {}).get("name")
                        or "Spa Service"
                    ),
                    "quantity": "1",
                    "unit_price": str(price),
                })
    if per_service_lines:
        lines.extend(per_service_lines)
    else:
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

    fees = _d(data.get("fees") or pricing.get("fees"))
    if fees > _ZERO:
        lines.append({
            "account_id": revenue_acct,
            "name": "Service Fees",
            "quantity": "1",
            "unit_price": str(fees),
        })

    target_total = _d(data.get("total_amount") or pricing.get("total"))
    discount = _d(data.get("discount") or pricing.get("discount"))

    # Net discount deduction: In ushanr, lines cannot have negative amounts, and discount
    # field is a percentage (0..100). To ensure sum of line subtotals equals net total_amount,
    # apply the discount directly against the line unit prices.
    if discount > _ZERO and lines:
        rem_discount = discount
        for line in lines:
            line_price = _d(line.get("unit_price"))
            if line_price > _ZERO and rem_discount > _ZERO:
                deduction = min(line_price, rem_discount)
                line["unit_price"] = str((line_price - deduction).quantize(Decimal("0.001")))
                rem_discount -= deduction
                if rem_discount <= _ZERO:
                    break

    # If target_total is positive, reconcile any fractional rounding difference (e.g. 0.001 KWD)
    if target_total > _ZERO and lines:
        current_sum = sum(_d(l.get("unit_price")) * _d(l.get("quantity", "1")) for l in lines)
        diff = target_total - current_sum
        if abs(diff) <= Decimal("0.010") and diff != _ZERO:
            for line in lines:
                lp = _d(line.get("unit_price"))
                if lp + diff >= _ZERO:
                    line["unit_price"] = str((lp + diff).quantize(Decimal("0.001")))
                    break

    return lines


# ── Shop order invoice builder ─────────────────────────────────────────────────


def build_shop_order_invoice_lines(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build invoice line items from a shop.order_created event payload.

    One line per order item, adjusted for discounts so invoice total matches total_amount.
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

    target_total = _d(data.get("total_amount"))
    discount = _d(data.get("discount"))
    if discount > _ZERO and lines:
        rem_discount = discount
        for line in lines:
            qty = _d(line.get("quantity", "1"))
            line_tot = _d(line.get("unit_price")) * qty
            if line_tot > _ZERO and rem_discount > _ZERO:
                deduction = min(line_tot, rem_discount)
                new_unit_price = (line_tot - deduction) / qty
                line["unit_price"] = str(new_unit_price.quantize(Decimal("0.001")))
                rem_discount -= deduction
                if rem_discount <= _ZERO:
                    break

    if target_total > _ZERO and lines:
        current_sum = sum(_d(l.get("unit_price")) * _d(l.get("quantity", "1")) for l in lines)
        diff = target_total - current_sum
        if abs(diff) <= Decimal("0.010") and diff != _ZERO:
            for line in lines:
                lp = _d(line.get("unit_price"))
                if lp + diff >= _ZERO:
                    line["unit_price"] = str((lp + diff).quantize(Decimal("0.001")))
                    break

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
