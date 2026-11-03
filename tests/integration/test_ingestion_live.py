"""Captured official FOMC pages ingest through the real pipeline (skipped when none exist)."""

from datetime import UTC, datetime

import httpx
import pytest

from fedtone.application.ingestion import IngestionService, Outcome
from fedtone.application.sources import SourceRegistry
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyProcessingRunRepository,
    SqlAlchemyReviewItemRepository,
    SqlAlchemySourceSnapshotRepository,
)
from fedtone.infrastructure.sources.fomc import FomcStatementAdapter
from fedtone.infrastructure.sources.http import HttpFetcher
from tests.fomc_fixtures import BASE, LIVE

STATEMENTS = sorted(LIVE.glob("statement_*.html"))


def live_transport() -> httpx.MockTransport:
    routes = {
        f"{BASE}/newsevents/pressreleases/monetary{p.stem.removeprefix('statement_')}a.htm": p
        for p in STATEMENTS
    }
    listing = "".join(f'<a href="{url}">HTML</a>' for url in routes)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{BASE}/listing.htm":
            return httpx.Response(200, text=listing)
        if url in routes:
            return httpx.Response(200, content=routes[url].read_bytes())
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.skipif(not STATEMENTS, reason="no captured statements in tests/fixtures/fomc/live")
def test_captured_statements_ingest_and_link_in_order(session_factory) -> None:
    fetcher = HttpFetcher(httpx.Client(transport=live_transport()), max_retries=0)
    adapter = FomcStatementAdapter(fetcher, listing_urls=[f"{BASE}/listing.htm"])
    documents = SqlAlchemyPolicyDocumentRepository(session_factory)
    service = IngestionService(
        registry=SourceRegistry([adapter]),
        documents=documents,
        snapshots=SqlAlchemySourceSnapshotRepository(session_factory),
        reviews=SqlAlchemyReviewItemRepository(session_factory),
        runs=SqlAlchemyProcessingRunRepository(session_factory),
        clock=lambda: datetime(2026, 11, 3, tzinfo=UTC),
    )

    report = service.run("FED", "fomc_statement")

    assert report.count(Outcome.INGESTED) == len(STATEMENTS)
    stored = documents.list(limit=None)
    assert {d.status for d in stored} == {DocumentStatus.QUEUED_FOR_ANALYSIS}
    for previous, current in zip(stored, stored[1:], strict=False):
        assert previous.published_at < current.published_at
        assert current.previous_document_id == previous.id
    assert service.run("FED", "fomc_statement").count(Outcome.UNCHANGED) == len(STATEMENTS)
