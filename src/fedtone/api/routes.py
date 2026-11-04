"""Research API routes (``/api/v1``). Handlers are thin: they parse parameters, call an
application service and map the result to a response model."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import status as http

from fedtone import __version__
from fedtone.api.schemas import (
    AnaloguesOut,
    AnalysisOut,
    AuditOut,
    ComparisonOut,
    DocumentDetailOut,
    DocumentPageOut,
    MarketReactionOut,
    PipelineResultOut,
    ResolveIn,
    ReviewOut,
    RunOut,
    SignalRecord,
    SpeakerOut,
    TelemetryOut,
    TimelineOut,
    ToneProfileOut,
    document_detail_out,
    market_reaction_out,
)
from fedtone.application.analogue_search import AnalogueService
from fedtone.application.pipeline import PipelineService, PipelineStage, StageUnavailableError
from fedtone.application.prompts import COMPARISON_PROMPT_VERSION, STATEMENT_PROMPT_VERSION
from fedtone.application.research_export import research_note, signals_to_csv, signals_to_json
from fedtone.application.research_queries import (
    DIMENSIONS,
    DocumentFilter,
    ResearchQueryService,
    signal_record,
)
from fedtone.application.research_summary import SUMMARY_PROMPT_VERSION
from fedtone.application.review import ReviewService
from fedtone.application.tone_profiles import GENERIC, PROFILES
from fedtone.domain.diff import DIFF_VERSION
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.metrics import METRIC_VERSION
from fedtone.domain.summary import SUMMARY_SCHEMA_VERSION
from fedtone.domain.text import NORMALIZER_VERSION
from fedtone.domain.tone_schema import COMPARISON_SCHEMA_VERSION, TONE_SCHEMA_VERSION
from fedtone.observability import PROVIDER_METRICS

VERSIONS = {
    "statement_prompt": STATEMENT_PROMPT_VERSION,
    "comparison_prompt": COMPARISON_PROMPT_VERSION,
    "summary_prompt": SUMMARY_PROMPT_VERSION,
    "tone_schema": TONE_SCHEMA_VERSION,
    "comparison_schema": COMPARISON_SCHEMA_VERSION,
    "summary_schema": SUMMARY_SCHEMA_VERSION,
    "diff": DIFF_VERSION,
    "normalizer": NORMALIZER_VERSION,
    "metrics": METRIC_VERSION,
}


@dataclass
class Services:
    queries: ResearchQueryService
    analogues: AnalogueService
    reviews: ReviewService
    pipeline: PipelineService
    analogue_top_k: int = 5
    analogue_min_sample: int = 3


def get_services(request: Request) -> Services:
    services: Callable[[], Services] = request.app.state.services
    return services()


ServicesDep = Annotated[Services, Depends(get_services)]


def document_filter(
    central_bank: str | None = None,
    document_type: str | None = None,
    status: DocumentStatus | None = None,
    model: str | None = None,
    published_from: Annotated[datetime | None, Query(description="ISO time with timezone")] = None,
    published_to: Annotated[datetime | None, Query(description="ISO time with timezone")] = None,
    speaker: str | None = None,
    include_speeches: Annotated[
        bool,
        Query(description="speeches are excluded unless requested, by type, speaker or this flag"),
    ] = False,
) -> DocumentFilter:
    try:
        return DocumentFilter(
            central_bank=central_bank,
            document_type=document_type,
            status=status,
            model=model,
            published_from=published_from,
            published_to=published_to,
            speaker=speaker,
            include_speeches=include_speeches,
        )
    except ValueError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc


FilterDep = Annotated[DocumentFilter, Depends(document_filter)]

# Literal code: the constant was renamed across Starlette versions.
UNPROCESSABLE = 422

router = APIRouter(prefix="/api/v1")


def _detail(services: Services, document_id: UUID):
    try:
        return services.queries.detail(document_id)
    except LookupError as exc:
        raise HTTPException(http.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/documents", response_model=DocumentPageOut, tags=["documents"])
def list_documents(
    services: ServicesDep,
    filters: FilterDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPageOut:
    """Documents with their latest stored tone signal, oldest release first."""
    page = services.queries.list_documents(filters, limit=limit, offset=offset)
    return DocumentPageOut(
        items=[SignalRecord.model_validate(signal_record(row)) for row in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/documents/{document_id}", response_model=DocumentDetailOut, tags=["documents"])
def get_document(document_id: UUID, services: ServicesDep) -> DocumentDetailOut:
    """Source, score, evidence, diff, market reaction, summary and audit for one meeting."""
    detail = _detail(services, document_id)
    return document_detail_out(detail, services.queries.previous_analysis(detail.comparison))


@router.get("/documents/{document_id}/analysis", response_model=AnalysisOut, tags=["analysis"])
def get_analysis(document_id: UUID, services: ServicesDep) -> AnalysisOut:
    detail = _detail(services, document_id)
    if detail.analysis is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "no tone analysis stored")
    return AnalysisOut.of(detail.analysis)


@router.get(
    "/documents/{document_id}/comparison", response_model=ComparisonOut, tags=["comparisons"]
)
def get_comparison(document_id: UUID, services: ServicesDep) -> ComparisonOut:
    detail = _detail(services, document_id)
    if detail.comparison is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "no comparison stored")
    return ComparisonOut.of(
        detail.comparison, services.queries.previous_analysis(detail.comparison)
    )


@router.get(
    "/documents/{document_id}/market-reaction",
    response_model=MarketReactionOut,
    tags=["market-reactions"],
)
def get_market_reaction(document_id: UUID, services: ServicesDep) -> MarketReactionOut:
    """Stored event-window metrics; values are never recalculated here."""
    return market_reaction_out(_detail(services, document_id))


@router.get(
    "/documents/{document_id}/analogues", response_model=AnaloguesOut, tags=["market-reactions"]
)
def get_analogues(
    document_id: UUID,
    services: ServicesDep,
    top_k: Annotated[int | None, Query(ge=1, le=50)] = None,
    min_sample: Annotated[int | None, Query(ge=1)] = None,
    cutoff: Annotated[datetime | None, Query(description="backtest cut-off (ISO, tz)")] = None,
) -> AnaloguesOut:
    """Most similar earlier meetings; only data available before the cutoff is used."""
    try:
        report = services.analogues.find(
            document_id,
            top_k=top_k or services.analogue_top_k,
            min_sample=min_sample or services.analogue_min_sample,
            cutoff=cutoff,
        )
    except LookupError as exc:
        raise HTTPException(http.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    return AnaloguesOut.of(report)


@router.get("/documents/{document_id}/audit", response_model=AuditOut, tags=["documents"])
def get_audit(document_id: UUID, services: ServicesDep) -> AuditOut:
    return AuditOut.of(_detail(services, document_id))


@router.get(
    "/documents/{document_id}/research-note",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}}},
    tags=["exports"],
)
def get_research_note(document_id: UUID, services: ServicesDep) -> Response:
    """Markdown research note built from the stored records."""
    detail = _detail(services, document_id)
    note = research_note(detail, services.queries.previous_analysis(detail.comparison))
    return Response(note, media_type="text/markdown; charset=utf-8")


@router.get("/timeline", response_model=TimelineOut, tags=["analysis"])
def get_timeline(services: ServicesDep, filters: FilterDep) -> TimelineOut:
    """Every matching document's stored tone signal, for charts."""
    records = services.queries.signals(filters)
    return TimelineOut(
        dimensions=list(DIMENSIONS),
        points=[SignalRecord.model_validate(r) for r in records],
    )


@router.get(
    "/exports/signals",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}, "application/json": {}}}},
    tags=["exports"],
)
def export_signals(
    services: ServicesDep,
    filters: FilterDep,
    format: Annotated[str, Query(pattern="^(csv|json)$")] = "csv",
) -> Response:
    """The timeline records as CSV or JSON, with full numeric precision."""
    records = services.queries.signals(filters)
    if format == "json":
        return Response(signals_to_json(records), media_type="application/json")
    return Response(
        signals_to_csv(records),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="fedtone-signals.csv"'},
    )


@router.get("/runs", response_model=list[RunOut], tags=["system"])
def list_runs(
    services: ServicesDep, limit: Annotated[int, Query(ge=1, le=200)] = 20
) -> list[RunOut]:
    return [RunOut.of(run) for run in services.queries.recent_runs(limit=limit)]


@router.post("/pipeline/{stage}", response_model=PipelineResultOut, tags=["system"])
def run_pipeline_stage(
    stage: PipelineStage,
    services: ServicesDep,
    response: Response,
    central_bank: str = "FED",
    document_type: str = "fomc_statement",
) -> PipelineResultOut:
    """Run one pipeline stage now. Re-running a stage retries documents that failed on a
    provider outage; documents in review continue after ``/reviews/{id}/resolve``."""
    try:
        result = services.pipeline.run(stage, central_bank, document_type)
    except StageUnavailableError as exc:
        raise HTTPException(http.HTTP_409_CONFLICT, str(exc)) from exc
    if not result.succeeded:
        response.status_code = http.HTTP_502_BAD_GATEWAY
    return PipelineResultOut.of(result)


@router.get("/reviews", response_model=list[ReviewOut], tags=["system"])
def list_reviews(services: ServicesDep) -> list[ReviewOut]:
    return [ReviewOut.of(item) for item in services.queries.open_reviews(limit=None)]


@router.post("/reviews/{item_id}/resolve", response_model=ReviewOut, tags=["system"])
def resolve_review(item_id: UUID, body: ResolveIn, services: ServicesDep) -> ReviewOut:
    """Close a review item; ``resume`` retries the document from its last good stage."""
    try:
        item = services.reviews.resolve(item_id, body.note, resume=body.resume)
    except LookupError as exc:
        raise HTTPException(http.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(http.HTTP_409_CONFLICT, str(exc)) from exc
    return ReviewOut.of(item)


@router.get("/speakers", response_model=list[SpeakerOut], tags=["analysis"])
def list_speakers(services: ServicesDep, filters: FilterDep) -> list[SpeakerOut]:
    """Speaker-level tone history summary; use ``/timeline?speaker=...`` for the series."""
    return [SpeakerOut.model_validate(item) for item in services.queries.speakers(filters)]


@router.get("/system/tone-profiles", response_model=list[ToneProfileOut], tags=["system"])
def tone_profiles() -> list[ToneProfileOut]:
    """Bank/document-type context added to tone prompts. All profiles share one output
    schema and the -1..+1 scale; levels are calibrated within a profile, not across them."""
    items = [ToneProfileOut.of(key, profile) for key, profile in sorted(PROFILES.items())]
    return [*items, ToneProfileOut.of(None, GENERIC)]


@router.get("/system/telemetry", response_model=TelemetryOut, tags=["system"])
def telemetry(services: ServicesDep) -> TelemetryOut:
    """Document counts per status, open reviews, recent runs, active versions and
    per-provider request/error/latency counters of this process."""
    return TelemetryOut.of(
        services.queries.telemetry(), __version__, VERSIONS, PROVIDER_METRICS.snapshot()
    )
