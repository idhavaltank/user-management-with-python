"""Structured JSON logging configuration for API and dependency events."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Serialize log records as compact JSON for local or hosted collectors."""

    def format(self, record: logging.LogRecord) -> str:
        """Keep event metadata machine-readable while retaining plain messages."""

        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Optional fields are attached through logging's ``extra`` API by
        # telemetry callers, so ordinary third-party logs remain compatible.
        event = getattr(record, "event", None)
        meta = getattr(record, "meta", None)
        if event:
            payload["event"] = event
        if meta:
            payload["meta"] = meta
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Install one predictable stdout handler for the application process."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
