"""
Shared session-formatting helpers for booking notification handlers.

Imported by booking_confirmed.py, booking_updated.py, and any future
handler that needs to render per-session time/therapist/service lines
in WhatsApp/SMS messages.
"""
from __future__ import annotations


def _fmt_time(iso_str: str) -> str:
    """Return 'H:MM AM/PM' from an ISO datetime or 'HH:MM:SS' string, or return as-is."""
    if not iso_str:
        return ""
    try:
        from datetime import datetime as _dt

        dt = _dt.fromisoformat(str(iso_str))
        return dt.strftime("%I:%M %p").lstrip("0")  # e.g. "2:00 PM"
    except (ValueError, TypeError):
        # Already "HH:MM" – return trimmed
        return str(iso_str)[:5]


def _session_lines_en(sessions: list[dict]) -> list[str]:
    """Return one bullet line per session for English messages.

    Example output:
        ["  Session 1: 2:00 PM–3:40 PM (Alice, Deep Tissue)", ...]
    """
    lines: list[str] = []
    for i, s in enumerate(sessions, 1):
        start = _fmt_time(s.get("appointment_start") or s.get("start_time") or "")
        end = _fmt_time(s.get("appointment_end") or s.get("end_time") or "")
        therapist = s.get("therapist_name") or s.get("therapist_id") or ""
        svc = s.get("service_name") or ""
        time_part = f"{start}–{end}" if end else start
        detail_parts = []
        if therapist:
            detail_parts.append(therapist)
        if svc:
            detail_parts.append(svc)
        detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
        lines.append(f"  Session {i}: {time_part}{detail}")
    return lines


def _session_lines_ar(sessions: list[dict]) -> list[str]:
    """Return one bullet line per session for Arabic messages.

    Example output:
        ["  الجلسة 1: 2:00 PM–3:40 PM (آليس، مساج عميق)", ...]
    """
    lines: list[str] = []
    for i, s in enumerate(sessions, 1):
        start = _fmt_time(s.get("appointment_start") or s.get("start_time") or "")
        end = _fmt_time(s.get("appointment_end") or s.get("end_time") or "")
        therapist = s.get("therapist_name") or s.get("therapist_id") or ""
        svc = s.get("service_name") or ""
        time_part = f"{start}–{end}" if end else start
        detail_parts = []
        if therapist:
            detail_parts.append(therapist)
        if svc:
            detail_parts.append(svc)
        detail = f" ({' ، '.join(detail_parts)})" if detail_parts else ""
        lines.append(f"  الجلسة {i}: {time_part}{detail}")
    return lines

