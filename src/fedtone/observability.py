"""Structured JSON logging with correlation ids and secret scrubbing.

Usage::

    configure_logging(settings)
    with correlation_context(run_id=run.id, document_id=document.id):
        logger.info("parsed document")   # JSON line carries run_id and document_id
"""

import json
import logging
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(settings.secret_values()))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
