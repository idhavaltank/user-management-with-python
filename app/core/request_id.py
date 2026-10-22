"""Correlation-ID middleware for logs and API responses."""

from __future__ import annotations

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.telemetry import Telemetry


def resolve_request_id(value: str | None) -> str:
    """Accept a safe caller ID or generate a new UUID when it is absent/unsafe."""

    if value and 0 < len(value) <= 128 and "\r" not in value and "\n" not in value:
        return value
    return str(uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach one request ID to request state, response headers, and telemetry."""

    def __init__(self, app, header_name: str, telemetry: Telemetry) -> None:
        """Configure the header name and event sink used by this middleware."""

        super().__init__(app)
        self.header_name = header_name
        self.telemetry = telemetry

    async def dispatch(self, request: Request, call_next) -> Response:
        """Capture request start/end events using the same correlation ID."""

        request_id = resolve_request_id(request.headers.get(self.header_name))
        request.state.request_id = request_id
        self.telemetry.info(
            "http.request.received",
            {"requestId": request_id, "method": request.method, "path": request.url.path},
        )

        response = await call_next(request)
        response.headers[self.header_name] = request_id
        self.telemetry.info(
            "http.request.completed",
            {"requestId": request_id, "statusCode": response.status_code},
        )
        return response
