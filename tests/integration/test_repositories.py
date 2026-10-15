from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.models import (
    PolicyDocument,
    ProcessingRun,
    ReviewItem,
    RunStatus,
    RunTrigger,
    transition,
)
from fedtone.infrastructure.persistence.orm import PolicyDocumentRow
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyProcessingRunRepository,
    SqlAlchemyReviewItemRepository,
)

NOW = datetime(2024, 1, 31, 19, 0, tzinfo=UTC)
URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20240131a.htm"


def make_document(source_url: str = URL, published_at: datetime | None = NOW) -> PolicyDocument:
    return PolicyDocument(
        central_bank="FED",
        document_type="fomc_statement",
        source_url=source_url,
        discovered_at=NOW,
        published_at=published_at,
        content_hash="d" * 64,
    )


@pytest.fixture
def documents(session_factory: sessionmaker[Session]) -> SqlAlchemyPolicyDocumentRepository:
    return SqlAlchemyPolicyDocumentRepository(session_factory)


def test_insert_query_update_document(documents: SqlAlchemyPolicyDocumentRepository) -> None:
    document = make_document()

    stored, created = documents.add(document)
    assert created
    assert stored.id == document.id

    loaded = documents.get(document.id)
    assert loaded is not None
    assert loaded.source_url == URL
    assert loaded.published_at == NOW
    assert loaded.status is DocumentStatus.DISCOVERED

    transition(loaded, DocumentStatus.FETCHED, at=NOW + timedelta(minutes=1))
    transition(loaded, DocumentStatus.PARSED, at=NOW + timedelta(minutes=2))
    loaded.parser_version = "fomc-html-1"
    documents.update(loaded)

    reloaded = documents.get(document.id)
    assert reloaded is not None
    assert reloaded.status is DocumentStatus.PARSED
    assert reloaded.last_successful_status is DocumentStatus.PARSED
    assert reloaded.parser_version == "fomc-html-1"
    assert [c.to_status for c in reloaded.history] == [
        DocumentStatus.FETCHED,
        DocumentStatus.PARSED,
    ]


def test_failure_state_round_trips(documents: SqlAlchemyPolicyDocumentRepository) -> None:
    document = make_document()
    documents.add(document)
    transition(document, DocumentStatus.FETCHED, at=NOW)
    transition(document, DocumentStatus.FAILED, at=NOW, reason="parser could not find body")
    documents.update(document)

    loaded = documents.get(document.id)
    assert loaded is not None
    assert loaded.status is DocumentStatus.FAILED
    assert loaded.last_successful_status is DocumentStatus.FETCHED
    assert loaded.failure_reason == "parser could not find body"
    assert loaded.history[-1].reason == "parser could not find body"


def test_duplicate_canonical_document_is_idempotent(
    documents: SqlAlchemyPolicyDocumentRepository,
) -> None:
    first, created_first = documents.add(make_document())
    second, created_second = documents.add(make_document())

    assert created_first
    assert not created_second
    assert second.id == first.id
    assert len(documents.list()) == 1


def test_database_rejects_duplicate_canonical_identity(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        for document in (make_document(), make_document()):
            session.add(
                PolicyDocumentRow(
                    id=document.id,
                    central_bank=document.central_bank,
                    document_type=document.document_type,
                    source_url=document.source_url,
                    discovered_at=document.discovered_at,
                    status=document.status.value,
                    last_successful_status=document.last_successful_status.value,
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()


def test_timestamps_round_trip_as_utc(documents: SqlAlchemyPolicyDocumentRepository) -> None:
    eastern = timezone(timedelta(hours=-5))
    document = make_document(published_at=datetime(2024, 1, 31, 14, 0, tzinfo=eastern))
    documents.add(document)

    loaded = documents.get(document.id)
    assert loaded is not None
    assert loaded.published_at == NOW
    assert loaded.published_at.tzinfo is UTC


def test_list_filters_and_orders_by_publication(
    documents: SqlAlchemyPolicyDocumentRepository,
) -> None:
    later = make_document(source_url=URL + "?b", published_at=NOW + timedelta(days=40))
    earlier = make_document(source_url=URL + "?a", published_at=NOW)
    documents.add(later)
    documents.add(earlier)
    transition(later, DocumentStatus.FETCHED, at=NOW)
    documents.update(later)

    assert [d.id for d in documents.list()] == [earlier.id, later.id]
    assert [d.id for d in documents.list(status=DocumentStatus.FETCHED)] == [later.id]
    assert documents.list(central_bank="ECB") == []
    assert [d.id for d in documents.list(limit=1, offset=1)] == [later.id]


def test_update_unknown_document_raises(documents: SqlAlchemyPolicyDocumentRepository) -> None:
    with pytest.raises(LookupError):
        documents.update(make_document())


def test_processing_run_round_trip(session_factory: sessionmaker[Session]) -> None:
    runs = SqlAlchemyProcessingRunRepository(session_factory)
    run = ProcessingRun(trigger=RunTrigger.SCHEDULED, started_at=NOW)
    runs.add(run)

    run.finish(RunStatus.FAILED, at=NOW + timedelta(minutes=3), error="source timeout")
    runs.update(run)

    loaded = runs.get(run.id)
    assert loaded is not None
    assert loaded.status is RunStatus.FAILED
    assert loaded.finished_at == NOW + timedelta(minutes=3)
    assert loaded.error == "source timeout"


def test_review_items_open_and_resolved(
    session_factory: sessionmaker[Session], documents: SqlAlchemyPolicyDocumentRepository
) -> None:
    reviews = SqlAlchemyReviewItemRepository(session_factory)
    document = make_document()
    documents.add(document)
    first = ReviewItem(document.id, DocumentStatus.ANALYZED, "low confidence", NOW)
    second = ReviewItem(document.id, DocumentStatus.PARSED, "ambiguous date", NOW + timedelta(1))
    reviews.add(first)
    reviews.add(second)

    first.resolve("approved", at=NOW + timedelta(days=2))
    reviews.update(first)

    assert [item.id for item in reviews.list_open()] == [second.id]
    loaded = reviews.get(first.id)
    assert loaded is not None
    assert loaded.resolution == "approved"
