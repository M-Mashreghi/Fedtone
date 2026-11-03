"""Final pipeline stage: publish market-linked documents with a validated research summary.

    MarketLinked + stored summary ─→ Published (terminal)

Publishing goes through ``transition()``, which requires the source content hash and an
evidence-backed analysis of that exact document version. A document that fails the guard is
routed to review instead of being published. Documents without a summary wait: the summary
stage runs first in every pipeline cycle, so a provider outage there only delays publication.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from fedtone.application.ports import (
    PolicyDocumentRepository,
    ResearchSummaryRepository,
    ReviewItemRepository,
    ToneAnalysisRepository,
)
from fedtone.domain.lifecycle import DocumentStatus, InvalidTransitionError
from fedtone.domain.models import PolicyDocument, ReviewItem, transition
from fedtone.observability import correlation_context

logger = logging.getLogger(__name__)


class PublicationOutcome(StrEnum):
    PUBLISHED = "published"
    AWAITING_SUMMARY = "awaiting_summary"
    REJECTED = "rejected"


@dataclass
class PublicationReport:
    outcomes: dict[PublicationOutcome, int] = field(
        default_factory=lambda: dict.fromkeys(PublicationOutcome, 0)
    )


class PublicationService:
    def __init__(
        self,
        *,
        documents: PolicyDocumentRepository,
        analyses: ToneAnalysisRepository,
        summaries: ResearchSummaryRepository,
        reviews: ReviewItemRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._documents = documents
        self._analyses = analyses
        self._summaries = summaries
        self._reviews = reviews
        self._clock = clock

    def run(self, central_bank: str, document_type: str) -> PublicationReport:
        report = PublicationReport()
        for document in self._documents.list(
            central_bank=central_bank,
            document_type=document_type,
            status=DocumentStatus.MARKET_LINKED,
            limit=None,
        ):
            report.outcomes[self._publish(document)] += 1
        return report

    def _publish(self, document: PolicyDocument) -> PublicationOutcome:
        with correlation_context(document_id=document.id):
            if self._summaries.latest_for_document(document.id) is None:
                return PublicationOutcome.AWAITING_SUMMARY
            analysis = self._analyses.latest_for_document(document.id)
            try:
                transition(document, DocumentStatus.PUBLISHED, at=self._clock(), analysis=analysis)
            except InvalidTransitionError as exc:
                reason = f"cannot publish: {exc}"
                logger.warning("needs review: %s", reason)
                stage = document.status
                transition(document, DocumentStatus.NEEDS_REVIEW, at=self._clock(), reason=reason)
                self._documents.update(document)
                self._reviews.add(ReviewItem(document.id, stage, reason, self._clock()))
                return PublicationOutcome.REJECTED
            self._documents.update(document)
            logger.info("published")
            return PublicationOutcome.PUBLISHED
