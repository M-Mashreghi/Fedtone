"""Speeches flow through the shared pipeline with speaker identity (Task 7.3)."""

from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from fedtone.api.app import create_app
from fedtone.application.ingestion import IngestionService, Outcome
from fedtone.application.sources import SourceRegistry
from fedtone.application.tone_analysis import AnalysisService, ToneAnalyzer
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyComparisonRepository,
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyProcessingRunRepository,
    SqlAlchemyReviewItemRepository,
    SqlAlchemySourceSnapshotRepository,
    SqlAlchemyToneAnalysisRepository,
)
from fedtone.infrastructure.sources.fed_speeches import FedSpeechAdapter
from fedtone.infrastructure.sources.http import HttpFetcher
from fedtone.settings import Settings
from tests.bank_fixtures import FIXTURES
from tests.fake_llm import FakeLLM
from tests.market_harness import MarketHarness

FED = "https://www.federalreserve.gov"
FEED = f"{FED}/feeds/speeches.xml"
SPEECHES = ("barr20240305a", "barr20240610a", "cook20240401a")
SCORES = {"restrictive for some time": (0.5, 0.8), "begin easing": (-0.3, 0.8)}


def transport() -> httpx.MockTransport:
    base = FIXTURES / "fed" / "synthetic"
    routes = {FEED: (base / "speeches_feed.xml").read_bytes()}
    for name in SPEECHES:
        routes[f"{FED}/newsevents/speech/{name}.htm"] = (base / f"speech_{name}.htm").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        content = routes.get(str(request.url))
        return httpx.Response(200, content=content) if content else httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def speeches(session_factory):
    """Market-linked FOMC statements plus three analyzed speeches by two speakers."""
    harness = MarketHarness(session_factory)
    ticks = count()
    clock = lambda: datetime(2024, 7, 1, tzinfo=UTC) + timedelta(seconds=next(ticks))  # noqa: E731
    documents = SqlAlchemyPolicyDocumentRepository(session_factory)
    snapshots = SqlAlchemySourceSnapshotRepository(session_factory)
    reviews = SqlAlchemyReviewItemRepository(session_factory)
    fetcher = HttpFetcher(httpx.Client(transport=transport()), max_retries=0)
    report = IngestionService(
        registry=SourceRegistry([FedSpeechAdapter(fetcher, clock=clock)]),
        documents=documents,
        snapshots=snapshots,
        reviews=reviews,
        runs=SqlAlchemyProcessingRunRepository(session_factory),
        clock=clock,
    ).run("FED", "speech")
    assert report.count(Outcome.INGESTED) == 3
    AnalysisService(
        analyzer=ToneAnalyzer(FakeLLM(scores=SCORES)),
        documents=documents,
        snapshots=snapshots,
        analyses=SqlAlchemyToneAnalysisRepository(session_factory),
        comparisons=SqlAlchemyComparisonRepository(session_factory),
        reviews=reviews,
        clock=clock,
    ).run("FED", "speech")
    return harness, documents


@pytest.fixture
def client(speeches, database_url, migrated_engine) -> TestClient:
    return TestClient(create_app(Settings(database={"url": database_url}), engine=migrated_engine))


def test_speaker_is_persisted_and_speeches_link_per_speaker(speeches) -> None:
    _, documents = speeches
    stored = {d.source_url.rsplit("/", 1)[1]: d for d in documents.list(document_type="speech")}

    barr1, barr2, cook = (stored[f"{n}.htm"] for n in SPEECHES)
    assert (barr1.speaker, barr2.speaker, cook.speaker) == (
        "Synthetic A. Barr",
        "Synthetic A. Barr",  # same person after a change of office
        "Synthetic L. Cook",
    )
    assert barr2.previous_document_id == barr1.id  # not Cook's speech in between
    assert barr1.previous_document_id is None and cook.previous_document_id is None


def test_speeches_stay_out_of_the_statement_timeline_by_default(client) -> None:
    default = client.get("/api/v1/timeline").json()["points"]
    speeches = client.get("/api/v1/timeline", params={"document_type": "speech"}).json()["points"]
    everything = client.get("/api/v1/timeline", params={"include_speeches": True}).json()

    assert {p["document_type"] for p in default} == {"fomc_statement"}
    assert len(speeches) == 3 and {p["document_type"] for p in speeches} == {"speech"}
    assert all(p["tone_profile"] == "fed-speech-v1" for p in speeches)
    assert len(everything["points"]) == len(default) + 3
    assert client.get("/api/v1/documents").json()["total"] == len(default)


def test_speaker_filter_and_speaker_summary(client) -> None:
    barr = client.get("/api/v1/timeline", params={"speaker": "Synthetic A. Barr"}).json()["points"]
    summary = {s["speaker"]: s for s in client.get("/api/v1/speakers").json()}

    assert [p["net_tone_score"] for p in barr] == [0.5, -0.3]
    assert barr[1]["overall_delta"] == pytest.approx(-0.8)  # versus Barr's previous speech
    assert set(summary) == {"Synthetic A. Barr", "Synthetic L. Cook"}
    assert summary["Synthetic A. Barr"]["speeches"] == 2
    assert summary["Synthetic A. Barr"]["mean_tone"] == pytest.approx(0.1)
    assert summary["Synthetic A. Barr"]["latest_tone"] == -0.3
    assert UUID(summary["Synthetic A. Barr"]["latest_document_id"])
    assert summary["Synthetic L. Cook"]["latest_tone"] == 0.3  # FakeLLM default score
