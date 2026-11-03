import pytest
from fastapi.testclient import TestClient

from fedtone.api.app import create_app
from fedtone.application.research_summary import ResearchSummaryService
from fedtone.settings import Settings
from tests.fake_llm import FakeLLM
from tests.integration.conftest import database_url, migrated_engine, session_factory  # noqa: F401
from tests.integration.test_research_summary import NAMES, faithful
from tests.market_harness import MarketHarness


@pytest.fixture
def harness(session_factory) -> MarketHarness:  # noqa: F811
    """Three analyzed, compared and market-linked statements; all summarized."""
    h = MarketHarness(session_factory)
    h.reaction_service().run("FED", "fomc_statement")
    ResearchSummaryService(
        client=FakeLLM(responder=faithful),
        documents=h.documents,
        analyses=h.analyses,
        comparisons=h.comparisons,
        metrics=h.metrics,
        summaries=h.summaries,
        reviews=h.reviews,
        instrument_names=NAMES,
        clock=h.clock,
    ).run("FED", "fomc_statement")
    return h


@pytest.fixture
def api_settings(database_url) -> Settings:  # noqa: F811
    return Settings(database={"url": database_url})


@pytest.fixture
def client(harness, api_settings, migrated_engine) -> TestClient:  # noqa: F811
    return TestClient(create_app(api_settings, engine=migrated_engine))
