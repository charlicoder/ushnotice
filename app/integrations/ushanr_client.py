"""
app/integrations/ushanr_client.py
───────────────────────────────────
Client for the ushanr accounting/invoicing service.

Calls internal endpoints directly (not through the API gateway) using a
shared X-Internal-Key header for service-to-service authentication.

Public interface
────────────────
  UshanrClient.ensure_partner(...)      → partner_id (UUID str)
  UshanrClient.create_invoice(...)      → dict with invoice_id, invoice_name
  UshanrClient.create_credit_note(...)  → dict with invoice_id, invoice_name
  UshanrClient.aclose()
"""
from __future__ import annotations

import time
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 15.0  # seconds — invoicing is low-latency-tolerant
_MAX_RETRIES = 2


class UshanrClient:
    """
    Async HTTP client for ushanr internal endpoints.

    Usage::

        client = UshanrClient()
        try:
            partner_id = await client.ensure_partner(...)
            invoice = await client.create_invoice(...)
        finally:
            await client.aclose()
    """

    def __init__(self) -> None:
        settings = get_settings()
        base_url = settings.USHANR_BASE_URL.rstrip("/")
        app_token = (
            settings.USHSPA_TOKEN.get_secret_value()
            if hasattr(settings.USHSPA_TOKEN, "get_secret_value")
            else str(settings.USHSPA_TOKEN)
        )
        self._api_key = settings.USHANR_INTERNAL_API_KEY or app_token
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=_TIMEOUT,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Key": self._api_key,
                "X-App-Token": app_token,
                "X-Service": "ushnotice",
            },
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST to ushanr with basic retry on 5xx / network errors."""
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            start = time.monotonic()
            try:
                response = await self._client.post(path, json=body)
                elapsed_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "ushanr_request_completed",
                    path=path,
                    status_code=response.status_code,
                    latency_ms=elapsed_ms,
                    attempt=attempt,
                )
                response.raise_for_status()
                return response.json()  # type: ignore[return-value]
            except httpx.HTTPStatusError as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                try:
                    body_text = exc.response.text[:500]
                except Exception:
                    body_text = "<unreadable>"
                logger.warning(
                    "ushanr_request_http_error",
                    path=path,
                    status_code=exc.response.status_code,
                    response_body=body_text,
                    attempt=attempt,
                )
                if exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    last_exc = exc
                    continue
                raise
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "ushanr_request_error",
                    path=path,
                    error=str(exc),
                    attempt=attempt,
                    latency_ms=elapsed_ms,
                )
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                break
        raise last_exc or RuntimeError(f"ushanr POST {path} failed after {_MAX_RETRIES} attempts")

    # ── Public API ─────────────────────────────────────────────────────────────

    async def ensure_partner(
        self,
        *,
        company_id: str,
        external_id: str,
        name: str = "",
        phone: str = "",
        email: str = "",
        currency_code: str = "KWD",
    ) -> str:
        """
        Idempotently get or create a Partner in ushanr.

        Returns the partner_id (UUID string).
        """
        body: dict[str, Any] = {
            "company_id": company_id,
            "external_id": external_id,
            "name": name or "",
            "phone": phone or "",
            "email": email or "",
            "currency_code": currency_code,
        }
        result = await self._post("/api/v1/internal/partners/ensure/", body)
        return str(result["partner_id"])

    async def create_invoice(
        self,
        *,
        company_id: str,
        partner_id: str,
        journal_id: str,
        invoice_date: date | str,
        source_document_type: str,
        source_document_id: str,
        source_document_ref: str = "",
        currency_code: str = "KWD",
        notes: str | None = None,
        lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Create and auto-post an invoice in ushanr from a source document.

        Each line dict must have at minimum:
            account_id, name, quantity, unit_price
        Optional: discount, tax_id, analytic_account_id, product_id

        Returns the response dict with invoice_id, invoice_name, state, amount_total.
        """
        if isinstance(invoice_date, date):
            invoice_date_str = invoice_date.isoformat()
        else:
            invoice_date_str = str(invoice_date)

        body: dict[str, Any] = {
            "company_id": company_id,
            "partner_id": partner_id,
            "journal_id": journal_id,
            "invoice_date": invoice_date_str,
            "source_document_type": source_document_type,
            "source_document_id": source_document_id,
            "source_document_ref": source_document_ref,
            "currency_code": currency_code,
            "notes": notes,
            "lines": [
                {
                    "account_id": str(ln["account_id"]),
                    "name": str(ln["name"]),
                    "quantity": str(ln.get("quantity", "1")),
                    "unit_price": str(ln["unit_price"]),
                    "discount": str(ln.get("discount", "0")),
                    "tax_id": str(ln["tax_id"]) if ln.get("tax_id") else None,
                    "analytic_account_id": (
                        str(ln["analytic_account_id"]) if ln.get("analytic_account_id") else None
                    ),
                    "product_id": ln.get("product_id"),
                }
                for ln in lines
            ],
        }
        return await self._post("/api/v1/internal/invoices/from-source/", body)

    async def create_credit_note(
        self,
        *,
        company_id: str,
        journal_id: str,
        source_document_type: str,
        source_document_id: str,
        notes: str | None = None,
        cancellation_date: date | str | None = None,
    ) -> dict[str, Any]:
        """
        Create a credit note reversing the invoice for a source document.

        Returns the response dict with invoice_id, invoice_name, state, amount_total.
        """
        body: dict[str, Any] = {
            "company_id": company_id,
            "journal_id": journal_id,
            "source_document_type": source_document_type,
            "source_document_id": source_document_id,
            "notes": notes,
            "cancellation_date": (
                cancellation_date.isoformat()
                if isinstance(cancellation_date, date)
                else cancellation_date
            ),
        }
        return await self._post("/api/v1/internal/invoices/credit-note/", body)

    async def aclose(self) -> None:
        """Close the underlying HTTPX client."""
        await self._client.aclose()
