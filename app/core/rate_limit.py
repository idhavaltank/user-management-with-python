"""Per-client request limiting with local and Redis-backed implementations."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.telemetry import Telemetry


@dataclass(frozen=True)
class RateLimitResult:
    """The decision and response metadata produced by a rate-limit store."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RateLimitStore(Protocol):
    """Small interface shared by the local and multi-instance stores."""

    async def consume(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """Consume one request slot and return the resulting limit decision."""

        ...


class InMemoryRateLimitStore:
    """Single-process fixed-window limiter used locally and in tests."""

    def __init__(self) -> None:
        """Initialize counters and the lock protecting them."""

        self._entries: dict[str, tuple[float, int]] = {}
        self._lock = asyncio.Lock()

    async def consume(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """Increment one key atomically within the asyncio process."""

        now = time.monotonic()
        async with self._lock:
            expires_at, count = self._entries.get(key, (0.0, 0))
            if expires_at <= now:
                expires_at = now + window_seconds
                count = 0

            count += 1
            self._entries[key] = (expires_at, count)
            remaining = max(limit - count, 0)
            retry_after = max(int(expires_at - now), 1)
            return RateLimitResult(count <= limit, limit, remaining, retry_after)


class RedisRateLimitStore:
    """Redis-backed limiter for horizontally scaled deployments."""

    def __init__(self, redis: Redis) -> None:
        """Keep the shared Redis client supplied by the application factory."""

        self.redis = redis

    async def consume(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """Increment and expire a Redis counter using one atomic Lua script."""

        result = await self.redis.eval(
            """
            local count = redis.call('INCR', KEYS[1])
            if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
            local ttl = redis.call('TTL', KEYS[1])
            return {count, ttl}
            """,
            1,
            key,
            window_seconds,
        )
        count = int(result[0])
        ttl = int(result[1])
        retry_after = max(ttl, 1)
        return RateLimitResult(count <= limit, limit, max(limit - count, 0), retry_after)


def get_client_ip(request: Request, trusted_proxy_ips: list[str]) -> str:
    """Resolve the client IP without trusting arbitrary forwarded headers."""

    client_ip = request.client.host if request.client else "unknown"
    if client_ip in trusted_proxy_ips:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            return forwarded_for.split(",", maxsplit=1)[0].strip() or client_ip
    return client_ip


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply global and login-specific limits before reaching route handlers."""

    def __init__(
        self,
        app,
        settings: Settings,
        telemetry: Telemetry,
        store: RateLimitStore | None = None,
    ) -> None:
        """Store policy and telemetry dependencies for request dispatch."""

        super().__init__(app)
        self.settings = settings
        self.telemetry = telemetry
        self.store = store or InMemoryRateLimitStore()

    async def dispatch(self, request: Request, call_next) -> Response:
        """Return a contract-shaped 429 or pass the request downstream."""

        if not self.settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)

        # Login gets a stricter limit because it is the most attractive brute-
        # force target; other endpoints use the general application limit.
        is_login = request.url.path == "/api/v1/auth/login"
        limit = (
            self.settings.rate_limit_login_requests
            if is_login
            else self.settings.rate_limit_requests
        )
        client_ip = get_client_ip(request, self.settings.trusted_proxy_ips)
        key = f"rate-limit:{client_ip}:{request.url.path}"
        result = await self.store.consume(key, limit, self.settings.rate_limit_window_seconds)
        headers = {
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": str(result.remaining),
        }

        if not result.allowed:
            headers["Retry-After"] = str(result.retry_after)
            request_id = getattr(request.state, "request_id", "unknown")
            self.telemetry.warning(
                "http.rate_limit.exceeded",
                {"requestId": request_id, "path": request.url.path},
            )
            return JSONResponse(
                status_code=429,
                headers=headers,
                content={
                    "code": "rate_limit_exceeded",
                    "message": "Too many requests. Please retry later.",
                    "requestId": request_id,
                },
            )

        response = await call_next(request)
        for name, value in headers.items():
            response.headers[name] = value
        return response
