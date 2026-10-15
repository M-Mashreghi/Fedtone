"""HTTP fetching with bounded retry and exponential backoff."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from fedtone.application.sources import PermanentSourceError, TransientSourceError

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class HttpResponse:
    url: str
    content: bytes
    content_type: str


class HttpFetcher:
    def __init__(
        self,
        client: httpx.Client,
        *,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    def get(self, url: str) -> HttpResponse:
        """GET ``url``; retry transient failures up to ``max_retries`` times.

        Raises TransientSourceError when retries are exhausted and PermanentSourceError for
        non-retryable HTTP errors (e.g. 404).
        """
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:
                reason = f"{type(exc).__name__}"
            else:
                if response.status_code < 400:
                    return HttpResponse(
                        url=str(response.url),
                        content=response.content,
                        content_type=response.headers.get("content-type", ""),
                    )
                if response.status_code not in RETRYABLE_STATUS:
                    raise PermanentSourceError(f"GET {url} returned HTTP {response.status_code}")
                reason = f"HTTP {response.status_code}"

            if attempt == attempts:
                raise TransientSourceError(f"GET {url} failed after {attempts} attempts: {reason}")
            delay = self._backoff_seconds * 2 ** (attempt - 1)
            logger.warning("GET %s failed (%s); retry %d in %.1fs", url, reason, attempt, delay)
            self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover
