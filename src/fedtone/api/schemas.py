"""Response models of the research API (they also document the OpenAPI contract).

Mapping functions only copy stored fields; any number shown here comes from a canonical
record or from the shared ``signal_record`` / ``metric_record`` helpers.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from fedtone.application.analogue_search import AnalogueReport
from fedtone.application.pipeline import PipelineResult
from fedtone.application.research_queries import (
    DocumentDetail,
    SystemTelemetry,
    metric_record,
    metric_sort_key,
    signal_record,
)
from fedtone.domain.models import (
    DocumentComparison,
    ProcessingRun,
    ReviewItem,
    ToneAnalysis,
)
from fedtone.domain.summary import ResearchSummary


class SignalRecord(BaseModel):
    document_id: UUID
    central_bank: str
    document_type: str
    published_at: datetime | None
    status: str
    needs_review: bool
    net_tone_score: float | None
    classification: str | None
    confidence: float | None
    overall_delta: float | None
    dim_inflation: float | None
    dim_labor: float | None
    dim_growth: float | None
    dim_policy_guidance: float | None
    dim_balance_sheet: float | None
    evidence_count: int
    uncertainty_flags: str
    analysis_id: UUID | None
    model: str | None
    prompt_version: str | None
    schema_version: str | None
    analyzed_at: datetime | None
    content_hash: str | None
    source_url: str


class DocumentPageOut(BaseModel):
    items: list[SignalRecord]
    total: int
    limit: int
    offset: int


class TimelineOut(BaseModel):
    dimensions: list[str]
    points: list[SignalRecord]


class EvidenceOut(BaseModel):
    text: str
    dimension: str | None
    direction: str | None
    match: str | None


class AnalysisOut(BaseModel):
    id: UUID
    document_id: UUID
    net_tone_score: float
    classification: str
    confidence: float
    dimension_scores: dict[str, float | None]
    evidence: list[EvidenceOut]
    explanation: str
    uncertainty_flags: list[str]
    model: str
    prompt_version: str
    schema_version: str
    temperature: float | None
    document_hash: str
    created_at: datetime

    @classmethod
    def of(cls, analysis: ToneAnalysis) -> "AnalysisOut":
        return cls(
            id=analysis.id,
            document_id=analysis.document_id,
            net_tone_score=analysis.net_tone_score,
            classification=analysis.classification.value,
            confidence=analysis.confidence,
            dimension_scores=dict(analysis.dimension_scores),
            evidence=[
                EvidenceOut(
                    text=e.text, dimension=e.dimension, direction=e.direction, match=e.match
                )
                for e in analysis.evidence
            ],
            explanation=analysis.explanation,
            uncertainty_flags=list(analysis.uncertainty_flags),
            model=analysis.model,
            prompt_version=analysis.prompt_version,
            schema_version=analysis.schema_version,
            temperature=analysis.temperature,
            document_hash=analysis.document_hash,
            created_at=analysis.created_at,
        )


class ComparisonOut(BaseModel):
    id: UUID
    document_id: UUID
    is_baseline: bool
    previous_document_id: UUID | None
    previous_analysis_id: UUID | None
    previous_net_tone_score: float | None
    overall_delta: float | None
    dimension_deltas: dict[str, float | None]
    diff: dict[str, Any] | None
    interpretation: dict[str, Any] | None
    model: str | None
    prompt_version: str | None
    schema_version: str | None
    uncertainty_flags: list[str]
    created_at: datetime

    @classmethod
    def of(cls, comparison: DocumentComparison, previous: ToneAnalysis | None) -> "ComparisonOut":
        return cls(
            id=comparison.id,
            document_id=comparison.document_id,
            is_baseline=comparison.is_baseline,
            previous_document_id=comparison.previous_document_id,
            previous_analysis_id=comparison.previous_analysis_id,
            previous_net_tone_score=previous.net_tone_score if previous else None,
            overall_delta=comparison.overall_delta,
            dimension_deltas=dict(comparison.dimension_deltas),
            diff=comparison.diff,
            interpretation=comparison.interpretation,
            model=comparison.model,
            prompt_version=comparison.prompt_version,
            schema_version=comparison.schema_version,
            uncertainty_flags=list(comparison.uncertainty_flags),
            created_at=comparison.created_at,
        )


class MetricOut(BaseModel):
    symbol: str
    window: str
    kind: str
    unit: str
    value: float | None
    display: str
    missing_reason: str | None
    benchmark_symbol: str | None
    event_day: str
    start_date: str
    end_date: str
    start_value: float | None
    end_value: float | None
    alignment_rule: str
    provider: str
    as_of: datetime
    computed_at: datetime
    end_observed_at: datetime
    metric_version: str
    backtest_mode: bool


class MarketReactionOut(BaseModel):
    document_id: UUID
    metrics: list[MetricOut]
    missing: int
    backtest_mode: bool


class SummaryOut(BaseModel):
    headline: str
    summary: str
    cited_fact_ids: list[str]
    uncertainty_flags: list[str]
    model: str
    prompt_version: str
    schema_version: str
    metric_version: str
    created_at: datetime

    @classmethod
    def of(cls, summary: ResearchSummary) -> "SummaryOut":
        return cls(
            headline=summary.headline,
            summary=summary.summary,
            cited_fact_ids=list(summary.cited_fact_ids),
            uncertainty_flags=list(summary.uncertainty_flags),
            model=summary.model,
            prompt_version=summary.prompt_version,
            schema_version=summary.schema_version,
            metric_version=summary.metric_version,
            created_at=summary.created_at,
        )


class ReviewOut(BaseModel):
    id: UUID
    document_id: UUID
    stage: str
    reason: str
    created_at: datetime
    resolved_at: datetime | None
    resolution: str | None

    @classmethod
    def of(cls, item: ReviewItem) -> "ReviewOut":
        return cls(
            id=item.id,
            document_id=item.document_id,
            stage=item.stage.value,
            reason=item.reason,
            created_at=item.created_at,
            resolved_at=item.resolved_at,
            resolution=item.resolution,
        )


class StatusChangeOut(BaseModel):
    from_status: str
    to_status: str
    at: datetime
    reason: str | None


class AuditOut(BaseModel):
    """Everything needed to reproduce or inspect a published analysis."""

    document_id: UUID
    central_bank: str
    document_type: str
    source_url: str
    published_at: datetime | None
    fetched_at: datetime | None
    content_hash: str | None
    parser_version: str | None
    snapshot_version: int | None
    raw_hash: str | None
    normalizer_version: str | None
    status: str
    failure_reason: str | None
    analysis_id: UUID | None
    model: str | None
    prompt_version: str | None
    schema_version: str | None
    temperature: float | None
    analyzed_at: datetime | None
    analysis_document_hash: str | None
    comparison_model: str | None
    comparison_prompt_version: str | None
    comparison_schema_version: str | None
    metric_version: str | None
    summary_model: str | None
    summary_prompt_version: str | None
    history: list[StatusChangeOut]

    @classmethod
    def of(cls, detail: DocumentDetail) -> "AuditOut":
        doc, analysis, comparison = detail.document, detail.analysis, detail.comparison
        return cls(
            document_id=doc.id,
            central_bank=doc.central_bank,
            document_type=doc.document_type,
            source_url=doc.source_url,
            published_at=doc.published_at,
            fetched_at=doc.fetched_at,
            content_hash=doc.content_hash,
            parser_version=doc.parser_version,
            snapshot_version=detail.snapshot.version if detail.snapshot else None,
            raw_hash=detail.snapshot.raw_hash if detail.snapshot else None,
            normalizer_version=detail.text.normalizer_version if detail.text else None,
            status=doc.status.value,
            failure_reason=doc.failure_reason,
            analysis_id=analysis.id if analysis else None,
            model=analysis.model if analysis else None,
            prompt_version=analysis.prompt_version if analysis else None,
            schema_version=analysis.schema_version if analysis else None,
            temperature=analysis.temperature if analysis else None,
            analyzed_at=analysis.created_at if analysis else None,
            analysis_document_hash=analysis.document_hash if analysis else None,
            comparison_model=comparison.model if comparison else None,
            comparison_prompt_version=comparison.prompt_version if comparison else None,
            comparison_schema_version=comparison.schema_version if comparison else None,
            metric_version=detail.metrics[0].metric_version if detail.metrics else None,
            summary_model=detail.summary.model if detail.summary else None,
            summary_prompt_version=detail.summary.prompt_version if detail.summary else None,
            history=[
                StatusChangeOut(
                    from_status=h.from_status.value,
                    to_status=h.to_status.value,
                    at=h.at,
                    reason=h.reason,
                )
                for h in doc.history
            ],
        )


class DocumentDetailOut(BaseModel):
    signal: SignalRecord
    title: str | None
    paragraphs: list[str]
    analysis: AnalysisOut | None
    comparison: ComparisonOut | None
    market_reaction: MarketReactionOut
    summary: SummaryOut | None
    open_reviews: list[ReviewOut]
    audit: AuditOut


def market_reaction_out(detail: DocumentDetail) -> MarketReactionOut:
    metrics = [MetricOut.model_validate(metric_record(m)) for m in detail.metrics]
    return MarketReactionOut(
        document_id=detail.document.id,
        metrics=metrics,
        missing=sum(1 for m in metrics if m.value is None),
        backtest_mode=any(m.backtest_mode for m in metrics),
    )


def document_detail_out(detail: DocumentDetail, previous: ToneAnalysis | None) -> DocumentDetailOut:
    return DocumentDetailOut(
        signal=SignalRecord.model_validate(signal_record(detail.row)),
        title=detail.text.title if detail.text else None,
        paragraphs=list(detail.text.paragraphs) if detail.text else [],
        analysis=AnalysisOut.of(detail.analysis) if detail.analysis else None,
        comparison=ComparisonOut.of(detail.comparison, previous) if detail.comparison else None,
        market_reaction=market_reaction_out(detail),
        summary=SummaryOut.of(detail.summary) if detail.summary else None,
        open_reviews=[ReviewOut.of(r) for r in detail.reviews],
        audit=AuditOut.of(detail),
    )


class AnalogueOut(BaseModel):
    document_id: UUID
    published_at: datetime
    distance: float
    shared_features: list[str]
    reactions: list[MetricOut]


class AnaloguesOut(BaseModel):
    target_id: UUID
    cutoff: datetime
    sample_size: int
    min_sample: int
    insufficient_history: bool
    withheld_after_cutoff: int
    excluded_other_versions: int
    matches: list[AnalogueOut]

    @classmethod
    def of(cls, report: AnalogueReport) -> "AnaloguesOut":
        result = report.result
        return cls(
            target_id=result.target_id,
            cutoff=result.cutoff,
            sample_size=result.sample_size,
            min_sample=result.min_sample,
            insufficient_history=result.insufficient_history,
            withheld_after_cutoff=report.withheld_after_cutoff,
            excluded_other_versions=report.excluded_other_versions,
            matches=[
                AnalogueOut(
                    document_id=m.document_id,
                    published_at=m.published_at,
                    distance=m.distance,
                    shared_features=list(m.shared_features),
                    reactions=[
                        MetricOut.model_validate(metric_record(r))
                        for r in sorted(report.reactions[m.document_id], key=metric_sort_key)
                    ],
                )
                for m in result.matches
            ],
        )


class RunOut(BaseModel):
    id: UUID
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None

    @classmethod
    def of(cls, run: ProcessingRun) -> "RunOut":
        return cls(
            id=run.id,
            trigger=run.trigger.value,
            status=run.status.value,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error=run.error,
        )


class PipelineResultOut(BaseModel):
    stage: str
    outcomes: dict[str, int]
    aborted: str | None
    run_id: UUID | None
    notes: list[str]

    @classmethod
    def of(cls, result: PipelineResult) -> "PipelineResultOut":
        return cls(
            stage=result.stage.value,
            outcomes=result.outcomes,
            aborted=result.aborted,
            run_id=result.run_id,
            notes=result.notes,
        )


class TelemetryOut(BaseModel):
    version: str
    documents_by_status: dict[str, int]
    open_reviews: int
    recent_runs: list[RunOut]
    versions: dict[str, str]
    providers: dict[str, dict[str, Any]]

    @classmethod
    def of(
        cls,
        telemetry: SystemTelemetry,
        version: str,
        versions: dict[str, str],
        providers: dict[str, dict[str, Any]],
    ):
        return cls(
            version=version,
            documents_by_status=telemetry.documents_by_status,
            open_reviews=telemetry.open_reviews,
            recent_runs=[RunOut.of(r) for r in telemetry.recent_runs],
            versions=versions,
            providers=providers,
        )


class ResolveIn(BaseModel):
    note: str
    resume: bool = False
