"""OpenAI-compatible chat-completions client (works with any provider exposing that API)."""

import logging
import time
from collections.abc import Callable

import httpx

from fedtone.application.llm import (
    LLMPermanentError,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
)
from fedtone.observability import scrub_secrets

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise LLMPermanentError("LLM API key is not configured")
        if not model:
            raise LLMPermanentError("LLM model is not configured")
        self.model = model
        self._api_key = api_key
        self._url = (base_url or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": self.model,
            "temperature": request.temperature,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "schema": request.json_schema},
            },
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.post(self._url, json=body, headers=headers)
            except httpx.TransportError as exc:
                reason = type(exc).__name__
            else:
                if response.status_code == 200:
                    return self._parse(response)
                detail = self._safe_detail(response)
                if response.status_code not in RETRYABLE_STATUS:
                    raise LLMPermanentError(f"LLM HTTP {response.status_code}: {detail}")
                reason = f"HTTP {response.status_code}"
            if attempt == attempts:
                raise LLMTransientError(f"LLM request failed after {attempts} attempts: {reason}")
            delay = self._backoff_seconds * 2 ** (attempt - 1)
            logger.warning("LLM request failed (%s); retry %d in %.1fs", reason, attempt, delay)
            self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _parse(self, response: httpx.Response) -> LLMResponse:
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMTransientError(
                f"unexpected LLM response shape: {type(exc).__name__}"
            ) from None
        if not isinstance(content, str):
            raise LLMTransientError("LLM response has no text content")
        usage = {k: v for k, v in (data.get("usage") or {}).items() if isinstance(v, int)}
        return LLMResponse(content=content, model=str(data.get("model") or self.model), usage=usage)

    def _safe_detail(self, response: httpx.Response) -> str:
        return scrub_secrets(response.text[:300], [self._api_key])
