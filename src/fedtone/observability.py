"""Structured JSON logging with correlation ids and secret scrubbing.

Usage::

    configure_logging(settings)
    with correlation_context(run_id=run.id, document_id=document.id):
        logger.info("parsed document")   # JSON line carries run_id and document_id

    PROVIDER_METRICS.record("llm", seconds=0.8, error=None)   # one provider request
    PROVIDER_METRICS.snapshot()                              # counters for telemetry
"""

import json
import logging
import re
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fedtone.settings import REDACTED, Settings, redact

_run_id: ContextVar[str | None] = ContextVar("fedtone_run_id", default=None)
_document_id: ContextVar[str | None] = ContextVar("fedtone_document_id", default=None)

# Catches credentials that are not in settings, e.g. echoed back by a provider error.
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b(\s*[=:]\s*)(?:bearer\s+)?"
    r"([\"']?)[^\s\"',;&]+\3"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def scrub_secrets(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove known secret values and credential-looking ``key=value`` pairs from text."""
    text = redact(text, secrets)
    text = _CREDENTIAL_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return _BEARER_PATTERN.sub(f"Bearer {REDACTED}", text)


@contextmanager
def correlation_context(
    *, run_id: UUID | str | None = None, document_id: UUID | str | None = None
) -> Iterator[None]:
    """Attach run/document ids to every log record emitted inside the block."""
    tokens = []
    if run_id is not None:
        tokens.append((_run_id, _run_id.set(str(run_id))))
    if document_id is not None:
        tokens.append((_document_id, _document_id.set(str(document_id))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": _run_id.get(),
            "document_id": _document_id.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        line = json.dumps(payload, default=str, ensure_ascii=False)
        return scrub_secrets(line, self._secrets)


@dataclass
class _ProviderStat:
    requests: int = 0
    errors: int = 0
    total_seconds: float = 0.0
    last_error: str | None = None
    last_error_at: str | None = None


class ProviderMetrics:
    """In-process request/error/latency counters per external provider.

    One ``record`` per request attempt, so retries show up as extra requests. Error text is
    scrubbed of credential-looking values before it is stored.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: dict[str, _ProviderStat] = {}

    def record(self, provider: str, *, seconds: float, error: str | None = None) -> None:
        with self._lock:
            stat = self._stats.setdefault(provider, _ProviderStat())
            stat.requests += 1
            stat.total_seconds += max(seconds, 0.0)
            if error is not None:
                stat.errors += 1
                stat.last_error = scrub_secrets(error)[:300]
                stat.last_error_at = datetime.now(UTC).isoformat()

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                name: {
                    "requests": s.requests,
                    "errors": s.errors,
                    "avg_latency_ms": round(1000 * s.total_seconds / s.requests, 1)
                    if s.requests
                    else None,
                    "last_error": s.last_error,
                    "last_error_at": s.last_error_at,
                }
                for name, s in sorted(self._stats.items())
            }

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()


PROVIDER_METRICS = ProviderMetrics()


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(settings.secret_values()))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
