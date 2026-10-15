from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from fedtone.domain.market import (
    MarketSeries,
    PermanentMarketDataError,
    TransientMarketDataError,
)
from fedtone.infrastructure.market.fred import CsvFileProvider, FredCsvProvider, parse_fred_csv
from fedtone.infrastructure.sources.http import HttpFetcher
from tests.fake_market import FakeMarketProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "market"
NOW = datetime(2024, 6, 1, tzinfo=UTC)
START, END = date(2024, 1, 29), date(2024, 2, 7)


def fred_transport(requests: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        symbol = parse_qs(urlparse(str(request.url)).query)["id"][0]
        path = FIXTURES / f"{symbol}.csv"
        if not path.is_file():
            return httpx.Response(404, text="<html>Series not found</html>")
        return httpx.Response(200, content=path.read_bytes(), headers={"content-type": "text/csv"})

    return httpx.MockTransport(handler)


def fred_provider(
    transport: httpx.BaseTransport | None = None, retries: int = 0
) -> FredCsvProvider:
    client = httpx.Client(transport=transport or fred_transport())
    fetcher = HttpFetcher(client, max_retries=retries, backoff_seconds=0, sleep=lambda _: None)
    return FredCsvProvider(fetcher, clock=lambda: NOW)


def fake_provider() -> FakeMarketProvider:
    series = parse_fred_csv(
        (FIXTURES / "SP500.csv").read_text("utf-8"),
        symbol="SP500",
        provider="fake",
        retrieved_at=NOW,
        start=date(2000, 1, 1),
        end=date(2100, 1, 1),
    )
    return FakeMarketProvider({"SP500": {o.date: o.value for o in series.observations}})


PROVIDERS = {
    "fake": fake_provider,
    "fred": fred_provider,
    "csv": lambda: CsvFileProvider(FIXTURES, clock=lambda: NOW),
}


@pytest.mark.parametrize("make", PROVIDERS.values(), ids=PROVIDERS.keys())
def test_provider_contract(make) -> None:
    provider = make()
    series = provider.fetch("SP500", START, END)

    assert isinstance(series, MarketSeries)
    assert series.symbol == "SP500"
    assert series.provider == provider.name
    dates = [o.date for o in series.observations]
    assert dates == sorted(dates)
    assert dates[0] == START and dates[-1] == END
    assert all(START <= d <= END for d in dates)
    assert all(o.retrieved_at.utcoffset() is not None for o in series.observations)
    # A listed date without a value stays explicit, never dropped or filled.
    assert series.by_date()[date(2024, 2, 5)].value is None
    assert series.by_date()[date(2024, 1, 31)].value == 4845.65


def test_fred_request_names_series_and_range() -> None:
    requests: list[httpx.Request] = []
    fred_provider(fred_transport(requests)).fetch("SP500", START, END)
    query = parse_qs(urlparse(str(requests[0].url)).query)
    assert query == {"id": ["SP500"], "cosd": ["2024-01-29"], "coed": ["2024-02-07"]}
    assert str(requests[0].url).startswith("https://fred.stlouisfed.org/graph/fredgraph.csv")


def test_older_date_header_and_empty_value_are_supported() -> None:
    series = CsvFileProvider(FIXTURES, clock=lambda: NOW).fetch("DGS10", START, END)
    values = series.by_date()
    assert values[date(2024, 1, 31)].value == 3.99
    assert values[date(2024, 2, 7)].value is None


def test_unknown_series_is_permanent() -> None:
    with pytest.raises(PermanentMarketDataError):
        fred_provider().fetch("NOPE", START, END)
    with pytest.raises(PermanentMarketDataError):
        CsvFileProvider(FIXTURES).fetch("NOPE", START, END)


def test_server_errors_retry_then_become_transient() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(TransientMarketDataError):
        fred_provider(httpx.MockTransport(handler), retries=2).fetch("SP500", START, END)
    assert len(calls) == 3


@pytest.mark.parametrize(
    "content",
    [
        "<!DOCTYPE html><html><body>Error</body></html>",
        "observation_date,DGS2\n2024-01-31,4.2\n",
        "observation_date,SP500\n2024-01-31,abc\n",
        "observation_date,SP500\n31/01/2024,4845.65\n",
        "observation_date,SP500\n2024-01-31,1\n2024-01-31,2\n",
    ],
)
def test_unexpected_csv_content_is_permanent(content: str) -> None:
    with pytest.raises(PermanentMarketDataError):
        parse_fred_csv(
            content, symbol="SP500", provider="fred", retrieved_at=NOW, start=START, end=END
        )
