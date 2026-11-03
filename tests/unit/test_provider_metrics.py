"""Provider request/error/latency counters (Task 6.3)."""

import httpx
import pytest

from fedtone.application.sources import PermanentSourceError
from fedtone.infrastructure.sources.http import HttpFetcher
from fedtone.observability import PROVIDER_METRICS, ProviderMetrics
from tests.unit.test_llm_client import KEY, REQUEST, client_for, ok_body


@pytest.fixture(autouse=True)
def fresh_metrics():
    PROVIDER_METRICS.reset()
    yield
    PROVIDER_METRICS.reset()


def test_counters_aggregate_requests_errors_and_latency() -> None:
    metrics = ProviderMetrics()

    metrics.record("llm", seconds=0.2)
    metrics.record("llm", seconds=0.4, error="HTTP 503")

    stats = metrics.snapshot()["llm"]
    assert stats["requests"] == 2
    assert stats["errors"] == 1
    assert stats["avg_latency_ms"] == pytest.approx(300.0)
    assert stats["last_error"] == "HTTP 503"
    assert stats["last_error_at"]


def test_stored_error_text_is_scrubbed() -> None:
    metrics = ProviderMetrics()

    metrics.record("llm", seconds=0.1, error="denied: api_key=sk-live-abc Bearer xyz.123")

    error = metrics.snapshot()["llm"]["last_error"]
    assert "sk-live-abc" not in error and "xyz.123" not in error


def test_http_fetcher_records_every_attempt_per_host() -> None:
    responses = iter([httpx.Response(503), httpx.Response(200, content=b"ok")])
    client = httpx.Client(transport=httpx.MockTransport(lambda request: next(responses)))
    fetcher = HttpFetcher(client, max_retries=2, sleep=lambda _: None)

    fetcher.get("https://www.federalreserve.gov/page.htm")

    stats = PROVIDER_METRICS.snapshot()["http:www.federalreserve.gov"]
    assert (stats["requests"], stats["errors"], stats["last_error"]) == (2, 1, "HTTP 503")


def test_http_fetcher_records_permanent_errors() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    fetcher = HttpFetcher(client, max_retries=3, sleep=lambda _: None)

    with pytest.raises(PermanentSourceError):
        fetcher.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10")

    stats = PROVIDER_METRICS.snapshot()["http:fred.stlouisfed.org"]
    assert (stats["requests"], stats["errors"]) == (1, 1)


def test_llm_client_records_attempts_without_the_key() -> None:
    client, _, _ = client_for(
        [
            httpx.Response(429, text=f"rate limited for key {KEY}"),
            httpx.Response(200, json=ok_body()),
        ]
    )

    client.complete(REQUEST)

    stats = PROVIDER_METRICS.snapshot()["llm"]
    assert (stats["requests"], stats["errors"]) == (2, 1)
    assert KEY not in str(PROVIDER_METRICS.snapshot())
