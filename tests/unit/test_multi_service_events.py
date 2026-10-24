"""Multi-service booking events: helpers, invoice lines and legacy fallbacks."""

from __future__ import annotations

from unittest.mock import patch

from app.events.handlers.booking._services import (
    _cache_services,
    event_services,
    service_names,
)
from app.invoicing.invoice_helper import build_booking_invoice_lines


def _multi(price_a="40.000", price_b="20.000", arrangement="60.000"):
    return {
        "service_name": "Massage, Facial",
        "service_id": "s1",
        "arrangement_price": arrangement,
        "total_amount": arrangement,
        "services": [
            {"service_id": "s1", "service_name": "Massage", "base_price": price_a,
             "service_arrangement_id": "a1", "service_arrangement_name": "Room 1", "duration_minutes": 60},
            {"service_id": "s2", "service_name": "Facial", "base_price": price_b,
             "service_arrangement_id": "a2", "service_arrangement_name": "Room 2", "duration_minutes": 30},
        ],
    }


def test_event_services_falls_back_to_legacy_flat_fields():
    legacy = {"service_id": "s1", "service_name": "Solo", "service_arrangement_id": "a1"}
    entries = event_services(legacy)
    assert len(entries) == 1 and entries[0]["service_name"] == "Solo"
    assert service_names(legacy) == ["Solo"]
    assert event_services({}) == []


def test_service_names_for_multi_service_event():
    assert service_names(_multi()) == ["Massage", "Facial"]


def test_cache_services_only_for_multi_service_events():
    assert _cache_services({"service_id": "s1"}) is None
    assert _cache_services({"services": [{"service_id": "s1"}]}) is None
    payload = _cache_services(_multi())
    assert [p["service_id"] for p in payload] == ["s1", "s2"]
    assert payload[0]["duration_minutes"] == 60


def _lines(data):
    with patch("app.invoicing.invoice_helper.get_settings") as gs:
        gs.return_value.USHANR_REVENUE_ACCOUNT_ID = "rev"
        gs.return_value.USHANR_ADDON_ACCOUNT_ID = "add"
        return build_booking_invoice_lines(data)


def test_invoice_has_one_line_per_service_when_prices_reconcile():
    lines = _lines(_multi())
    assert [l["name"] for l in lines] == ["Massage", "Facial"]
    assert [l["unit_price"] for l in lines] == ["40.000", "20.000"]


def test_invoice_falls_back_to_single_line_when_prices_do_not_reconcile():
    lines = _lines(_multi(price_a="10.000", price_b="10.000", arrangement="60.000"))
    assert len(lines) == 1
    assert lines[0]["name"] == "Massage, Facial"
    assert lines[0]["unit_price"] == "60.000"


def test_invoice_single_service_unchanged():
    lines = _lines({"service_name": "Solo", "arrangement_price": "25.000", "total_amount": "25.000"})
    assert len(lines) == 1 and lines[0]["name"] == "Solo"
