"""Default pipeline stage runners, shared by the API, the CLI and the scheduler.

Each runner wires the same application service and adapters as the matching CLI command.
The factories are parameters so tests can substitute fake LLM, market and source adapters.
"""

from collections.abc import Callable
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session, sessionmaker

from fedtone.application.agentic_research import AgentResearchService
from fedtone.application.alerting import AlertNotifier, AlertService
from fedtone.application.ingestion import IngestionService
from fedtone.application.llm import LLMClient, LLMPermanentError
from fedtone.application.market_data import MarketDataProvider
from fedtone.application.market_reaction import MarketReactionService
from fedtone.application.pipeline import (
    PipelineResult,
    PipelineStage,
    StageRunner,
    StageUnavailableError,
)
from fedtone.application.publication import PublicationService
from fedtone.application.research_summary import ResearchSummaryService
from fedtone.application.sources import SourceRegistry, UnsupportedSourceError
from fedtone.application.tone_analysis import AnalysisService, ToneAnalyzer
from fedtone.domain.alerts import AlertConfig
from fedtone.domain.metrics import WindowSpec
from fedtone.domain.models import RunStatus, RunTrigger
from fedtone.infrastructure.alerts import WebhookNotifier
from fedtone.infrastructure.factories import (
    build_instruments,
    build_llm_client,
    build_market_provider,
    build_registry,
)
from fedtone.infrastructure.persistence.repositories import (
    SqlAlchemyAgentNoteRepository,
    SqlAlchemyAlertRepository,
    SqlAlchemyComparisonRepository,
    SqlAlchemyEventMetricRepository,
    SqlAlchemyMarketObservationRepository,
    SqlAlchemyPolicyDocumentRepository,
    SqlAlchemyProcessingRunRepository,
    SqlAlchemyResearchSummaryRepository,
    SqlAlchemyReviewItemRepository,
    SqlAlchemySourceSnapshotRepository,
    SqlAlchemyToneAnalysisRepository,
)
from fedtone.settings import Settings


def _http_client(settings: Settings, timeout: float) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.sources.user_agent},
        timeout=timeout,
        follow_redirects=True,
    )


@dataclass
class AdapterFactories:
    registry: Callable[[Settings, httpx.Client], SourceRegistry] = build_registry
    llm_client: Callable[[Settings], LLMClient] = build_llm_client
    market_provider: Callable[[Settings, httpx.Client], MarketDataProvider | None] = (
        build_market_provider
    )
    alert_webhook: Callable[[httpx.Client, str], AlertNotifier] = WebhookNotifier


def alert_config(settings: Settings) -> AlertConfig:
    config = settings.alerts
    return AlertConfig(
        min_abs_overall_delta=config.min_abs_overall_delta,
        classification_change=config.classification_change,
        min_abs_dimension_delta=config.min_abs_dimension_delta,
        min_confidence=config.min_confidence,
    )


def agent_service(
    settings: Settings, session_factory: sessionmaker[Session], client: LLMClient
) -> AgentResearchService:
    return AgentResearchService(
        client=client,
        documents=SqlAlchemyPolicyDocumentRepository(session_factory),
        analyses=SqlAlchemyToneAnalysisRepository(session_factory),
        comparisons=SqlAlchemyComparisonRepository(session_factory),
        metrics=SqlAlchemyEventMetricRepository(session_factory),
        notes=SqlAlchemyAgentNoteRepository(session_factory),
        reviews=SqlAlchemyReviewItemRepository(session_factory),
        instrument_names={i.symbol: i.name for i in settings.market_data.instruments},
        temperature=settings.llm.temperature,
    )


def default_stage_runners(
    settings: Settings,
    session_factory: sessionmaker[Session],
    factories: AdapterFactories | None = None,
) -> dict[PipelineStage, StageRunner]:
    factories = factories or AdapterFactories()
    documents = SqlAlchemyPolicyDocumentRepository(session_factory)
    snapshots = SqlAlchemySourceSnapshotRepository(session_factory)
    reviews = SqlAlchemyReviewItemRepository(session_factory)
    analyses = SqlAlchemyToneAnalysisRepository(session_factory)
    comparisons = SqlAlchemyComparisonRepository(session_factory)
    metrics = SqlAlchemyEventMetricRepository(session_factory)

    def llm() -> LLMClient:
        try:
            return factories.llm_client(settings)
        except LLMPermanentError as exc:
            raise StageUnavailableError(
                f"{exc} (set FEDTONE_LLM__API_KEY and FEDTONE_LLM__MODEL)"
            ) from exc

    def ingest(bank: str, doc_type: str, trigger: RunTrigger) -> PipelineResult:
        with _http_client(settings, settings.sources.timeout_seconds) as client:
            service = IngestionService(
                registry=factories.registry(settings, client),
                documents=documents,
                snapshots=snapshots,
                reviews=reviews,
                runs=SqlAlchemyProcessingRunRepository(session_factory),
            )
            try:
                report = service.run(bank, doc_type, trigger=trigger)
            except UnsupportedSourceError as exc:
                raise StageUnavailableError(str(exc)) from exc
        outcomes = {k.value: v for k, v in report.outcomes.items()}
        outcomes |= {"discovered": report.discovered, "relinked": report.relinked}
        notes = [report.run.error] if report.run.error else []
        failed = report.run.status is RunStatus.FAILED
        return PipelineResult(
            PipelineStage.INGEST,
            outcomes,
            aborted=report.run.error if failed else None,
            run_id=report.run.id,
            notes=notes,
        )

    def analyze(bank: str, doc_type: str, trigger: RunTrigger) -> PipelineResult:
        report = AnalysisService(
            analyzer=ToneAnalyzer(llm(), temperature=settings.llm.temperature),
            documents=documents,
            snapshots=snapshots,
            analyses=analyses,
            comparisons=comparisons,
            reviews=reviews,
            min_confidence=settings.llm.min_confidence,
        ).run(bank, doc_type)
        outcomes = {k.value: v for k, v in report.outcomes.items()}
        return PipelineResult(PipelineStage.ANALYZE, outcomes, aborted=report.aborted)

    def reactions(bank: str, doc_type: str, trigger: RunTrigger) -> PipelineResult:
        market = settings.market_data
        with _http_client(settings, market.timeout_seconds) as client:
            provider = factories.market_provider(settings, client)
            if provider is None:
                raise StageUnavailableError(
                    "no market-data provider configured "
                    "(set FEDTONE_MARKET_DATA__PROVIDER to fred or csv)"
                )
            try:
                service = MarketReactionService(
                    provider=provider,
                    documents=documents,
                    observations=SqlAlchemyMarketObservationRepository(session_factory),
                    metrics=metrics,
                    instruments=build_instruments(settings),
                    windows=[WindowSpec.parse(w) for w in market.windows],
                    benchmark_symbol=market.benchmark_symbol,
                )
            except ValueError as exc:
                raise StageUnavailableError(f"market-data configuration: {exc}") from exc
            report = service.run(bank, doc_type)
        outcomes = {k.value: v for k, v in report.outcomes.items()}
        notes = [f"{s} unavailable: {r}" for s, r in report.unavailable_instruments.items()]
        return PipelineResult(
            PipelineStage.REACTIONS, outcomes, aborted=report.aborted, notes=notes
        )

    def summarize(bank: str, doc_type: str, trigger: RunTrigger) -> PipelineResult:
        report = ResearchSummaryService(
            client=llm(),
            documents=documents,
            analyses=analyses,
            comparisons=comparisons,
            metrics=metrics,
            summaries=SqlAlchemyResearchSummaryRepository(session_factory),
            reviews=reviews,
            instrument_names={i.symbol: i.name for i in settings.market_data.instruments},
            temperature=settings.llm.temperature,
        ).run(bank, doc_type)
        outcomes = {k.value: v for k, v in report.outcomes.items()}
        return PipelineResult(PipelineStage.SUMMARIZE, outcomes, aborted=report.aborted)

    def alerts(bank: str, doc_type: str, trigger: RunTrigger) -> PipelineResult:
        config = settings.alerts
        if not config.enabled:
            raise StageUnavailableError("alerts are disabled (FEDTONE_ALERTS__ENABLED)")
        notifiers: list[AlertNotifier] = [AlertNotifier()]
        with _http_client(settings, config.timeout_seconds) as client:
            if config.webhook_url is not None and config.webhook_url.get_secret_value():
                notifiers.append(
                    factories.alert_webhook(client, config.webhook_url.get_secret_value())
                )
            report = AlertService(
                config=alert_config(settings),
                documents=documents,
                analyses=analyses,
                comparisons=comparisons,
                alerts=SqlAlchemyAlertRepository(session_factory),
                notifiers=notifiers,
            ).run(bank, doc_type)
        outcomes = {"evaluated": report.evaluated, "fired": len(report.fired)}
        notes = [f"{report.notify_errors} notification(s) failed"] if report.notify_errors else []
        return PipelineResult(PipelineStage.ALERTS, outcomes, notes=notes)

    def agent_notes(bank: str, doc_type: str, trigger: RunTrigger) -> PipelineResult:
        report = agent_service(settings, session_factory, llm()).run(bank, doc_type)
        outcomes = {k.value: v for k, v in report.outcomes.items()}
        return PipelineResult(PipelineStage.AGENT_NOTES, outcomes)

    def publish(bank: str, doc_type: str, trigger: RunTrigger) -> PipelineResult:
        report = PublicationService(
            documents=documents,
            analyses=analyses,
            summaries=SqlAlchemyResearchSummaryRepository(session_factory),
            reviews=reviews,
        ).run(bank, doc_type)
        outcomes = {k.value: v for k, v in report.outcomes.items()}
        return PipelineResult(PipelineStage.PUBLISH, outcomes)

    return {
        PipelineStage.INGEST: ingest,
        PipelineStage.ANALYZE: analyze,
        PipelineStage.REACTIONS: reactions,
        PipelineStage.SUMMARIZE: summarize,
        PipelineStage.PUBLISH: publish,
        PipelineStage.ALERTS: alerts,
        PipelineStage.AGENT_NOTES: agent_notes,
    }
