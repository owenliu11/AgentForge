# app/logs.py
"""Structured JSON logs. Every worker line carries run_id, worker_id, and generation."""

import json
import logging
import sys
from datetime import UTC, datetime

CONTEXT_FIELDS = ("run_id", "worker_id", "generation", "seq", "tool", "crash_at", "event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        for name in CONTEXT_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                out[name] = value

        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)

        return json.dumps(out, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # httpx logs every request at INFO; keep worker logs readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)
