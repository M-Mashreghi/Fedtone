"""Read-side queries shared by the API, exports and dashboard.

Every value returned here is a stored, canonical record (or a field of one); nothing is
recalculated. The timeline, the signal export and the document list all come from
``signals()``, so a chart, an export and an API response cannot disagree.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from fedtone.application.ports import (
    ComparisonRepository,
    EventMetricRepository,
    PolicyDocumentRepository,
    ProcessingRunRepository,
    ResearchSummaryRepository,
    ReviewItemRepository,
    SourceSnapshotRepository,
    ToneAnalysisRepository,
)
from fedtone.application.research_summary import metric_display
from fedtone.application.tone_profiles import SPEAKER_DOCUMENT_TYPES, profile_id_of
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.metrics import EventMetric, WindowSpec
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
from fedtone.domain.tone_schema import PolicyDimension

DIMENSIONS = tuple(d.value for d in PolicyDimension)
# A live run sets as_of to "now"; a gap larger than this means an explicit backtest cut-off.
BACKTEST_TOLERANCE = timedelta(hours=1)


@dataclass(frozen=True)
class DocumentFilter:
    central_bank: str | None = None
    document_type: str | None = None
    status: DocumentStatus | None = None
    model: str | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None
    speaker: str | None = None
    # Speeches are kept out of committee views unless asked for (type or speaker).
    include_speeches: bool = False

    def __post_init__(self) -> None:
        for name in ("published_from", "published_to"):
            value = getattr(self, name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")

    def matches(self, document: PolicyDocument, analysis: ToneAnalysis | None) -> bool:
        if self.speaker is not None and document.speaker != self.speaker:
            return False
        speech_requested = (
            self.include_speeches
            or self.speaker is not None
            or self.document_type in SPEAKER_DOCUMENT_TYPES
        )
        if document.document_type in SPEAKER_DOCUMENT_TYPES and not speech_requested:
            return False
        if self.model is not None and (analysis is None or analysis.model != self.model):
            return False
        if self.published_from is None and self.published_to is None:
            return True
        if document.published_at is None:
            return False
        if self.published_from is not None and document.published_at < self.published_from:
            return False
        return self.published_to is None or document.published_at <= self.published_to


@dataclass(frozen=True)
class DocumentRow:
    document: PolicyDocument
    analysis: ToneAnalysis | None
    comparison: DocumentComparison | None
    open_reviews: int

    @property
    def needs_review(self) -> bool:
        return self.document.status is DocumentStatus.NEEDS_REVIEW or self.open_reviews > 0


@dataclass(frozen=True)
class DocumentPage:
    items: list[DocumentRow]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class DocumentDetail:
    row: DocumentRow
    text: DocumentText | None
    snapshot: SourceSnapshot | None
    metrics: list[EventMetric]
    summary: ResearchSummary | None
    reviews: list[ReviewItem]

    @property
    def document(self) -> PolicyDocument:
        return self.row.document

    @property
    def analysis(self) -> ToneAnalysis | None:
        return self.row.analysis

    @property
    def comparison(self) -> DocumentComparison | None:
        return self.row.comparison


@dataclass(frozen=True)
class SystemTelemetry:
    documents_by_status: dict[str, int]
    open_reviews: int
    recent_runs: list[ProcessingRun]


def signal_record(row: DocumentRow) -> dict[str, object]:
    """One flat, export-ready record of a document's stored tone signal."""
    document, analysis, comparison = row.document, row.analysis, row.comparison
    record: dict[str, object] = {
        "document_id": str(document.id),
        "central_bank": document.central_bank,
        "document_type": document.document_type,
        "speaker": document.speaker,
        "published_at": document.published_at.isoformat() if document.published_at else None,
        "status": document.status.value,
        "needs_review": row.needs_review,
        "net_tone_score": analysis.net_tone_score if analysis else None,
        "classification": analysis.classification.value if analysis else None,
        "confidence": analysis.confidence if analysis else None,
        "overall_delta": comparison.overall_delta if comparison else None,
    }
    for name in DIMENSIONS:
        record[f"dim_{name}"] = analysis.dimension_scores.get(name) if analysis else None
    record.update(
        {
            "evidence_count": len(analysis.evidence) if analysis else 0,
            "uncertainty_flags": ";".join(analysis.uncertainty_flags) if analysis else "",
            "analysis_id": str(analysis.id) if analysis else None,
            "model": analysis.model if analysis else None,
            "prompt_version": analysis.prompt_version if analysis else None,
            "tone_profile": profile_id_of(analysis.prompt_version) if analysis else None,
            "schema_version": analysis.schema_version if analysis else None,
            "analyzed_at": analysis.created_at.isoformat() if analysis else None,
            "content_hash": document.content_hash,
            "source_url": document.source_url,
        }
    )
    return record


def metric_sort_key(metric: EventMetric) -> tuple:
    window = WindowSpec.parse(metric.window)
    return metric.symbol, window.start, window.end, metric.kind.value


def is_backtest(metric: EventMetric) -> bool:
    """Computed with an explicit ``as_of`` cut-off well before the computation itself."""
    return metric.computed_at - metric.as_of > BACKTEST_TOLERANCE


def metric_record(metric: EventMetric) -> dict[str, object]:
    """A stored market metric plus its canonical display string (no recalculation)."""
    return {
        "symbol": metric.symbol,
        "window": metric.window,
        "kind": metric.kind.value,
        "unit": metric.unit.value,
        "value": metric.value,
        "display": metric_display(metric),
        "missing_reason": metric.missing_reason.value if metric.missing_reason else None,
        "benchmark_symbol": metric.benchmark_symbol,
        "event_day": metric.event_day.isoformat(),
        "start_date": metric.start_date.isoformat(),
        "end_date": metric.end_date.isoformat(),
        "start_value": metric.start_value,
        "end_value": metric.end_value,
        "alignment_rule": metric.alignment_rule,
        "provider": metric.provider,
        "as_of": metric.as_of.isoformat(),
        "computed_at": metric.computed_at.isoformat(),
        "end_observed_at": metric.end_observed_at.isoformat(),
        "metric_version": metric.metric_version,
        "backtest_mode": is_backtest(metric),
    }


class ResearchQueryService:
    def __init__(
        self,
        *,
        documents: PolicyDocumentRepository,
        snapshots: SourceSnapshotRepository,
        analyses: ToneAnalysisRepository,
        comparisons: ComparisonRepository,
        metrics: EventMetricRepository,
        summaries: ResearchSummaryRepository,
        reviews: ReviewItemRepository,
        runs: ProcessingRunRepository,
    ) -> None:
        self._documents = documents
        self._snapshots = snapshots
        self._analyses = analyses
        self._comparisons = comparisons
        self._metrics = metrics
        self._summaries = summaries
        self._reviews = reviews
        self._runs = runs

    def _row(self, document: PolicyDocument, analysis: ToneAnalysis | None) -> DocumentRow:
        return DocumentRow(
            document=document,
            analysis=analysis,
            comparison=self._comparisons.latest_for_document(document.id),
            open_reviews=len(self._reviews.list_open(document_id=document.id, limit=None)),
        )

    def rows(self, filters: DocumentFilter) -> list[DocumentRow]:
        """All matching documents, ordered by publication time (unknown last)."""
        rows = []
        for document in self._documents.list(
            central_bank=filters.central_bank,
            document_type=filters.document_type,
            status=filters.status,
            limit=None,
        ):
            analysis = self._analyses.latest_for_document(document.id)
            if filters.matches(document, analysis):
                rows.append(self._row(document, analysis))
        return rows

    def list_documents(
        self, filters: DocumentFilter, *, limit: int = 50, offset: int = 0
    ) -> DocumentPage:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset non-negative")
        rows = self.rows(filters)
        return DocumentPage(rows[offset : offset + limit], len(rows), limit, offset)

    def signals(self, filters: DocumentFilter) -> list[dict[str, object]]:
        return [signal_record(row) for row in self.rows(filters)]

    def detail(self, document_id: UUID) -> DocumentDetail:
        document = self._documents.get(document_id)
        if document is None:
            raise LookupError(f"document {document_id} not found")
        texts = self._snapshots.texts_for(document_id)
        return DocumentDetail(
            row=self._row(document, self._analyses.latest_for_document(document_id)),
            text=texts[-1] if texts else None,
            snapshot=self._snapshots.latest_snapshot(document_id),
            metrics=sorted(
                self._metrics.for_document(document_id),
                key=metric_sort_key,
            ),
            summary=self._summaries.latest_for_document(document_id),
            reviews=list(self._reviews.list_open(document_id=document_id, limit=None)),
        )

    def previous_analysis(self, comparison: DocumentComparison | None) -> ToneAnalysis | None:
        if comparison is None or comparison.previous_analysis_id is None:
            return None
        return self._analyses.get(comparison.previous_analysis_id)

    def open_reviews(self, *, limit: int | None = 100) -> Sequence[ReviewItem]:
        return self._reviews.list_open(limit=limit)

    def recent_runs(self, *, limit: int = 20) -> Sequence[ProcessingRun]:
        return self._runs.list_recent(limit=limit)

    def speakers(self, filters: DocumentFilter) -> list[dict[str, object]]:
        """Per speaker: speech count, first/last date, stored scores and their mean.

        The mean is a plain average of stored scores (deterministic), within one bank and
        tone profile; speakers are never mixed with committee documents.
        """
        speech_filter = replace(filters, include_speeches=True)
        groups: dict[tuple[str, str], list[DocumentRow]] = {}
        for row in self.rows(speech_filter):
            document = row.document
            if document.speaker is None or document.document_type not in SPEAKER_DOCUMENT_TYPES:
                continue
            groups.setdefault((document.central_bank, document.speaker), []).append(row)
        result = []
        for (bank, speaker), rows in sorted(groups.items()):
            scores = [r.analysis.net_tone_score for r in rows if r.analysis is not None]
            dated = [r.document.published_at for r in rows if r.document.published_at]
            latest = rows[-1]
            result.append(
                {
                    "central_bank": bank,
                    "speaker": speaker,
                    "speeches": len(rows),
                    "scored": len(scores),
                    "first_at": min(dated).isoformat() if dated else None,
                    "last_at": max(dated).isoformat() if dated else None,
                    "mean_tone": sum(scores) / len(scores) if scores else None,
                    "latest_tone": latest.analysis.net_tone_score if latest.analysis else None,
                    "latest_document_id": str(latest.document.id),
                }
            )
        return result

    def telemetry(self) -> SystemTelemetry:
        counts = dict.fromkeys((s.value for s in DocumentStatus), 0)
        for document in self._documents.list(limit=None):
            counts[document.status.value] += 1
        return SystemTelemetry(
            documents_by_status=counts,
            open_reviews=len(self._reviews.list_open(limit=None)),
            recent_runs=list(self._runs.list_recent(limit=10)),
        )
