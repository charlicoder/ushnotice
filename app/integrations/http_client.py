"""
Async HTTP client for inter-service communication through the API Gateway.

All outbound service calls MUST go through this client.  It enforces:
- USHSPA_TOKEN header injection on every request
- Request ID and Correlation ID propagation
- Configurable timeout
- Retry with exponential backoff for transient failures
- Structured logging (token is never logged)
- Safe error wrapping into domain exceptions

Never bypass this client to call upstream services directly.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import ServiceClientError, ServiceTimeoutError, ServiceUnavailableError
from app.core.logging import get_logger
from app.core.security import get_ushspa_token

logger = get_logger(__name__)

# HTTP status codes that are safe to retry
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class GatewayHttpClient:
    """Async HTTPX client pre-configured for API Gateway communication.

    Args:
        base_url: Base URL of the API Gateway (e.g. ``http://api.ushspa.local``).
        service_path: Service-specific path prefix (e.g. ``"/booknpay"``).
        service_name: Human-readable service name for logging and error attribution.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retries for transient failures.
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_path: str,
        service_name: str,
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_path = service_path.rstrip("/")
        self._service_name = service_name
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout)

    def _build_url(self, path: str) -> str:
        """Construct the full URL for a service path."""
        clean_path = path if path.startswith("/") else f"/{path}"
        return f"{self._base_url}{self._service_path}{clean_path}"

    def _build_headers(
        self,
        *,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, str]:
        """Build headers including auth token and tracing IDs."""
        return {
            "USHSPA-TOKEN": get_ushspa_token(),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Request-ID": request_id or str(uuid.uuid4()),
            "X-Correlation-ID": correlation_id or "",
            "X-Service": "ushnotice",
        }

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated GET request through the gateway."""
        return await self._request(
            "GET",
            path,
            params=params,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    async def post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated POST request through the gateway."""
        return await self._request(
            "POST",
            path,
            json=json,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    async def patch(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated PATCH request through the gateway."""
        return await self._request(
            "PATCH",
            path,
            json=json,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request with retry logic."""
        url = self._build_url(path)
        headers = self._build_headers(request_id=request_id, correlation_id=correlation_id)
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            start = time.monotonic()
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                )
                elapsed_ms = int((time.monotonic() - start) * 1000)

                logger.info(
                    "Gateway request completed",
                    service=self._service_name,
                    method=method,
                    path=path,
                    status_code=response.status_code,
                    latency_ms=elapsed_ms,
                    attempt=attempt,
                )

                if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue

                response.raise_for_status()
                return response.json()  # type: ignore[return-value]

            except httpx.TimeoutException as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "Gateway request timed out",
                    service=self._service_name,
                    method=method,
                    path=path,
                    attempt=attempt,
                    latency_ms=elapsed_ms,
                )
                last_exc = ServiceTimeoutError(
                    f"Timeout calling {self._service_name} {method} {path}",
                    service=self._service_name,
                )
                if attempt < self._max_retries:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)

            except httpx.HTTPStatusError as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                # Capture response body for non-retryable errors (especially 400 validation errors)
                try:
                    _resp_body = exc.response.text[:1000]
                except Exception:
                    _resp_body = "<unreadable>"
                logger.warning(
                    "Gateway request HTTP error",
                    service=self._service_name,
                    status_code=exc.response.status_code,
                    method=method,
                    path=path,
                    attempt=attempt,
                    response_body=_resp_body,
                )
                if exc.response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    last_exc = ServiceUnavailableError(
                        f"{self._service_name} returned {exc.response.status_code}",
                        service=self._service_name,
                        status_code=exc.response.status_code,
                    )
                    continue
                raise ServiceClientError(
                    f"{self._service_name} HTTP {exc.response.status_code}: {method} {path}",
                    service=self._service_name,
                    status_code=exc.response.status_code,
                ) from exc

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                break

        raise last_exc or ServiceClientError(
            f"Gateway request failed: {self._service_name} {method} {path}",
            service=self._service_name,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTPX client."""
        await self._client.aclose()
