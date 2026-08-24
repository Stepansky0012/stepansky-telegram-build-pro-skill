"""Structured JSON logging with a redaction allow-list, plus a metrics shim.

Contract (skills/telegram-backend/references/observability.md):
  one JSON object per line, fixed field names, trace_id on every line,
  allow-list redaction, and a token filter that also covers traceback text.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")
update_id_var: ContextVar[int | None] = ContextVar("update_id", default=None)
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)

# Only these keys survive into a log line. Anything else is dropped, so a new
# field cannot leak PII by accident.
ALLOWED_FIELDS = {
    "event", "handler", "screen", "callback", "response_mode", "presence_state",
    "detail", "outcome", "latency_ms", "attempt", "method", "error", "action",
    "chat_id", "chat_type", "from_screen", "to_screen", "from_state", "to_state",
    "update_type", "api_calls", "charge_id", "amount", "currency", "reason",
    "ok", "count", "sent", "failed", "inactive", "text_sha", "trace_id",
    "update_id", "user_id", "duplicate", "set_name", "glyph",
}
_TOKEN_RE = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,50}")


class RedactFilter(logging.Filter):
    """Catches what the schema cannot: tokens inside exception text and reprs."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:                                    # noqa: BLE001
            return True
        if _TOKEN_RE.search(msg):
            record.msg = _TOKEN_RE.sub("***TOKEN***", msg)
            record.args = ()
        if record.exc_text and _TOKEN_RE.search(record.exc_text):
            record.exc_text = _TOKEN_RE.sub("***TOKEN***", record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, version: str, env: str):
        super().__init__()
        self.base = {"service": service, "version": version, "env": env}

    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "event": getattr(record, "event", record.getMessage()),
            "trace_id": trace_id_var.get(),
            "update_id": update_id_var.get(),
            "user_id": user_id_var.get(),
            **self.base,
        }
        for k, v in record.__dict__.items():
            if k in ALLOWED_FIELDS and v is not None:
                doc[k] = v
        if record.exc_info:
            doc["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            doc["stack"] = self.formatException(record.exc_info)[-4000:]
        return json.dumps(doc, ensure_ascii=False, default=str)


def setup(service: str, version: str, env: str, level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter(service, version, env))
    h.addFilter(RedactFilter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    for noisy in ("aiogram.event", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel("WARNING")


class Metrics:
    """Swap for prometheus_client in production; the interface is what matters."""

    def __init__(self) -> None:
        self.counters: dict[tuple, int] = {}
        self.observations: dict[tuple, list[float]] = {}

    @staticmethod
    def _key(name: str, labels: dict) -> tuple:
        return (name, *sorted(labels.items()))

    def inc(self, name: str, n: int = 1, **labels) -> None:
        k = self._key(name, labels)
        self.counters[k] = self.counters.get(k, 0) + n

    def observe(self, name: str, value: float, **labels) -> None:
        self.observations.setdefault(self._key(name, labels), []).append(value)

    def gauge(self, name: str, value: float, **labels) -> None:
        self.counters[self._key(name, labels)] = int(value)

    def counter(self, name: str, **labels) -> int:
        return self.counters.get(self._key(name, labels), 0)


metrics = Metrics()


def text_sha(s: str) -> str:
    """Log whether two messages were the same without logging either."""
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:12]
