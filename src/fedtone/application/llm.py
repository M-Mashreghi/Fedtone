"""Provider-independent LLM port."""

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(Exception):
    """Base class for LLM provider failures. Messages never contain credentials."""


class LLMTransientError(LLMError):
    """Retries exhausted on a failure that may succeed later (timeout, 429, 5xx)."""


class LLMPermanentError(LLMError):
    """Configuration or request problem that retrying will not fix (401, 403, 400)."""


@dataclass(frozen=True)
class LLMRequest:
    system: str
    user: str
    schema_name: str
    json_schema: dict[str, Any]
    temperature: float


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient(Protocol):
    model: str

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the raw model text; schema validation is the caller's job."""
        ...
