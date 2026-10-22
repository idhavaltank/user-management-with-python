"""FastAPI application factory and cross-cutting request configuration.

The factory is injectable so tests can replace settings, database health, and
telemetry without changing production behavior.  It also owns startup/shutdown
resource lifecycles for SQLAlchemy and Redis.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.users import router as users_router
from app.core.bootstrap import ensure_bootstrap_admin
from app.core.config import Settings, get_settings
from app.core.database import check_database, create_engine, create_session_factory
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimitMiddleware, RedisRateLimitStore
from app.core.request_id import RequestIdMiddleware
from app.core.telemetry import LoggingTelemetry, Telemetry

DatabaseCheck = Callable[[], Awaitable[bool]]


def _request_id(request: Request) -> str:
    """Read the middleware correlation ID or create a safe fallback."""

    return getattr(request.state, "request_id", str(uuid4()))


def _error_payload(
    request: Request, code: str, message: str, details: Any = None
) -> dict[str, Any]:
    """Build the public error envelope shared by all exception handlers."""

    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "requestId": _request_id(request),
    }
    if details is not None:
        payload["details"] = details
    return payload


def _safe_validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Expose useful validation locations without leaking raw exception data."""

    return [
        {
            "loc": list(error.get("loc", [])),
            "message": error.get("msg", "Invalid value"),
            "type": error.get("type", "validation_error"),
        }
        for error in exc.errors()
    ]


def create_app(
    settings: Settings | None = None,
    database_check: DatabaseCheck | None = None,
    telemetry: Telemetry | None = None,
) -> FastAPI:
    """Construct a configured FastAPI application instance."""

    configure_logging()
    app_settings = settings or get_settings()
    app_telemetry = telemetry or LoggingTelemetry()
    # Foundation tests can omit a real engine while still exercising HTTP
    # middleware and error contracts.
    engine = create_engine(app_settings) if database_check is None else None
    session_factory = create_session_factory(engine) if engine is not None else None
    # Redis is optional locally; production validation requires it when rate
    # limiting is enabled so multiple API instances share counters.
    redis_client = (
        Redis.from_url(app_settings.redis_url, decode_responses=True)
        if app_settings.redis_url
        else None
    )
    rate_limit_store = RedisRateLimitStore(redis_client) if redis_client else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Start optional bootstrap work and dispose external resources cleanly."""

        app.state.telemetry.info(
            "application.started",
            {"environment": app_settings.environment},
        )
        if app.state.session_factory is not None:
            await ensure_bootstrap_admin(app_settings, app.state.session_factory, app_telemetry)
        yield
        if app.state.engine is not None:
            await app.state.engine.dispose()
        if app.state.redis is not None:
            await app.state.redis.aclose()
        app.state.telemetry.info("application.stopped")

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.telemetry = app_telemetry
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.redis = redis_client
    app.state.database_check = database_check or (lambda: check_database(engine))

    # Convert FastAPI/Starlette HTTP errors into the documented error shape.
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Normalize explicit HTTP exceptions and preserve their status code."""

        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code", f"http_{exc.status_code}")
        message = detail.get("message", str(exc.detail))
        app_telemetry.warning(
            "http.request.rejected",
            {"requestId": _request_id(request), "statusCode": exc.status_code, "code": code},
        )
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=_error_payload(request, code, message, detail.get("details")),
        )

    # Pydantic/FastAPI validation errors are normalized for frontend handling.
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Normalize Pydantic request errors without exposing implementation details."""

        details = _safe_validation_details(exc)
        app_telemetry.warning(
            "http.request.validation_failed",
            {"requestId": _request_id(request), "errorCount": len(details)},
        )
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                request, "validation_error", "Request validation failed.", details
            ),
        )

    # Unexpected exceptions receive a generic response; details stay in logs.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Log unexpected failures and return a safe generic 500 response."""

        app_telemetry.error(
            "http.request.failed",
            {"requestId": _request_id(request)},
            error=exc,
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload(request, "internal_server_error", "An internal error occurred."),
        )

    # Middleware is registered here so every router gets the same host, rate,
    # correlation, and browser-origin protections.
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=app_settings.trusted_hosts,
    )
    app.add_middleware(
        RateLimitMiddleware,
        settings=app_settings,
        telemetry=app_telemetry,
        store=rate_limit_store,
    )
    app.add_middleware(
        RequestIdMiddleware,
        header_name=app_settings.request_id_header,
        telemetry=app_telemetry,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", app_settings.request_id_header, "X-CSRF-Token"],
    )

    # Routers contain endpoint-specific behavior; the factory composes them
    # into one application and keeps the public route map centralized.
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    return app


app = create_app()
