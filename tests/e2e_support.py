"""Fake adapters for end-to-end runs: FOMC fixture pages, a fake LLM, fixture market data."""

from datetime import date

import httpx

from fedtone.application.llm import LLMRequest, LLMResponse, LLMTransientError
from fedtone.application.sources import SourceRegistry
from fedtone.infrastructure.pipeline import AdapterFactories
from fedtone.infrastructure.sources.fomc import FomcStatementAdapter
from fedtone.infrastructure.sources.http import HttpFetcher
from fedtone.settings import Settings
from tests.fake_llm import FakeLLM
from tests.fake_market import FakeMarketProvider
from tests.fomc_fixtures import fixture_transport
from tests.integration.test_research_summary import faithful
from tests.market_harness import market_data

INSTRUMENTS = [
    {
        "symbol": "SP500",
        "name": "S&P 500 index",
        "kind": "price",
        "calendar": "nyse",
        "observation_time": "16:00",
    },
    {
        "symbol": "DGS10",
        "name": "10-Year Treasury yield",
        "kind": "yield_percent",
        "calendar": "us_treasury",
        "observation_time": "15:30",
    },
]


def e2e_settings(database_url: str, **overrides) -> Settings:
    return Settings(
        database={"url": database_url},
        market_data={"provider": "csv", "instruments": INSTRUMENTS},
        sources={"max_retries": 0},
        **overrides,
    )


class PipelineLLM(FakeLLM):
    """Tone/comparison from FakeLLM; research summaries copy the facts faithfully."""

    def __init__(self, *args, outage: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.outage = outage

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self.outage:
            self.requests.append(request)
            raise LLMTransientError("provider unavailable (503)")
        if request.schema_name == "research_summary":
            self.requests.append(request)
            return LLMResponse(faithful(request), self.model)
        return super().complete(request)


def fixture_factories(
    llm: FakeLLM | None = None,
    market: dict[str, dict[date, float | None]] | None = None,
    transport: httpx.MockTransport | None = None,
) -> AdapterFactories:
    llm = llm or PipelineLLM()
    data = market_data() if market is None else market

    def registry(settings: Settings, client: httpx.Client) -> SourceRegistry:
        fetcher = HttpFetcher(
            httpx.Client(transport=transport or fixture_transport()),
            max_retries=0,
            sleep=lambda _: None,
        )
        return SourceRegistry([FomcStatementAdapter(fetcher)])

    return AdapterFactories(
        registry=registry,
        llm_client=lambda settings: llm,
        market_provider=lambda settings, client: FakeMarketProvider(data),
    )
