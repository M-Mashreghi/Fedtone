"""Repository interfaces the application layer depends on.

Infrastructure provides the implementations (see ``fedtone.infrastructure.persistence``).
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.market import MarketSeries, Observation
from fedtone.domain.metrics import EventMetric
from fedtone.domain.models import (
    DocumentComparison,
    DocumentText,
    PolicyDocument,
    ProcessingRun,
    ReviewItem,
    SourceSnapshot,
    ToneAnalysis,
)
from fedtone.domain.summary import ResearchSummary


class PolicyDocumentRepository(Protocol):
    def add(self, document: PolicyDocument) -> tuple[PolicyDocument, bool]:
        """Insert a document idempotently.

        Returns ``(stored, created)``. When a document with the same canonical identity
        (central bank, document type, source URL) already exists, the stored document is
        returned unchanged and ``created`` is False.
        """
        ...

    def get(self, document_id: UUID) -> PolicyDocument | None: ...

    def get_by_canonical_identity(
        self, central_bank: str, document_type: str, source_url: str
    ) -> PolicyDocument | None: ...

    def update(self, document: PolicyDocument) -> None:
        """Persist status, provenance fields and newly appended history entries."""
        ...

    def list(
        self,
        *,
        central_bank: str | None = None,
        document_type: str | None = None,
        status: DocumentStatus | None = None,
        limit: int | None = 100,
        offset: int = 0,
    ) -> Sequence[PolicyDocument]:
        """Documents ordered by publication time (unknown last), then discovery time."""
        ...


class ProcessingRunRepository(Protocol):
    def add(self, run: ProcessingRun) -> None: ...

    def get(self, run_id: UUID) -> ProcessingRun | None: ...

    def update(self, run: ProcessingRun) -> None: ...


class ReviewItemRepository(Protocol):
    def add(self, item: ReviewItem) -> None: ...

    def get(self, item_id: UUID) -> ReviewItem | None: ...

    def update(self, item: ReviewItem) -> None: ...

    def list_open(
        self, *, document_id: UUID | None = None, limit: int | None = 100
    ) -> Sequence[ReviewItem]: ...


class SourceSnapshotRepository(Protocol):
    """Append-only store of raw snapshots and the texts parsed from them."""

    def add_snapshot(
        self,
        *,
        document_id: UUID,
        source_url: str,
        content: bytes,
        content_type: str,
        fetched_at: datetime,
    ) -> tuple[SourceSnapshot, bool]:
        """Store raw content unless identical content is already stored for the document.

        Returns ``(snapshot, created)``. New content gets the next version number.
        """
        ...

    def latest_snapshot(self, document_id: UUID) -> SourceSnapshot | None: ...

    def add_text(self, text: DocumentText) -> tuple[DocumentText, bool]:
        """Store a parsed text unless one exists for the same snapshot and processing versions."""
        ...

    def texts_for(self, document_id: UUID) -> Sequence[DocumentText]:
        """All parsed texts of a document, oldest first."""
        ...


class ToneAnalysisRepository(Protocol):
    """Append-only: re-analysis adds a new row; nothing is overwritten."""

    def add(self, analysis: ToneAnalysis) -> None: ...

    def get(self, analysis_id: UUID) -> ToneAnalysis | None: ...

    def latest_for_document(self, document_id: UUID) -> ToneAnalysis | None: ...


class ComparisonRepository(Protocol):
    def add(self, comparison: DocumentComparison) -> None: ...

    def latest_for_document(self, document_id: UUID) -> DocumentComparison | None: ...


class MarketObservationRepository(Protocol):
    def add_series(self, series: MarketSeries) -> int:
        """Store observations; a date already stored with the same value is skipped.

        Returns the number of rows written (new dates plus changed values).
        """
        ...

    def get_series(
        self, provider: str, symbol: str, start: date, end: date
    ) -> dict[date, Observation]:
        """Latest retrieved observation per date in ``[start, end]``."""
        ...


class EventMetricRepository(Protocol):
    def add_many(self, metrics: Sequence[EventMetric]) -> None: ...

    def for_document(self, document_id: UUID) -> list[EventMetric]:
        """Metrics of the latest computation for the document."""
        ...


class ResearchSummaryRepository(Protocol):
    def add(self, summary: ResearchSummary) -> None: ...

    def latest_for_document(self, document_id: UUID) -> ResearchSummary | None: ...
