"""ECB, BoE and SNB documents run through the same pipeline as FOMC statements (Task 7.1)."""

from datetime import UTC, datetime, timedelta
from itertools import count

import httpx
import pytest

from fedtone.application.ingestion import IngestionService, Outcome
from fedtone.application.pipeline import PipelineService
from fedtone.application.scheduler import PipelineScheduler
from fedtone.application.tone_analysis import AnalysisService, ToneAnalyzer
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.infrastructure.factories import build_registry
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyComparisonRepository,
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyProcessingRunRepository,
    SqlAlchemyReviewItemRepository,
    SqlAlchemySourceSnapshotRepository,
    SqlAlchemyToneAnalysisRepository,
)
from fedtone.infrastructure.pipeline import AdapterFactories, default_stage_runners
from fedtone.settings import Settings
from tests.bank_fixtures import ECB_LISTING, bank_routes
from tests.fake_llm import FakeLLM
from tests.fomc_fixtures import SYNTHETIC, SYNTHETIC_ROUTES

SOURCES = [
    ("ECB", "monetary_policy_decision"),
    ("BOE", "monetary_policy_summary"),
    ("SNB", "monetary_policy_assessment"),
]


def transport() -> httpx.MockTransport:
    routes = bank_routes()
    for url, name in SYNTHETIC_ROUTES.items():
        routes[url] = ((SYNTHETIC / name).read_bytes(), "text/html; charset=utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        found = routes.get(str(request.url))
        if found is None:
            return httpx.Response(404)
        return httpx.Response(200, content=found[0], headers={"content-type": found[1]})

    return httpx.MockTransport(handler)


def settings(database_url: str) -> Settings:
    return Settings(
        database={"url": database_url},
        sources={
            "ecb_listing_urls": [ECB_LISTING],
            "max_retries": 0,
            "enabled": ["FED/fomc_statement", *(f"{b}/{t}" for b, t in SOURCES)],
        },
    )


def registry(settings: Settings, client: httpx.Client):
    return build_registry(settings, httpx.Client(transport=transport()))


class Repos:
    def __init__(self, session_factory) -> None:
        self.documents = SqlAlchemyPolicyDocumentRepository(session_factory)
        self.snapshots = SqlAlchemySourceSnapshotRepository(session_factory)
        self.reviews = SqlAlchemyReviewItemRepository(session_factory)
        self.runs = SqlAlchemyProcessingRunRepository(session_factory)
        self.analyses = SqlAlchemyToneAnalysisRepository(session_factory)
        self.comparisons = SqlAlchemyComparisonRepository(session_factory)
        ticks = count()
        self.clock = lambda: datetime(2024, 10, 1, tzinfo=UTC) + timedelta(seconds=next(ticks))

    def ingestion(self, database_url) -> IngestionService:
        return IngestionService(
            registry=registry(settings(database_url), None),
            documents=self.documents,
            snapshots=self.snapshots,
            reviews=self.reviews,
            runs=self.runs,
            clock=self.clock,
        )


def test_registry_holds_all_four_banks(database_url) -> None:
    keys = [
        (a.central_bank, a.document_type) for a in registry(settings(database_url), None).adapters()
    ]

    assert keys == sorted([("FED", "fomc_statement"), *SOURCES])


@pytest.mark.parametrize(("bank", "doc_type"), SOURCES)
def test_bank_documents_use_the_shared_ingestion_and_analysis(
    session_factory, database_url, bank: str, doc_type: str
) -> None:
    repos = Repos(session_factory)

    first = repos.ingestion(database_url).run(bank, doc_type)
    second = repos.ingestion(database_url).run(bank, doc_type)

    assert first.count(Outcome.INGESTED) == 2
    assert second.count(Outcome.UNCHANGED) == 2  # idempotent
    older, newer = repos.documents.list(central_bank=bank, document_type=doc_type)
    assert older.published_at < newer.published_at
    assert newer.previous_document_id == older.id and older.previous_document_id is None
    assert older.content_hash and older.parser_version
    assert repos.snapshots.texts_for(newer.id)

    report = AnalysisService(
        analyzer=ToneAnalyzer(FakeLLM()),
        documents=repos.documents,
        snapshots=repos.snapshots,
        analyses=repos.analyses,
        comparisons=repos.comparisons,
        reviews=repos.reviews,
        clock=repos.clock,
    ).run(bank, doc_type)

    assert not report.aborted
    statuses = {d.status for d in repos.documents.list(central_bank=bank)}
    assert statuses == {DocumentStatus.COMPARED}
    assert repos.comparisons.latest_for_document(newer.id).previous_document_id == older.id


def test_banks_never_link_across_each_other(session_factory, database_url) -> None:
    repos = Repos(session_factory)
    for bank, doc_type in [("FED", "fomc_statement"), *SOURCES]:
        repos.ingestion(database_url).run(bank, doc_type)

    documents = {d.id: d for d in repos.documents.list(limit=None)}

    assert len(documents) == 9
    for document in documents.values():
        if document.previous_document_id is not None:
            previous = documents[document.previous_document_id]
            assert (previous.central_bank, previous.document_type) == (
                document.central_bank,
                document.document_type,
            )


def test_scheduler_cycles_over_enabled_sources(session_factory, database_url) -> None:
    config = settings(database_url)
    runners = default_stage_runners(
        config,
        session_factory,
        AdapterFactories(registry=registry, llm_client=lambda s: FakeLLM()),
    )
    scheduler = PipelineScheduler(PipelineService(runners), sources=config.sources.enabled_pairs())

    report = scheduler.run_cycle()

    assert not report.errors
    assert "ECB/monetary_policy_decision:reactions" in report.skipped  # no market provider
    banks = {
        d.central_bank for d in SqlAlchemyPolicyDocumentRepository(session_factory).list(limit=None)
    }
    assert banks == {"FED", "ECB", "BOE", "SNB"}


def test_enabled_sources_must_name_bank_and_type() -> None:
    with pytest.raises(ValueError, match="BANK/document_type"):
        Settings(sources={"enabled": ["ECB"]})
