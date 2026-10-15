"""Command-line entry point (composition root for batch commands).

    fedtone ingest                      # official FOMC statements
    fedtone ingest --refetch            # also re-download known documents to detect revisions
    fedtone reviews                     # list open review items
    fedtone resolve ITEM_ID --note "..." [--resume]   # close a review item
    fedtone analyze                     # LLM tone analysis + comparison of queued documents
    fedtone benchmark --dataset FILE    # evaluation metrics for the configured model
    fedtone documents                   # stored documents with status and tone score
    fedtone reactions [--as-of ISO]     # market reactions for compared documents
    fedtone analogues DOCUMENT_ID       # most similar earlier meetings and their reactions
    fedtone summarize                   # LLM research summary over computed facts

Run ``alembic upgrade head`` first to create or migrate the database.
"""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime, time
from pathlib import Path
from uuid import UUID

import httpx

from fedtone.application.analogue_search import AnalogueService
from fedtone.application.evaluation import load_dataset, run_benchmark
from fedtone.application.ingestion import IngestionService
from fedtone.application.llm import LLMPermanentError
from fedtone.application.market_data import MarketDataProvider
from fedtone.application.market_reaction import MarketReactionService
from fedtone.application.prompts import STATEMENT_PROMPT_VERSION
from fedtone.application.research_summary import ResearchSummaryService, metric_display
from fedtone.application.review import ReviewService
from fedtone.application.sources import SourceRegistry, UnsupportedSourceError
from fedtone.application.tone_analysis import AnalysisService, ToneAnalyzer
from fedtone.domain.market import Instrument, ValueKind
from fedtone.domain.metrics import WindowSpec
from fedtone.domain.models import RunStatus, RunTrigger
from fedtone.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from fedtone.infrastructure.market.fred import FRED_CSV_URL, CsvFileProvider, FredCsvProvider
from fedtone.infrastructure.persistence.db import create_db_engine, create_session_factory
from fedtone.infrastructure.persistence.repositories import (
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
from fedtone.infrastructure.sources.fomc import FomcStatementAdapter
from fedtone.infrastructure.sources.http import HttpFetcher
from fedtone.observability import configure_logging
from fedtone.settings import Settings, get_settings

logger = logging.getLogger("fedtone.cli")


def build_registry(settings: Settings, client: httpx.Client) -> SourceRegistry:
    fetcher = HttpFetcher(
        client,
        max_retries=settings.sources.max_retries,
        backoff_seconds=settings.sources.backoff_seconds,
    )
    return SourceRegistry(
        [FomcStatementAdapter(fetcher, listing_urls=settings.sources.fomc_listing_urls)]
    )


def _ingest(args: argparse.Namespace, settings: Settings) -> int:
    session_factory = create_session_factory(create_db_engine(settings.database.url))
    headers = {"User-Agent": settings.sources.user_agent}
    with httpx.Client(
        headers=headers, timeout=settings.sources.timeout_seconds, follow_redirects=True
    ) as client:
        service = IngestionService(
            registry=build_registry(settings, client),
            documents=SqlAlchemyPolicyDocumentRepository(session_factory),
            snapshots=SqlAlchemySourceSnapshotRepository(session_factory),
            reviews=SqlAlchemyReviewItemRepository(session_factory),
            runs=SqlAlchemyProcessingRunRepository(session_factory),
        )
        try:
            report = service.run(
                args.bank,
                args.type,
                trigger=RunTrigger(args.trigger),
                refetch_existing=args.refetch,
            )
        except UnsupportedSourceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    summary = ", ".join(f"{name}={value}" for name, value in report.outcomes.items())
    print(
        f"run {report.run.id}: {report.run.status} "
        f"(discovered={report.discovered}, {summary}, relinked={report.relinked})"
    )
    if report.run.error:
        print(f"note: {report.run.error}")
    return 1 if report.run.status is RunStatus.FAILED else 0


def _reviews(_args: argparse.Namespace, settings: Settings) -> int:
    session_factory = create_session_factory(create_db_engine(settings.database.url))
    items = SqlAlchemyReviewItemRepository(session_factory).list_open(limit=None)
    for item in items:
        print(f"{item.id} {item.created_at:%Y-%m-%d %H:%M} [{item.stage}] {item.reason}")
    print(f"{len(items)} open review item(s)")
    return 0


def build_llm_client(settings: Settings) -> OpenAICompatibleClient:
    llm = settings.llm
    return OpenAICompatibleClient(
        api_key=llm.api_key.get_secret_value() if llm.api_key else "",
        model=llm.model or "",
        base_url=llm.base_url,
        timeout_seconds=llm.timeout_seconds,
        max_retries=llm.max_retries,
        backoff_seconds=llm.backoff_seconds,
    )


def _analyze(args: argparse.Namespace, settings: Settings) -> int:
    try:
        client = build_llm_client(settings)
    except LLMPermanentError as exc:
        print(f"error: {exc} (set FEDTONE_LLM__API_KEY and FEDTONE_LLM__MODEL)", file=sys.stderr)
        return 2
    session_factory = create_session_factory(create_db_engine(settings.database.url))
    report = AnalysisService(
        analyzer=ToneAnalyzer(client, temperature=settings.llm.temperature),
        documents=SqlAlchemyPolicyDocumentRepository(session_factory),
        snapshots=SqlAlchemySourceSnapshotRepository(session_factory),
        analyses=SqlAlchemyToneAnalysisRepository(session_factory),
        comparisons=SqlAlchemyComparisonRepository(session_factory),
        reviews=SqlAlchemyReviewItemRepository(session_factory),
        min_confidence=settings.llm.min_confidence,
    ).run(args.bank, args.type)
    print(", ".join(f"{name}={value}" for name, value in report.outcomes.items()))
    if report.aborted:
        print(f"error: analysis aborted: {report.aborted}", file=sys.stderr)
        return 1
    return 0


def _resolve(args: argparse.Namespace, settings: Settings) -> int:
    session_factory = create_session_factory(create_db_engine(settings.database.url))
    service = ReviewService(
        SqlAlchemyPolicyDocumentRepository(session_factory),
        SqlAlchemyReviewItemRepository(session_factory),
    )
    try:
        service.resolve(UUID(args.item_id), args.note, resume=args.resume)
    except (LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("resolved" + (" and resumed" if args.resume else " and rejected document"))
    return 0


def _benchmark(args: argparse.Namespace, settings: Settings) -> int:
    try:
        client = build_llm_client(settings)
    except LLMPermanentError as exc:
        print(f"error: {exc} (set FEDTONE_LLM__API_KEY and FEDTONE_LLM__MODEL)", file=sys.stderr)
        return 2
    report = run_benchmark(
        ToneAnalyzer(client, temperature=settings.llm.temperature),
        load_dataset(Path(args.dataset)),
        model=client.model,
        prompt_version=STATEMENT_PROMPT_VERSION,
        repeats=args.repeats,
    )
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "benchmark.json").write_text(json.dumps(report.to_dict(), indent=2), "utf-8")
    (output / "benchmark.md").write_text(report.to_markdown(), "utf-8")
    print(report.to_markdown())
    return 0


def _documents(args: argparse.Namespace, settings: Settings) -> int:
    session_factory = create_session_factory(create_db_engine(settings.database.url))
    analyses = SqlAlchemyToneAnalysisRepository(session_factory)
    documents = SqlAlchemyPolicyDocumentRepository(session_factory).list(
        central_bank=args.bank, document_type=args.type, limit=None
    )
    for doc in documents:
        analysis = analyses.latest_for_document(doc.id)
        released = f"{doc.published_at:%Y-%m-%d %H:%M}Z" if doc.published_at else "unknown"
        tone = f"{analysis.net_tone_score:+.2f}" if analysis else "-"
        print(f"{doc.id} {released} {doc.status} tone={tone}")
    print(f"{len(documents)} document(s)")
    return 0


def build_instruments(settings: Settings) -> list[Instrument]:
    return [
        Instrument(
            symbol=i.symbol,
            name=i.name,
            kind=ValueKind(i.kind),
            calendar=i.calendar,
            observation_time=time.fromisoformat(i.observation_time),
        )
        for i in settings.market_data.instruments
    ]


def build_market_provider(settings: Settings, client: httpx.Client) -> MarketDataProvider | None:
    market = settings.market_data
    if market.provider == "fred":
        fetcher = HttpFetcher(
            client, max_retries=market.max_retries, backoff_seconds=market.backoff_seconds
        )
        return FredCsvProvider(fetcher, base_url=market.base_url or FRED_CSV_URL)
    if market.provider == "csv":
        return CsvFileProvider(Path(market.csv_directory))
    return None


def _reactions(args: argparse.Namespace, settings: Settings) -> int:
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    if as_of is not None and as_of.utcoffset() is None:
        print("error: --as-of needs a timezone, e.g. 2024-03-01T00:00:00+00:00", file=sys.stderr)
        return 2
    session_factory = create_session_factory(create_db_engine(settings.database.url))
    headers = {"User-Agent": settings.sources.user_agent}
    with httpx.Client(
        headers=headers, timeout=settings.market_data.timeout_seconds, follow_redirects=True
    ) as client:
        provider = build_market_provider(settings, client)
        if provider is None:
            print(
                "error: no market-data provider configured "
                "(set FEDTONE_MARKET_DATA__PROVIDER to fred or csv)",
                file=sys.stderr,
            )
            return 2
        try:
            windows = [WindowSpec.parse(w) for w in settings.market_data.windows]
            service = MarketReactionService(
                provider=provider,
                documents=SqlAlchemyPolicyDocumentRepository(session_factory),
                observations=SqlAlchemyMarketObservationRepository(session_factory),
                metrics=SqlAlchemyEventMetricRepository(session_factory),
                instruments=build_instruments(settings),
                windows=windows,
                benchmark_symbol=settings.market_data.benchmark_symbol,
            )
        except ValueError as exc:
            print(f"error: market-data configuration: {exc}", file=sys.stderr)
            return 2
        report = service.run(args.bank, args.type, as_of=as_of)
    print(", ".join(f"{name}={value}" for name, value in report.outcomes.items()))
    for symbol, reason in report.unavailable_instruments.items():
        print(f"note: {symbol} unavailable: {reason}")
    if report.aborted:
        print(f"error: market reaction aborted: {report.aborted}", file=sys.stderr)
        return 1
    return 0


def _analogues(args: argparse.Namespace, settings: Settings) -> int:
    session_factory = create_session_factory(create_db_engine(settings.database.url))
    service = AnalogueService(
        documents=SqlAlchemyPolicyDocumentRepository(session_factory),
        analyses=SqlAlchemyToneAnalysisRepository(session_factory),
        comparisons=SqlAlchemyComparisonRepository(session_factory),
        metrics=SqlAlchemyEventMetricRepository(session_factory),
    )
    try:
        report = service.find(
            UUID(args.document_id),
            top_k=args.top_k or settings.market_data.analogue_top_k,
            min_sample=args.min_sample or settings.market_data.analogue_min_sample,
        )
    except (LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = report.result
    print(f"cutoff {result.cutoff:%Y-%m-%d %H:%M}Z, {result.sample_size} earlier meeting(s)")
    if result.insufficient_history:
        print(f"note: fewer than {result.min_sample} earlier meetings; treat ranking as anecdotal")
    for rank, match in enumerate(result.matches, start=1):
        released = f"{match.published_at:%Y-%m-%d}"
        print(f"{rank}. {released} distance={match.distance:.3f} {match.document_id}")
        for metric in report.reactions[match.document_id]:
            print(f"     {metric.symbol} {metric.kind} {metric.window}: {metric_display(metric)}")
    if report.withheld_after_cutoff:
        print(f"note: {report.withheld_after_cutoff} metric(s) withheld (ended after cutoff)")
    if report.excluded_other_versions:
        print(f"note: {report.excluded_other_versions} meeting(s) analyzed by another model/prompt")
    return 0


def _summarize(args: argparse.Namespace, settings: Settings) -> int:
    try:
        client = build_llm_client(settings)
    except LLMPermanentError as exc:
        print(f"error: {exc} (set FEDTONE_LLM__API_KEY and FEDTONE_LLM__MODEL)", file=sys.stderr)
        return 2
    session_factory = create_session_factory(create_db_engine(settings.database.url))
    report = ResearchSummaryService(
        client=client,
        documents=SqlAlchemyPolicyDocumentRepository(session_factory),
        analyses=SqlAlchemyToneAnalysisRepository(session_factory),
        comparisons=SqlAlchemyComparisonRepository(session_factory),
        metrics=SqlAlchemyEventMetricRepository(session_factory),
        summaries=SqlAlchemyResearchSummaryRepository(session_factory),
        reviews=SqlAlchemyReviewItemRepository(session_factory),
        instrument_names={i.symbol: i.name for i in settings.market_data.instruments},
        temperature=settings.llm.temperature,
    ).run(args.bank, args.type)
    print(", ".join(f"{name}={value}" for name, value in report.outcomes.items()))
    if report.aborted:
        print(f"error: summary aborted: {report.aborted}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fedtone")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="discover, fetch, parse and link documents")
    ingest.add_argument("--bank", default="FED")
    ingest.add_argument("--type", default="fomc_statement")
    ingest.add_argument("--refetch", action="store_true", help="re-check known documents")
    ingest.add_argument(
        "--trigger", choices=[t.value for t in RunTrigger], default=RunTrigger.MANUAL.value
    )
    ingest.set_defaults(handler=_ingest)

    reviews = commands.add_parser("reviews", help="list open review items")
    reviews.set_defaults(handler=_reviews)

    resolve = commands.add_parser("resolve", help="resolve a review item")
    resolve.add_argument("item_id")
    resolve.add_argument("--note", required=True, help="what the reviewer decided and why")
    resolve.add_argument(
        "--resume", action="store_true", help="continue processing (default: reject document)"
    )
    resolve.set_defaults(handler=_resolve)

    analyze = commands.add_parser("analyze", help="LLM tone analysis and comparison")
    analyze.add_argument("--bank", default="FED")
    analyze.add_argument("--type", default="fomc_statement")
    analyze.set_defaults(handler=_analyze)

    benchmark = commands.add_parser("benchmark", help="evaluate the configured model")
    benchmark.add_argument("--dataset", required=True)
    benchmark.add_argument("--repeats", type=int, default=3)
    benchmark.add_argument("--output", default="reports/benchmark")
    benchmark.set_defaults(handler=_benchmark)

    for name, handler, text in (
        ("documents", _documents, "list stored documents"),
        ("reactions", _reactions, "market reactions for compared documents"),
        ("summarize", _summarize, "LLM research summaries for market-linked documents"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--bank", default="FED")
        sub.add_argument("--type", default="fomc_statement")
        sub.set_defaults(handler=handler)
        if name == "reactions":
            sub.add_argument("--as-of", help="only use data available at this ISO time")

    analogues = commands.add_parser("analogues", help="similar earlier meetings")
    analogues.add_argument("document_id")
    analogues.add_argument("--top-k", type=int)
    analogues.add_argument("--min-sample", type=int)
    analogues.set_defaults(handler=_analogues)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    return args.handler(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
