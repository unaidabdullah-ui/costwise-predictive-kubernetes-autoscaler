"""Structured logging shared by every component.

``configure_logging`` installs a JSON formatter (containers) or a readable text
formatter (laptop). Extra fields passed with ``logger.info("msg", extra={...})``
are included. :class:`RingBufferHandler` keeps the most recent records in memory
so the dashboard's *Technical details → Logs* view can show them without reading
log files.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

_STANDARD_ATTRS = set(
    vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None)).keys()
) | {"message", "asctime", "taskName"}

# Never let these leak into logs, even if a caller passes them in ``extra``.
_REDACT_KEYS = {"token", "control_token", "password", "authorization", "bearer_token"}


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_ATTRS or key.startswith("_"):
            continue
        out[key] = "***" if key.lower() in _REDACT_KEYS else value
    return out


class JsonFormatter(logging.Formatter):
    """Single-line JSON log records."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Readable formatter that appends ``extra`` fields as ``key=value``."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = _extras(record)
        if extras:
            base += " | " + " ".join(f"{k}={v}" for k, v in extras.items())
        return base


class RingBufferHandler(logging.Handler):
    """Keep the last ``capacity`` log records (as dicts) for the dashboard."""

    def __init__(self, capacity: int = 300, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock_rb = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "fields": {k: str(v)[:300] for k, v in _extras(record).items()},
            }
        except Exception:  # noqa: BLE001 - logging must never raise
            return
        with self._lock_rb:
            self._records.append(entry)

    def recent(self, limit: int = 200, min_level: int = logging.INFO) -> list[dict[str, Any]]:
        with self._lock_rb:
            items = list(self._records)
        items = [r for r in items if logging.getLevelName(r["level"]) >= min_level]
        return items[-limit:]


_ring: RingBufferHandler | None = None


def ring_buffer() -> RingBufferHandler:
    """The process-wide in-memory log buffer (created on first use)."""
    global _ring
    if _ring is None:
        _ring = RingBufferHandler()
    return _ring


def configure_logging(level: str = "INFO", fmt: str = "text", log_file: str | None = None) -> None:
    """Configure the root logger (stdout, optional file, in-memory ring buffer)."""
    formatter: logging.Formatter = JsonFormatter() if fmt.lower() == "json" else TextFormatter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(TextFormatter())
        root.addHandler(file_handler)
    root.addHandler(ring_buffer())
    root.setLevel(level.upper())
    for noisy in ("urllib3", "kubernetes", "matplotlib", "asyncio", "uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
