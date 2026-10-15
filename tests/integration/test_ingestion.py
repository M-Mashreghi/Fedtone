from datetime import UTC, datetime, timedelta
from itertools import count

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from fedtone.application.ingestion import IngestionService, Outcome
from fedtone.application.sources import SourceRegistry
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.models import RunStatus, transition
from fedtone.domain.text import sha256_hex
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyProcessingRunRepository,
    SqlAlchemyReviewItemRepository,
    SqlAlchemySourceSnapshotRepository,
)
from fedtone.infrastructure.sources.fomc import FomcStatementAdapter
from fedtone.infrastructure.sources.http import HttpFetcher
from fedtone.infrastructure.sources.manual import ManualDocument, ManualSourceAdapter
from tests.fomc_fixtures import BASE, fixture_transport, read

CALENDAR = f"{BASE}/monetarypolicy/fomccalendars.htm"
DEC = f"{BASE}/newsevents/pressreleases/monetary20231213a.htm"
JAN = f"{BASE}/newsevents/pressreleases/monetary20240131a.htm"
MAR = f"{BASE}/newsevents/pressreleases/monetary20240320a.htm"
START = datetime(2024, 4, 1, 12, tzinfo=UTC)


def calendar_with(*urls: str) -> httpx.Response:
    links = "".join(f'<a href="{url}">HTML</a>' for url in urls)
    return httpx.Response(200, content=f"<html><body>{links}</body></html>".encode())


def html_response(text: str) -> httpx.Response:
    return httpx.Response(200, content=text.encode(), headers={"content-type": "text/html"})


class Harness:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.documents = SqlAlchemyPolicyDocumentRepository(session_factory)
        self.snapshots = SqlAlchemySourceSnapshotRepository(session_factory)
        self.reviews = SqlAlchemyReviewItemRepository(session_factory)
        self.runs = SqlAlchemyProcessingRunRepository(session_factory)
        ticks = count()
        self.clock = lambda: START + timedelta(seconds=next(ticks))
        self.overrides: dict[str, httpx.Response] = {}

    def service(self) -> IngestionService:
        client = httpx.Client(transport=fixture_transport(overrides=self.overrides))
        fetcher = HttpFetcher(client, max_retries=1, sleep=lambda _: None)
        adapter = FomcStatementAdapter(fetcher, clock=self.clock)
        return self.service_for(adapter)

    def service_for(self, adapter) -> IngestionService:
        return IngestionService(
            registry=SourceRegistry([adapter]),
            documents=self.documents,
            snapshots=self.snapshots,
            reviews=self.reviews,
            runs=self.runs,
            clock=self.clock,
        )

    def run(self, **kwargs):
        return self.service().run("FED", "fomc_statement", **kwargs)

    def doc(self, url: str):
        return self.documents.get_by_canonical_identity("FED", "fomc_statement", url)


@pytest.fixture
def h(session_factory) -> Harness:
    return Harness(session_factory)


def test_fixture_set_ingests_and_links_in_publication_order(h: Harness) -> None:
    report = h.run()

    assert report.discovered == 3
    assert report.count(Outcome.INGESTED) == 3
    assert report.run.status is RunStatus.SUCCEEDED
    assert h.runs.get(report.run.id).status is RunStatus.SUCCEEDED

    dec, jan, mar = h.doc(DEC), h.doc(JAN), h.doc(MAR)
    for doc in (dec, jan, mar):
        assert doc.status is DocumentStatus.QUEUED_FOR_ANALYSIS
        assert doc.parser_version == "fomc-statement-html-1"
        assert doc.fetched_at is not None
    assert dec.previous_document_id is None
    assert jan.previous_document_id == dec.id
    assert mar.previous_document_id == jan.id
    assert jan.published_at == datetime(2024, 1, 31, 19, tzinfo=UTC)


def test_raw_snapshot_and_normalized_text_are_stored_with_hashes(h: Harness) -> None:
    h.run()
    jan = h.doc(JAN)

    snapshot = h.snapshots.latest_snapshot(jan.id)
    (text,) = h.snapshots.texts_for(jan.id)

    assert snapshot.version == 1
    assert snapshot.content == read("statement_20240131.html").encode()
    assert snapshot.raw_hash == sha256_hex(snapshot.content)
    assert text.snapshot_id == snapshot.id
    assert jan.content_hash == text.text_hash == sha256_hex(text.normalized_text)
    # Wording and order preserved; NBSP and line breaks normalized.
    assert "inflation at the rate of 2 percent over the longer run" in text.paragraphs[1]
    assert text.paragraphs[3] == (
        "Voting for the monetary policy action were Synthetic Member One, Chair; "
        "Synthetic Member Two, Vice Chair; and Synthetic Member Three."
    )
    assert len(text.paragraphs) == 4


def test_second_run_creates_no_duplicates(h: Harness) -> None:
    h.run()
    before = {d.id: (d.status, len(d.history), d.content_hash) for d in h.documents.list()}

    report = h.run()

    after = {d.id: (d.status, len(d.history), d.content_hash) for d in h.documents.list()}
    assert report.count(Outcome.UNCHANGED) == 3
    assert report.count(Outcome.INGESTED) == 0
    assert after == before
    for doc_id in after:
        assert len(h.snapshots.texts_for(doc_id)) == 1
        assert h.snapshots.latest_snapshot(doc_id).version == 1
    assert h.reviews.list_open() == []


def test_second_run_with_refetch_and_identical_content_is_unchanged(h: Harness) -> None:
    h.run()

    report = h.run(refetch_existing=True)

    assert report.count(Outcome.UNCHANGED) == 3
    assert h.snapshots.latest_snapshot(h.doc(JAN).id).version == 1


def test_transient_failure_marks_failed_then_next_run_resumes_and_relinks(h: Harness) -> None:
    h.overrides[JAN] = httpx.Response(503)
    first = h.run()

    jan, mar, dec = h.doc(JAN), h.doc(MAR), h.doc(DEC)
    assert first.run.status is RunStatus.PARTIAL
    assert first.count(Outcome.FAILED) == 1
    assert jan.status is DocumentStatus.FAILED
    assert jan.last_successful_status is DocumentStatus.DISCOVERED
    assert "fetch failed" in jan.failure_reason
    assert mar.previous_document_id == dec.id  # best available while January is missing

    del h.overrides[JAN]
    second = h.run()

    jan, mar = h.doc(JAN), h.doc(MAR)
    assert second.run.status is RunStatus.SUCCEEDED
    assert second.count(Outcome.INGESTED) == 1
    assert second.relinked == 2
    assert jan.status is DocumentStatus.QUEUED_FOR_ANALYSIS
    assert jan.previous_document_id == dec.id
    assert mar.previous_document_id == jan.id


def test_permanent_parse_failure_routes_to_review_once(h: Harness) -> None:
    h.overrides[JAN] = html_response(read("broken_layout.html"))
    first = h.run()

    jan = h.doc(JAN)
    assert first.count(Outcome.NEEDS_REVIEW) == 1
    assert jan.status is DocumentStatus.NEEDS_REVIEW
    assert jan.last_successful_status is DocumentStatus.FETCHED
    (item,) = h.reviews.list_open()
    assert item.document_id == jan.id
    assert item.stage is DocumentStatus.FETCHED
    assert "#article container not found" in item.reason
    # Raw content is still kept for audit.
    assert h.snapshots.latest_snapshot(jan.id) is not None

    second = h.run()

    assert second.count(Outcome.NEEDS_REVIEW) == 1
    assert len(h.reviews.list_open()) == 1


def test_not_found_is_a_permanent_fetch_failure(h: Harness) -> None:
    h.overrides[JAN] = httpx.Response(404)

    h.run()

    jan = h.doc(JAN)
    assert jan.status is DocumentStatus.NEEDS_REVIEW
    assert jan.last_successful_status is DocumentStatus.DISCOVERED
    assert h.snapshots.latest_snapshot(jan.id) is None


def test_revised_text_is_versioned_and_flagged_not_overwritten(h: Harness) -> None:
    h.run()
    original_hash = h.doc(JAN).content_hash
    revised = read("statement_20240131.html").replace("solid pace", "strong pace")
    h.overrides[JAN] = html_response(revised)

    report = h.run(refetch_existing=True)

    jan = h.doc(JAN)
    assert report.count(Outcome.REVISION_DETECTED) == 1
    assert jan.content_hash == original_hash
    assert h.snapshots.latest_snapshot(jan.id).version == 2
    texts = h.snapshots.texts_for(jan.id)
    assert [t.text_hash == original_hash for t in texts] == [True, False]
    (item,) = h.reviews.list_open()
    assert "source text revised (snapshot v2)" in item.reason

    again = h.run(refetch_existing=True)

    assert again.count(Outcome.REVISION_DETECTED) == 0
    assert len(h.reviews.list_open()) == 1


def test_page_chrome_change_is_stored_but_not_a_revision(h: Harness) -> None:
    h.run()
    chrome_only = read("statement_20240131.html").replace("About the Fed", "About Us")
    h.overrides[JAN] = html_response(chrome_only)

    report = h.run(refetch_existing=True)

    assert report.count(Outcome.REVISION_DETECTED) == 0
    assert h.snapshots.latest_snapshot(h.doc(JAN).id).version == 2
    assert h.reviews.list_open() == []


def test_discovery_failure_fails_the_run(h: Harness) -> None:
    h.overrides[CALENDAR] = httpx.Response(500)

    report = h.run()

    assert report.run.status is RunStatus.FAILED
    assert "discovery failed" in h.runs.get(report.run.id).error
    assert h.documents.list() == []


def test_new_earlier_document_after_analysis_opens_review_instead_of_relinking(
    h: Harness,
) -> None:
    h.overrides[CALENDAR] = calendar_with(DEC, MAR)
    h.run()
    mar = h.doc(MAR)
    dec = h.doc(DEC)
    assert mar.previous_document_id == dec.id
    transition(mar, DocumentStatus.ANALYZED, at=START + timedelta(days=1))
    h.documents.update(mar)

    h.overrides[CALENDAR] = calendar_with(DEC, JAN, MAR)
    h.run()
    h.run()

    mar, jan = h.doc(MAR), h.doc(JAN)
    assert mar.previous_document_id == dec.id
    assert jan.previous_document_id == dec.id
    (item,) = h.reviews.list_open()
    assert item.document_id == mar.id
    assert str(jan.id) in item.reason


def test_manual_adapter_uses_the_same_pipeline(h: Harness) -> None:
    adapter = ManualSourceAdapter(
        "FED",
        "fomc_statement",
        [
            ManualDocument("manual://a", "Statement A", START - timedelta(days=60), "One.\n\nTwo."),
            ManualDocument("manual://b", "Statement B", START - timedelta(days=10), "Three."),
        ],
        clock=h.clock,
    )
    service = h.service_for(adapter)

    report = service.run("FED", "fomc_statement")
    service.run("FED", "fomc_statement")

    a, b = h.doc("manual://a"), h.doc("manual://b")
    assert report.count(Outcome.INGESTED) == 2
    assert b.previous_document_id == a.id
    assert len(h.documents.list()) == 2
