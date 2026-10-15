from datetime import UTC, datetime

import pytest

from fedtone.application.review import ReviewService
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.models import PolicyDocument, ReviewItem, transition
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyReviewItemRepository,
)

NOW = datetime(2024, 5, 1, tzinfo=UTC)


@pytest.fixture
def setup(session_factory):
    documents = SqlAlchemyPolicyDocumentRepository(session_factory)
    reviews = SqlAlchemyReviewItemRepository(session_factory)
    doc = PolicyDocument("FED", "fomc_statement", "manual://x", NOW)
    documents.add(doc)
    for status in (
        DocumentStatus.FETCHED,
        DocumentStatus.PARSED,
        DocumentStatus.NORMALIZED,
        DocumentStatus.QUEUED_FOR_ANALYSIS,
        DocumentStatus.ANALYZED,
    ):
        transition(doc, status, at=NOW)
    transition(doc, DocumentStatus.NEEDS_REVIEW, at=NOW, reason="low confidence")
    documents.update(doc)
    item = ReviewItem(doc.id, DocumentStatus.ANALYZED, "low confidence", NOW)
    reviews.add(item)
    return ReviewService(documents, reviews, clock=lambda: NOW), documents, reviews, doc, item


def test_resolve_and_resume_continues_from_last_good_stage(setup) -> None:
    service, documents, reviews, doc, item = setup

    service.resolve(item.id, "checked evidence manually", resume=True)

    assert documents.get(doc.id).status is DocumentStatus.ANALYZED
    assert reviews.list_open() == []


def test_resolve_without_resume_rejects_document(setup) -> None:
    service, documents, _, doc, item = setup

    service.resolve(item.id, "source is not a policy statement", resume=False)

    stored = documents.get(doc.id)
    assert stored.status is DocumentStatus.FAILED
    assert stored.failure_reason == "rejected: source is not a policy statement"


def test_document_waits_until_all_its_review_items_are_resolved(setup) -> None:
    service, documents, reviews, doc, item = setup
    second = ReviewItem(doc.id, DocumentStatus.ANALYZED, "injection suspected", NOW)
    reviews.add(second)

    service.resolve(item.id, "ok", resume=True)
    assert documents.get(doc.id).status is DocumentStatus.NEEDS_REVIEW

    service.resolve(second.id, "ok", resume=True)
    assert documents.get(doc.id).status is DocumentStatus.ANALYZED


def test_unknown_item_raises(setup) -> None:
    service, *_ = setup
    from uuid import uuid4

    with pytest.raises(LookupError):
        service.resolve(uuid4(), "x", resume=True)
