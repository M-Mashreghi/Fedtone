"""FastAPI application factory."""

import logging
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI, Response, status
from sqlalchemy import Engine

from fedtone import __version__
from fedtone.api.routes import Services, router
from fedtone.application.agentic_research import AgentResearchService
from fedtone.application.analogue_search import AnalogueService
from fedtone.application.historical_search import HistoricalSearchService
from fedtone.application.llm import LLMPermanentError
from fedtone.application.pipeline import (
    PipelineService,
    PipelineStage,
    StageRunner,
    StageUnavailableError,
)
from fedtone.application.research_queries import ResearchQueryService
from fedtone.application.review import ReviewService
from fedtone.infrastructure.persistence.db import (
    create_db_engine,
    create_session_factory,
    database_is_ready,
    schema_revision,
)
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyAgentNoteRepository,
    SqlAlchemyAlertRepository,
    SqlAlchemyComparisonRepository,
    SqlAlchemyEventMetricRepository,
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyProcessingRunRepository,
    SqlAlchemyResearchSummaryRepository,
    SqlAlchemyReviewItemRepository,
    SqlAlchemySourceSnapshotRepository,
    SqlAlchemyToneAnalysisRepository,
)
from fedtone.infrastructure.pipeline import (
    AdapterFactories,
    agent_service,
    default_stage_runners,
)
from fedtone.observability import configure_logging
from fedtone.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    *,
    adapter_factories: AdapterFactories | None = None,
    stage_runners: Mapping[PipelineStage, StageRunner] | None = None,
) -> FastAPI:
    """Build the API. ``adapter_factories`` / ``stage_runners`` replace the external
    adapters used by pipeline runs (tests pass fakes)."""
    settings = settings or get_settings()
    app = FastAPI(
        title="FedTone API",
        version=__version__,
        description="Central-bank communication research data: documents, tone analyses, "
        "comparisons, market reactions, exports and pipeline runs.",
    )
    app.state.engine = engine
    app.state.services_instance = None

    def get_engine() -> Engine:
        # Created on first use so importing the app has no filesystem side effects.
        if app.state.engine is None:
            app.state.engine = create_db_engine(settings.database.url)
        return app.state.engine

    def services() -> Services:
        if app.state.services_instance is None:
            app.state.services_instance = _build_services(
                settings, get_engine(), adapter_factories, stage_runners
            )
        return app.state.services_instance

    app.state.services = services
    app.include_router(router)

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        """Liveness: the process is up. Does not touch dependencies."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["system"])
    def ready(response: Response) -> dict[str, object]:
        """Readiness: the database is reachable and migrated. Optional providers are reported
        (``configured`` / ``not_configured``) but do not fail readiness; pipeline stages that
        need a missing provider answer 409 instead."""
        engine = get_engine()
        database_ok = database_is_ready(engine)
        revision = schema_revision(engine) if database_ok else None
        checks = {
            "database": "ok" if database_ok else "unavailable",
            "schema": "ok" if revision else "missing",
            **provider_checks(settings),
        }
        ok = database_ok and revision is not None
        if not ok:
            logger.warning("readiness check failed: %s", checks)
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ready" if ok else "unavailable",
            "checks": checks,
            "schema_revision": revision,
        }

    return app


def provider_checks(settings: Settings) -> dict[str, str]:
    """Configuration state of optional providers; never contacts them and never shows secrets."""
    llm = settings.llm
    llm_ok = bool(llm.api_key and llm.api_key.get_secret_value() and llm.model)
    market = settings.market_data
    if market.provider == "none":
        market_state = "not_configured"
    elif market.provider == "csv" and not Path(market.csv_directory).is_dir():
        market_state = "csv_directory_missing"
    else:
        market_state = "configured"
    return {
        "llm": "configured" if llm_ok else "not_configured",
        "market_data": market_state,
        "scheduler": "enabled" if settings.scheduler.enabled else "disabled",
    }


def _build_services(
    settings: Settings,
    engine: Engine,
    adapter_factories: AdapterFactories | None,
    stage_runners: Mapping[PipelineStage, StageRunner] | None,
) -> Services:
    session_factory = create_session_factory(engine)
    documents = SqlAlchemyPolicyDocumentRepository(session_factory)
    analyses = SqlAlchemyToneAnalysisRepository(session_factory)
    comparisons = SqlAlchemyComparisonRepository(session_factory)
    metrics = SqlAlchemyEventMetricRepository(session_factory)
    reviews = SqlAlchemyReviewItemRepository(session_factory)
    if stage_runners is None:
        stage_runners = default_stage_runners(settings, session_factory, adapter_factories)
    factories = adapter_factories or AdapterFactories()

    def agent_research() -> AgentResearchService:
        try:
            client = factories.llm_client(settings)
        except LLMPermanentError as exc:
            raise StageUnavailableError(
                f"{exc} (set FEDTONE_LLM__API_KEY and FEDTONE_LLM__MODEL)"
            ) from exc
        return agent_service(settings, session_factory, client)

    def answer_client():
        try:
            return factories.llm_client(settings)
        except LLMPermanentError as exc:
            raise StageUnavailableError(
                f"{exc} (set FEDTONE_LLM__API_KEY and FEDTONE_LLM__MODEL)"
            ) from exc

    return Services(
        queries=ResearchQueryService(
            documents=documents,
            snapshots=SqlAlchemySourceSnapshotRepository(session_factory),
            analyses=analyses,
            comparisons=comparisons,
            metrics=metrics,
            summaries=SqlAlchemyResearchSummaryRepository(session_factory),
            reviews=reviews,
            runs=SqlAlchemyProcessingRunRepository(session_factory),
        ),
        analogues=AnalogueService(
            documents=documents, analyses=analyses, comparisons=comparisons, metrics=metrics
        ),
        reviews=ReviewService(documents, reviews),
        pipeline=PipelineService(stage_runners),
        agent_notes=SqlAlchemyAgentNoteRepository(session_factory),
        agent_research=agent_research,
        search=HistoricalSearchService(
            documents=documents,
            snapshots=SqlAlchemySourceSnapshotRepository(session_factory),
            analyses=analyses,
        ),
        alerts=SqlAlchemyAlertRepository(session_factory),
        answer_client=answer_client,
        analogue_top_k=settings.market_data.analogue_top_k,
        analogue_min_sample=settings.market_data.analogue_min_sample,
    )


def create_default_app() -> FastAPI:
    """Entry point for ``uvicorn fedtone.api.app:create_default_app --factory``."""
    settings = get_settings()
    configure_logging(settings)
    return create_app(settings)
