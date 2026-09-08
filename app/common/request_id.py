"""
Request ID middleware and utilities.

Injects a unique ``X-Request-ID`` header into every request and binds it
to the structlog context so it appears in all log records produced during
that request.
"""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import bind_request_context, clear_request_context

_REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that assigns a unique request ID to every request.

    If the incoming request already carries an ``X-Request-ID`` header, its
    value is used as-is (to support request tracing across service boundaries).
    Otherwise a new UUID4 is generated.

    The request ID is:
    - Bound to the structlog context for the duration of the request.
    - Added to the response headers.
    - Stored on ``request.state.request_id`` for downstream handlers.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())
        correlation_id = request.headers.get("X-Correlation-ID", "")

        request.state.request_id = request_id

        bind_request_context(
            request_id=request_id,
            correlation_id=correlation_id or None,
        )

        try:
            response = await call_next(request)
        finally:
            clear_request_context()

        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


def generate_request_id() -> str:
    """Generate a new UUID4-based request ID string."""
    return str(uuid.uuid4())
