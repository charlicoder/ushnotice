"""Helpers for multi-service booking events.

Events carry the full ordered list in ``services`` (one dict per booked service,
same branch and therapist).  Older producers only send the flat ``service_id`` /
``service_name`` / ``service_arrangement_id`` fields, so every helper falls back
to those – safe during a rolling deploy.
"""

from __future__ import annotations

from typing import Any


def event_services(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the event's service entries, synthesising one from legacy flat fields."""
    raw = data.get("services")
    if isinstance(raw, list) and raw and all(isinstance(s, dict) for s in raw):
        return raw
    sid = str(data.get("service_id") or "")
    if not sid and not data.get("service_name"):
        return []
    return [
        {
            "service_id": sid,
            "service_name": str(data.get("service_name") or ""),
            "service_arrangement_id": str(data.get("service_arrangement_id") or ""),
            "service_arrangement_name": str(data.get("service_arrangement_name") or ""),
        }
    ]


def service_names(data: dict[str, Any]) -> list[str]:
    names = []
    for s in event_services(data):
        sd = s.get("service_data") or {}
        n = s.get("service_name") or sd.get("name") or ""
        if n:
            names.append(str(n))
    return names


def _cache_services(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Compact per-service payload for ushauth's appointment cache (None if single/legacy)."""
    raw = data.get("services")
    if not (isinstance(raw, list) and len(raw) > 1):
        return None
    return [
        {
            "service_id": str(s.get("service_id") or "") or None,
            "service_name": str(s.get("service_name") or "") or None,
            "service_arrangement_id": str(s.get("service_arrangement_id") or "") or None,
            "service_arrangement_name": str(s.get("service_arrangement_name") or "") or None,
            "duration_minutes": int(s.get("duration_minutes") or 0),
        }
        for s in raw
        if isinstance(s, dict)
    ]
