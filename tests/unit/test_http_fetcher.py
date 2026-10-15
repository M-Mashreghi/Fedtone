import httpx
import pytest

from fedtone.application.sources import PermanentSourceError, TransientSourceError
from fedtone.infrastructure.sources.http import HttpFetcher

URL = "https://www.federalreserve.gov/x.htm"


def fetcher_for(responses, max_retries=3):
    calls, sleeps = [], []
    items = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        item = next(items)
        if isinstance(item, Exception):
            raise item
        return item

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = HttpFetcher(client, max_retries=max_retries, backoff_seconds=0.5, sleep=sleeps.append)
    return fetcher, calls, sleeps


def test_success_returns_content_and_type() -> None:
    fetcher, calls, sleeps = fetcher_for(
        [httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})]
    )

    response = fetcher.get(URL)

    assert (response.content, response.content_type, response.url) == (b"<html/>", "text/html", URL)
    assert len(calls) == 1
    assert sleeps == []


def test_transient_failures_retry_with_exponential_backoff_then_succeed() -> None:
    fetcher, calls, sleeps = fetcher_for(
        [
            httpx.ConnectError("boom"),
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, content=b"ok"),
        ]
    )

    assert fetcher.get(URL).content == b"ok"
    assert len(calls) == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_retries_are_bounded() -> None:
    fetcher, calls, sleeps = fetcher_for([httpx.Response(500)] * 3, max_retries=2)

    with pytest.raises(TransientSourceError, match="after 3 attempts: HTTP 500"):
        fetcher.get(URL)
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize("status", [400, 403, 404, 410])
def test_client_errors_are_permanent_and_not_retried(status: int) -> None:
    fetcher, calls, sleeps = fetcher_for([httpx.Response(status)])

    with pytest.raises(PermanentSourceError, match=str(status)):
        fetcher.get(URL)
    assert len(calls) == 1
    assert sleeps == []
