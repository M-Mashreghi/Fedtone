"""SQLAlchemy implementations of the application repository ports."""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.market import MarketSeries, Observation
from fedtone.domain.metrics import EventMetric, MetricKind, MetricUnit, MissingReason
from fedtone.domain.models import (
    DocumentComparison,
    DocumentText,
    EvidenceItem,
    PolicyDocument,
    ProcessingRun,
    ReviewItem,
    RunStatus,
    RunTrigger,
    SourceSnapshot,
    StatusChange,
    ToneAnalysis,
    ToneClassification,
)
from fedtone.domain.summary import Fact, ResearchSummary
from fedtone.infrastructure.persistence.orm import (
    DocumentComparisonRow,
    DocumentStatusChangeRow,
    DocumentTextRow,
    EventMetricRow,
    MarketObservationRow,
    PolicyDocumentRow,
    ProcessingRunRow,
    ResearchSummaryRow,
    ReviewItemRow,
    SourceSnapshotRow,
    ToneAnalysisRow,
)

_DOCUMENT_FIELDS = (
    "central_bank",
    "document_type",
    "source_url",
    "discovered_at",
    "published_at",
    "fetched_at",
    "content_hash",
    "parser_version",
    "previous_document_id",
    "published_analysis_id",
    "failure_reason",
)


def _document_to_domain(row: PolicyDocumentRow) -> PolicyDocument:
    document = PolicyDocument(
        id=row.id,
        status=DocumentStatus(row.status),
        last_successful_status=DocumentStatus(row.last_successful_status),
        **{name: getattr(row, name) for name in _DOCUMENT_FIELDS},
    )
    document.history = [
        StatusChange(
            from_status=DocumentStatus(change.from_status),
            to_status=DocumentStatus(change.to_status),
            at=change.at,
            reason=change.reason,
        )
        for change in row.history
    ]
    return document


def _apply_document(row: PolicyDocumentRow, document: PolicyDocument) -> None:
    for name in _DOCUMENT_FIELDS:
        setattr(row, name, getattr(document, name))
    row.status = document.status.value
    row.last_successful_status = document.last_successful_status.value
    # History is append-only: persist only entries not stored yet.
    for sequence in range(len(row.history), len(document.history)):
        change = document.history[sequence]
        row.history.append(
            DocumentStatusChangeRow(
                sequence=sequence,
                from_status=change.from_status.value,
                to_status=change.to_status.value,
                at=change.at,
                reason=change.reason,
            )
        )


class SqlAlchemyPolicyDocumentRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, document: PolicyDocument) -> tuple[PolicyDocument, bool]:
        existing = self.get_by_canonical_identity(
            document.central_bank, document.document_type, document.source_url
        )
        if existing is not None:
            return existing, False
        with self._session_factory() as session:
            row = PolicyDocumentRow(id=document.id)
            _apply_document(row, document)
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                # Lost a race with a concurrent insert of the same canonical document.
                session.rollback()
                stored = self.get_by_canonical_identity(
                    document.central_bank, document.document_type, document.source_url
                )
                if stored is None:
                    raise
                return stored, False
        return document, True

    def get(self, document_id: UUID) -> PolicyDocument | None:
        with self._session_factory() as session:
            row = session.get(PolicyDocumentRow, document_id)
            return None if row is None else _document_to_domain(row)

    def get_by_canonical_identity(
        self, central_bank: str, document_type: str, source_url: str
    ) -> PolicyDocument | None:
        with self._session_factory() as session:
            row = session.scalars(
                select(PolicyDocumentRow).where(
                    PolicyDocumentRow.central_bank == central_bank,
                    PolicyDocumentRow.document_type == document_type,
                    PolicyDocumentRow.source_url == source_url,
                )
            ).one_or_none()
            return None if row is None else _document_to_domain(row)

    def update(self, document: PolicyDocument) -> None:
        with self._session_factory() as session:
            row = session.get(PolicyDocumentRow, document.id)
            if row is None:
                raise LookupError(f"policy document {document.id} does not exist")
            _apply_document(row, document)
            session.commit()

    def list(
        self,
        *,
        central_bank: str | None = None,
        document_type: str | None = None,
        status: DocumentStatus | None = None,
        limit: int | None = 100,
        offset: int = 0,
    ) -> Sequence[PolicyDocument]:
        query = select(PolicyDocumentRow)
        if central_bank is not None:
            query = query.where(PolicyDocumentRow.central_bank == central_bank)
        if document_type is not None:
            query = query.where(PolicyDocumentRow.document_type == document_type)
        if status is not None:
            query = query.where(PolicyDocumentRow.status == status.value)
        query = query.order_by(
            PolicyDocumentRow.published_at.is_(None),
            PolicyDocumentRow.published_at,
            PolicyDocumentRow.discovered_at,
            PolicyDocumentRow.source_url,
        )
        with self._session_factory() as session:
            if limit is not None:
                query = query.limit(limit)
            rows = session.scalars(query.offset(offset)).all()
            return [_document_to_domain(row) for row in rows]


def _run_to_domain(row: ProcessingRunRow) -> ProcessingRun:
    return ProcessingRun(
        id=row.id,
        trigger=RunTrigger(row.trigger),
        started_at=row.started_at,
        status=RunStatus(row.status),
        finished_at=row.finished_at,
        error=row.error,
    )


def _apply_run(row: ProcessingRunRow, run: ProcessingRun) -> None:
    row.trigger = run.trigger.value
    row.status = run.status.value
    row.started_at = run.started_at
    row.finished_at = run.finished_at
    row.error = run.error


class SqlAlchemyProcessingRunRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, run: ProcessingRun) -> None:
        with self._session_factory() as session:
            row = ProcessingRunRow(id=run.id)
            _apply_run(row, run)
            session.add(row)
            session.commit()

    def get(self, run_id: UUID) -> ProcessingRun | None:
        with self._session_factory() as session:
            row = session.get(ProcessingRunRow, run_id)
            return None if row is None else _run_to_domain(row)

    def update(self, run: ProcessingRun) -> None:
        with self._session_factory() as session:
            row = session.get(ProcessingRunRow, run.id)
            if row is None:
                raise LookupError(f"processing run {run.id} does not exist")
            _apply_run(row, run)
            session.commit()


def _review_to_domain(row: ReviewItemRow) -> ReviewItem:
    return ReviewItem(
        id=row.id,
        document_id=row.document_id,
        stage=DocumentStatus(row.stage),
        reason=row.reason,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        resolution=row.resolution,
    )


class SqlAlchemyReviewItemRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, item: ReviewItem) -> None:
        with self._session_factory() as session:
            session.add(
                ReviewItemRow(
                    id=item.id,
                    document_id=item.document_id,
                    stage=item.stage.value,
                    reason=item.reason,
                    created_at=item.created_at,
                    resolved_at=item.resolved_at,
                    resolution=item.resolution,
                )
            )
            session.commit()

    def get(self, item_id: UUID) -> ReviewItem | None:
        with self._session_factory() as session:
            row = session.get(ReviewItemRow, item_id)
            return None if row is None else _review_to_domain(row)

    def update(self, item: ReviewItem) -> None:
        with self._session_factory() as session:
            row = session.get(ReviewItemRow, item.id)
            if row is None:
                raise LookupError(f"review item {item.id} does not exist")
            row.resolved_at = item.resolved_at
            row.resolution = item.resolution
            session.commit()

    def list_open(
        self, *, document_id: UUID | None = None, limit: int | None = 100
    ) -> Sequence[ReviewItem]:
        query = (
            select(ReviewItemRow)
            .where(ReviewItemRow.resolved_at.is_(None))
            .order_by(ReviewItemRow.created_at)
        )
        if document_id is not None:
            query = query.where(ReviewItemRow.document_id == document_id)
        if limit is not None:
            query = query.limit(limit)
        with self._session_factory() as session:
            return [_review_to_domain(row) for row in session.scalars(query).all()]


def _snapshot_to_domain(row: SourceSnapshotRow) -> SourceSnapshot:
    return SourceSnapshot(
        id=row.id,
        document_id=row.document_id,
        version=row.version,
        source_url=row.source_url,
        content=row.content,
        content_type=row.content_type,
        raw_hash=row.raw_hash,
        fetched_at=row.fetched_at,
    )


def _text_to_domain(row: DocumentTextRow) -> DocumentText:
    return DocumentText(
        id=row.id,
        document_id=row.document_id,
        snapshot_id=row.snapshot_id,
        title=row.title,
        paragraphs=tuple(row.paragraphs),
        parser_version=row.parser_version,
        normalizer_version=row.normalizer_version,
        text_hash=row.text_hash,
        published_at=row.published_at,
        created_at=row.created_at,
    )


class SqlAlchemySourceSnapshotRepository:
    """Append-only: rows are inserted, never updated or deleted."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add_snapshot(
        self,
        *,
        document_id: UUID,
        source_url: str,
        content: bytes,
        content_type: str,
        fetched_at: datetime,
    ) -> tuple[SourceSnapshot, bool]:
        with self._session_factory() as session:
            candidate_hash = SourceSnapshot.create(
                document_id=document_id,
                version=1,
                source_url=source_url,
                content=content,
                content_type=content_type,
                fetched_at=fetched_at,
            ).raw_hash
            existing = session.scalars(
                select(SourceSnapshotRow).where(
                    SourceSnapshotRow.document_id == document_id,
                    SourceSnapshotRow.raw_hash == candidate_hash,
                )
            ).one_or_none()
            if existing is not None:
                return _snapshot_to_domain(existing), False
            latest_version = session.scalar(
                select(func.max(SourceSnapshotRow.version)).where(
                    SourceSnapshotRow.document_id == document_id
                )
            )
            snapshot = SourceSnapshot.create(
                document_id=document_id,
                version=(latest_version or 0) + 1,
                source_url=source_url,
                content=content,
                content_type=content_type,
                fetched_at=fetched_at,
            )
            session.add(
                SourceSnapshotRow(
                    id=snapshot.id,
                    document_id=snapshot.document_id,
                    version=snapshot.version,
                    source_url=snapshot.source_url,
                    content=snapshot.content,
                    content_type=snapshot.content_type,
                    raw_hash=snapshot.raw_hash,
                    fetched_at=snapshot.fetched_at,
                )
            )
            session.commit()
            return snapshot, True

    def latest_snapshot(self, document_id: UUID) -> SourceSnapshot | None:
        with self._session_factory() as session:
            row = session.scalars(
                select(SourceSnapshotRow)
                .where(SourceSnapshotRow.document_id == document_id)
                .order_by(SourceSnapshotRow.version.desc())
                .limit(1)
            ).one_or_none()
            return None if row is None else _snapshot_to_domain(row)

    def add_text(self, text: DocumentText) -> tuple[DocumentText, bool]:
        with self._session_factory() as session:
            existing = session.scalars(
                select(DocumentTextRow).where(
                    DocumentTextRow.snapshot_id == text.snapshot_id,
                    DocumentTextRow.parser_version == text.parser_version,
                    DocumentTextRow.normalizer_version == text.normalizer_version,
                )
            ).one_or_none()
            if existing is not None:
                return _text_to_domain(existing), False
            session.add(
                DocumentTextRow(
                    id=text.id,
                    document_id=text.document_id,
                    snapshot_id=text.snapshot_id,
                    title=text.title,
                    paragraphs=list(text.paragraphs),
                    parser_version=text.parser_version,
                    normalizer_version=text.normalizer_version,
                    text_hash=text.text_hash,
                    published_at=text.published_at,
                    created_at=text.created_at,
                )
            )
            session.commit()
            return text, True

    def texts_for(self, document_id: UUID) -> Sequence[DocumentText]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(DocumentTextRow)
                .join(SourceSnapshotRow, DocumentTextRow.snapshot_id == SourceSnapshotRow.id)
                .where(DocumentTextRow.document_id == document_id)
                .order_by(SourceSnapshotRow.version, DocumentTextRow.created_at)
            ).all()
            return [_text_to_domain(row) for row in rows]


def _analysis_to_domain(row: ToneAnalysisRow) -> ToneAnalysis:
    return ToneAnalysis(
        id=row.id,
        document_id=row.document_id,
        document_hash=row.document_hash,
        model=row.model,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        net_tone_score=row.net_tone_score,
        classification=ToneClassification(row.classification),
        confidence=row.confidence,
        evidence=tuple(EvidenceItem(**item) for item in row.evidence),
        explanation=row.explanation,
        created_at=row.created_at,
        dimension_scores=dict(row.dimension_scores or {}),
        uncertainty_flags=tuple(row.uncertainty_flags or ()),
        temperature=row.temperature,
        raw_output=row.raw_output,
    )


class SqlAlchemyToneAnalysisRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, analysis: ToneAnalysis) -> None:
        with self._session_factory() as session:
            session.add(
                ToneAnalysisRow(
                    id=analysis.id,
                    document_id=analysis.document_id,
                    document_hash=analysis.document_hash,
                    model=analysis.model,
                    prompt_version=analysis.prompt_version,
                    schema_version=analysis.schema_version,
                    net_tone_score=analysis.net_tone_score,
                    classification=analysis.classification.value,
                    confidence=analysis.confidence,
                    evidence=[
                        {
                            "text": e.text,
                            "dimension": e.dimension,
                            "direction": e.direction,
                            "match": e.match,
                        }
                        for e in analysis.evidence
                    ],
                    explanation=analysis.explanation,
                    created_at=analysis.created_at,
                    dimension_scores=analysis.dimension_scores,
                    uncertainty_flags=list(analysis.uncertainty_flags),
                    temperature=analysis.temperature,
                    raw_output=analysis.raw_output,
                )
            )
            session.commit()

    def get(self, analysis_id: UUID) -> ToneAnalysis | None:
        with self._session_factory() as session:
            row = session.get(ToneAnalysisRow, analysis_id)
            return None if row is None else _analysis_to_domain(row)

    def latest_for_document(self, document_id: UUID) -> ToneAnalysis | None:
        with self._session_factory() as session:
            row = session.scalars(
                select(ToneAnalysisRow)
                .where(ToneAnalysisRow.document_id == document_id)
                .order_by(ToneAnalysisRow.created_at.desc())
                .limit(1)
            ).one_or_none()
            return None if row is None else _analysis_to_domain(row)


class SqlAlchemyComparisonRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, comparison: DocumentComparison) -> None:
        with self._session_factory() as session:
            session.add(
                DocumentComparisonRow(
                    id=comparison.id,
                    document_id=comparison.document_id,
                    current_analysis_id=comparison.current_analysis_id,
                    previous_document_id=comparison.previous_document_id,
                    previous_analysis_id=comparison.previous_analysis_id,
                    created_at=comparison.created_at,
                    diff=comparison.diff,
                    overall_delta=comparison.overall_delta,
                    dimension_deltas=comparison.dimension_deltas,
                    interpretation=comparison.interpretation,
                    model=comparison.model,
                    prompt_version=comparison.prompt_version,
                    schema_version=comparison.schema_version,
                    uncertainty_flags=list(comparison.uncertainty_flags),
                )
            )
            session.commit()

    def latest_for_document(self, document_id: UUID) -> DocumentComparison | None:
        with self._session_factory() as session:
            row = session.scalars(
                select(DocumentComparisonRow)
                .where(DocumentComparisonRow.document_id == document_id)
                .order_by(DocumentComparisonRow.created_at.desc())
                .limit(1)
            ).one_or_none()
            if row is None:
                return None
            return DocumentComparison(
                id=row.id,
                document_id=row.document_id,
                current_analysis_id=row.current_analysis_id,
                previous_document_id=row.previous_document_id,
                previous_analysis_id=row.previous_analysis_id,
                created_at=row.created_at,
                diff=row.diff,
                overall_delta=row.overall_delta,
                dimension_deltas=dict(row.dimension_deltas or {}),
                interpretation=row.interpretation,
                model=row.model,
                prompt_version=row.prompt_version,
                schema_version=row.schema_version,
                uncertainty_flags=tuple(row.uncertainty_flags or ()),
            )


class SqlAlchemyMarketObservationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add_series(self, series: MarketSeries) -> int:
        if not series.observations:
            return 0
        dates = [o.date for o in series.observations]
        with self._session_factory() as session:
            current = self._latest(session, series.provider, series.symbol, min(dates), max(dates))
            written = 0
            for obs in series.observations:
                stored = current.get(obs.date)
                if stored is not None and stored.value == obs.value:
                    continue
                session.add(
                    MarketObservationRow(
                        provider=series.provider,
                        symbol=series.symbol,
                        date=obs.date,
                        value=obs.value,
                        retrieved_at=obs.retrieved_at,
                    )
                )
                written += 1
            session.commit()
            return written

    def get_series(
        self, provider: str, symbol: str, start: date, end: date
    ) -> dict[date, Observation]:
        with self._session_factory() as session:
            return self._latest(session, provider, symbol, start, end)

    @staticmethod
    def _latest(
        session: Session, provider: str, symbol: str, start: date, end: date
    ) -> dict[date, Observation]:
        rows = session.scalars(
            select(MarketObservationRow)
            .where(
                MarketObservationRow.provider == provider,
                MarketObservationRow.symbol == symbol,
                MarketObservationRow.date >= start,
                MarketObservationRow.date <= end,
            )
            .order_by(MarketObservationRow.date, MarketObservationRow.id)
        ).all()
        # Later rows for the same date are newer values (append-only, increasing id).
        return {
            row.date: Observation(row.symbol, row.date, row.value, row.provider, row.retrieved_at)
            for row in rows
        }


class SqlAlchemyEventMetricRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add_many(self, metrics: Sequence[EventMetric]) -> None:
        with self._session_factory() as session:
            for m in metrics:
                session.add(
                    EventMetricRow(
                        id=m.id,
                        document_id=m.document_id,
                        symbol=m.symbol,
                        window=m.window,
                        kind=m.kind.value,
                        unit=m.unit.value,
                        value=m.value,
                        missing_reason=m.missing_reason.value if m.missing_reason else None,
                        event_day=m.event_day,
                        alignment_rule=m.alignment_rule,
                        start_date=m.start_date,
                        end_date=m.end_date,
                        start_value=m.start_value,
                        end_value=m.end_value,
                        end_observed_at=m.end_observed_at,
                        provider=m.provider,
                        as_of=m.as_of,
                        computed_at=m.computed_at,
                        metric_version=m.metric_version,
                        benchmark_symbol=m.benchmark_symbol,
                    )
                )
            session.commit()

    def for_document(self, document_id: UUID) -> list[EventMetric]:
        with self._session_factory() as session:
            latest = session.scalar(
                select(func.max(EventMetricRow.computed_at)).where(
                    EventMetricRow.document_id == document_id
                )
            )
            if latest is None:
                return []
            rows = session.scalars(
                select(EventMetricRow)
                .where(
                    EventMetricRow.document_id == document_id,
                    EventMetricRow.computed_at == latest,
                )
                .order_by(EventMetricRow.symbol, EventMetricRow.window, EventMetricRow.kind)
            ).all()
            return [_metric_to_domain(row) for row in rows]


def _metric_to_domain(row: EventMetricRow) -> EventMetric:
    return EventMetric(
        id=row.id,
        document_id=row.document_id,
        symbol=row.symbol,
        window=row.window,
        kind=MetricKind(row.kind),
        unit=MetricUnit(row.unit),
        value=row.value,
        missing_reason=MissingReason(row.missing_reason) if row.missing_reason else None,
        event_day=row.event_day,
        alignment_rule=row.alignment_rule,
        start_date=row.start_date,
        end_date=row.end_date,
        start_value=row.start_value,
        end_value=row.end_value,
        end_observed_at=row.end_observed_at,
        provider=row.provider,
        as_of=row.as_of,
        computed_at=row.computed_at,
        metric_version=row.metric_version,
        benchmark_symbol=row.benchmark_symbol,
    )


class SqlAlchemyResearchSummaryRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, summary: ResearchSummary) -> None:
        with self._session_factory() as session:
            session.add(
                ResearchSummaryRow(
                    id=summary.id,
                    document_id=summary.document_id,
                    model=summary.model,
                    prompt_version=summary.prompt_version,
                    schema_version=summary.schema_version,
                    metric_version=summary.metric_version,
                    facts=[f.as_dict() for f in summary.facts],
                    headline=summary.headline,
                    summary=summary.summary,
                    cited_fact_ids=list(summary.cited_fact_ids),
                    uncertainty_flags=list(summary.uncertainty_flags),
                    created_at=summary.created_at,
                )
            )
            session.commit()

    def latest_for_document(self, document_id: UUID) -> ResearchSummary | None:
        with self._session_factory() as session:
            row = session.scalars(
                select(ResearchSummaryRow)
                .where(ResearchSummaryRow.document_id == document_id)
                .order_by(ResearchSummaryRow.created_at.desc())
                .limit(1)
            ).one_or_none()
            if row is None:
                return None
            return ResearchSummary(
                id=row.id,
                document_id=row.document_id,
                model=row.model,
                prompt_version=row.prompt_version,
                schema_version=row.schema_version,
                metric_version=row.metric_version,
                facts=tuple(Fact(**f) for f in row.facts),
                headline=row.headline,
                summary=row.summary,
                cited_fact_ids=tuple(row.cited_fact_ids),
                uncertainty_flags=tuple(row.uncertainty_flags),
                created_at=row.created_at,
            )
