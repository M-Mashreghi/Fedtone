"""Links compared documents to deterministic market reactions.

    Compared ─ every window closed ─→ metrics stored → MarketLinked
             ├ a window still open (recent meeting) → stays Compared, retried next run
             └ provider unavailable for an instrument → stays Compared, retried next run

Missing observations do not block linking: they are stored as metrics with a missing
reason, never filled. Observations are stored as fetched so every metric can be reproduced.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

from fedtone.application.market_data import MarketDataProvider
from fedtone.application.ports import (
    EventMetricRepository,
    MarketObservationRepository,
    PolicyDocumentRepository,
)
from fedtone.domain.calendar import NEW_YORK, align_event, calendar
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.market import (
    Instrument,
    PermanentMarketDataError,
    TransientMarketDataError,
    ValueKind,
)
from fedtone.domain.metrics import (
    EventMetric,
    MissingReason,
    WindowSpec,
    benchmark_adjusted,
    compute_event_metrics,
    window_dates,
)
from fedtone.domain.models import PolicyDocument, transition
from fedtone.observability import correlation_context

logger = logging.getLogger(__name__)


class ReactionOutcome(StrEnum):
    LINKED = "linked"
    WINDOW_OPEN = "window_open"
    DATA_UNAVAILABLE = "data_unavailable"
    NO_RELEASE_TIME = "no_release_time"
    RELEASED_AFTER_AS_OF = "released_after_as_of"


@dataclass
class ReactionReport:
    outcomes: dict[ReactionOutcome, int] = field(
        default_factory=lambda: dict.fromkeys(ReactionOutcome, 0)
    )
    unavailable_instruments: dict[str, str] = field(default_factory=dict)
    aborted: str | None = None


class MarketReactionService:
    def __init__(
        self,
        *,
        provider: MarketDataProvider,
        documents: PolicyDocumentRepository,
        observations: MarketObservationRepository,
        metrics: EventMetricRepository,
        instruments: Sequence[Instrument],
        windows: Sequence[WindowSpec],
        benchmark_symbol: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not instruments or not windows:
            raise ValueError("at least one instrument and one window are required")
        symbols = [i.symbol for i in instruments]
        if benchmark_symbol is not None and benchmark_symbol not in symbols:
            raise ValueError(f"benchmark {benchmark_symbol} is not a configured instrument")
        self._provider = provider
        self._documents = documents
        self._observations = observations
        self._metrics = metrics
        self._instruments = list(instruments)
        self._windows = list(windows)
        self._benchmark = benchmark_symbol
        self._clock = clock

    def run(
        self, central_bank: str, document_type: str, *, as_of: datetime | None = None
    ) -> ReactionReport:
        """Compute reactions for ``Compared`` documents using data visible at ``as_of``."""
        as_of = as_of or self._clock()
        report = ReactionReport()
        pending = [
            d
            for d in self._documents.list(
                central_bank=central_bank, document_type=document_type, limit=None
            )
            if d.status is DocumentStatus.COMPARED
        ]
        dated = []
        for document in pending:
            if document.published_at is None:
                report.outcomes[ReactionOutcome.NO_RELEASE_TIME] += 1
            elif document.published_at > as_of:
                report.outcomes[ReactionOutcome.RELEASED_AFTER_AS_OF] += 1
            else:
                dated.append(document)
        if not dated:
            return report

        try:
            available = self._refresh_observations(dated, as_of, report)
        except PermanentMarketDataError as exc:
            logger.error("market reaction aborted: %s", exc)
            report.aborted = str(exc)
            return report

        for document in dated:
            report.outcomes[self._link(document, as_of, available)] += 1
        return report

    def _span(self, instrument: Instrument, released_at: datetime) -> tuple[date, date]:
        cal = calendar(instrument.calendar)
        alignment = align_event(released_at, cal, instrument.observation_time)
        spans = [window_dates(alignment, w, cal) for w in self._windows]
        return min(s.start_date for s in spans), max(s.end_date for s in spans)

    def _refresh_observations(
        self, documents: list[PolicyDocument], as_of: datetime, report: ReactionReport
    ) -> set[str]:
        """Fetch one date range per instrument covering every pending document."""
        available = set()
        last_visible_day = as_of.astimezone(NEW_YORK).date()
        for instrument in self._instruments:
            spans = [self._span(instrument, d.published_at) for d in documents]
            start = min(s for s, _ in spans)
            end = min(max(e for _, e in spans), last_visible_day)
            if end < start:
                end = start
            try:
                series = self._provider.fetch(instrument.symbol, start, end)
            except TransientMarketDataError as exc:
                logger.warning("market data unavailable for %s: %s", instrument.symbol, exc)
                report.unavailable_instruments[instrument.symbol] = str(exc)
                continue
            written = self._observations.add_series(series)
            logger.info("stored %d observation rows for %s", written, instrument.symbol)
            available.add(instrument.symbol)
        return available

    def _link(
        self, document: PolicyDocument, as_of: datetime, available: set[str]
    ) -> ReactionOutcome:
        with correlation_context(document_id=document.id):
            if any(i.symbol not in available for i in self._instruments):
                return ReactionOutcome.DATA_UNAVAILABLE
            now = self._clock()
            metrics: list[EventMetric] = []
            by_symbol: dict[str, list[EventMetric]] = {}
            for instrument in self._instruments:
                cal = calendar(instrument.calendar)
                alignment = align_event(document.published_at, cal, instrument.observation_time)
                start, end = self._span(instrument, document.published_at)
                observations = self._observations.get_series(
                    self._provider.name, instrument.symbol, start, end
                )
                computed = compute_event_metrics(
                    document_id=document.id,
                    instrument=instrument,
                    alignment=alignment,
                    cal=cal,
                    observations=observations,
                    windows=self._windows,
                    provider=self._provider.name,
                    as_of=as_of,
                    computed_at=now,
                )
                by_symbol[instrument.symbol] = computed
                metrics.extend(computed)

            if any(m.missing_reason is MissingReason.WINDOW_NOT_CLOSED for m in metrics):
                return ReactionOutcome.WINDOW_OPEN

            if self._benchmark is not None:
                benchmark = by_symbol[self._benchmark]
                for instrument in self._instruments:
                    if (
                        instrument.symbol == self._benchmark
                        or instrument.kind is not ValueKind.PRICE
                    ):
                        continue
                    metrics.extend(
                        benchmark_adjusted(by_symbol[instrument.symbol], benchmark, computed_at=now)
                    )

            self._metrics.add_many(metrics)
            transition(document, DocumentStatus.MARKET_LINKED, at=now)
            self._documents.update(document)
            missing = sum(m.value is None for m in metrics)
            logger.info("market linked: %d metrics, %d missing", len(metrics), missing)
            return ReactionOutcome.LINKED
