"""Adapter construction from settings, shared by the CLI and the API."""

from datetime import time
from pathlib import Path

import httpx

from fedtone.application.market_data import MarketDataProvider
from fedtone.application.sources import SourceRegistry
from fedtone.domain.market import Instrument, ValueKind
from fedtone.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from fedtone.infrastructure.market.fred import FRED_CSV_URL, CsvFileProvider, FredCsvProvider
from fedtone.infrastructure.sources.boe import BoeSummaryAdapter
from fedtone.infrastructure.sources.ecb import EcbDecisionAdapter
from fedtone.infrastructure.sources.fomc import FomcStatementAdapter
from fedtone.infrastructure.sources.http import HttpFetcher
from fedtone.infrastructure.sources.snb import SnbAssessmentAdapter
from fedtone.settings import Settings


def build_registry(settings: Settings, client: httpx.Client) -> SourceRegistry:
    fetcher = HttpFetcher(
        client,
        max_retries=settings.sources.max_retries,
        backoff_seconds=settings.sources.backoff_seconds,
    )
    sources = settings.sources
    return SourceRegistry(
        [
            FomcStatementAdapter(fetcher, listing_urls=sources.fomc_listing_urls),
            EcbDecisionAdapter(fetcher, listing_urls=sources.ecb_listing_urls or None),
            BoeSummaryAdapter(fetcher, listing_urls=sources.boe_listing_urls),
            SnbAssessmentAdapter(fetcher, listing_urls=sources.snb_listing_urls),
        ]
    )


def build_llm_client(settings: Settings) -> OpenAICompatibleClient:
    llm = settings.llm
    return OpenAICompatibleClient(
        api_key=llm.api_key.get_secret_value() if llm.api_key else "",
        model=llm.model or "",
        base_url=llm.base_url,
        timeout_seconds=llm.timeout_seconds,
        max_retries=llm.max_retries,
        backoff_seconds=llm.backoff_seconds,
    )


def build_instruments(settings: Settings) -> list[Instrument]:
    return [
        Instrument(
            symbol=i.symbol,
            name=i.name,
            kind=ValueKind(i.kind),
            calendar=i.calendar,
            observation_time=time.fromisoformat(i.observation_time),
        )
        for i in settings.market_data.instruments
    ]


def build_market_provider(settings: Settings, client: httpx.Client) -> MarketDataProvider | None:
    market = settings.market_data
    if market.provider == "fred":
        fetcher = HttpFetcher(
            client, max_retries=market.max_retries, backoff_seconds=market.backoff_seconds
        )
        return FredCsvProvider(fetcher, base_url=market.base_url or FRED_CSV_URL)
    if market.provider == "csv":
        return CsvFileProvider(Path(market.csv_directory))
    return None
