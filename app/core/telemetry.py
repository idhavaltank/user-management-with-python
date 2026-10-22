"""Small telemetry abstraction used to keep logging testable and replaceable."""

from __future__ import annotations

import logging
from typing import Protocol


class Telemetry(Protocol):
    """Interface implemented by the current logger and future observability adapters."""

    def info(self, event: str, meta: dict[str, object] | None = None) -> None:
        """Record an informational event."""

        ...

    def warning(self, event: str, meta: dict[str, object] | None = None) -> None:
        """Record a warning event."""

        ...

    def error(
        self, event: str, meta: dict[str, object] | None = None, error: Exception | None = None
    ) -> None:
        """Record an error event."""

        ...


class LoggingTelemetry:
    """Emit structured events through Python's standard logging package."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Use the supplied logger or the application's default logger."""

        self.logger = logger or logging.getLogger("user_management")

    def info(self, event: str, meta: dict[str, object] | None = None) -> None:
        """Record a non-error lifecycle or request event."""

        self.logger.info(event, extra={"event": event, "meta": meta or {}})

    def warning(self, event: str, meta: dict[str, object] | None = None) -> None:
        """Record a recoverable rejection or degraded dependency event."""

        self.logger.warning(event, extra={"event": event, "meta": meta or {}})

    def error(
        self,
        event: str,
        meta: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Record an error event and include a traceback when one is supplied."""

        self.logger.error(
            event,
            extra={"event": event, "meta": meta or {}},
            exc_info=error is not None,
        )
