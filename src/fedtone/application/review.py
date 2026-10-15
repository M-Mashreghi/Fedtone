"""Human review queue actions."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fedtone.application.ports import PolicyDocumentRepository, ReviewItemRepository
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.models import ReviewItem, transition


class ReviewService:
    def __init__(
        self,
        documents: PolicyDocumentRepository,
        reviews: ReviewItemRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._documents = documents
        self._reviews = reviews
        self._clock = clock

    def resolve(self, item_id: UUID, note: str, *, resume: bool) -> ReviewItem:
        """Close a review item; with ``resume`` the document continues from its last good stage.

        Without ``resume`` a document in NeedsReview is marked Failed (rejected by the reviewer).
        """
        item = self._reviews.get(item_id)
        if item is None:
            raise LookupError(f"review item {item_id} does not exist")
        document = self._documents.get(item.document_id)
        if document is None:
            raise LookupError(f"document {item.document_id} does not exist")
        now = self._clock()
        item.resolve(note, at=now)
        self._reviews.update(item)

        others_open = self._reviews.list_open(document_id=document.id)
        if document.status is DocumentStatus.NEEDS_REVIEW and not others_open:
            if resume:
                transition(document, document.last_successful_status, at=now)
            else:
                transition(document, DocumentStatus.FAILED, at=now, reason=f"rejected: {note}")
            self._documents.update(document)
        return item
