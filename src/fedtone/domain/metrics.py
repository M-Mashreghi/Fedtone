"""Deterministic event-window market metrics. No LLM is involved anywhere in this module.

Window notation follows return days relative to the event session T: ``[a, b]`` covers the
daily returns of sessions T+a through T+b, i.e. the move from the observation of session
T+a-1 to the observation of session T+b. ``[0, +1]`` is therefore close(T-1) → close(T+1).

Units:
- ``DECIMAL_RETURN`` and ``LOG_RETURN`` are fractions (0.0123 = 1.23%).
- ``BASIS_POINTS`` is a yield change in bps (yield in percent × 100).

Missing data is never filled: a metric without both endpoint observations is stored with
``value=None`` and a ``MissingReason``.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from fedtone.domain.calendar import EventAlignment, TradingCalendar
from fedtone.domain.market import Instrument, Observation, ValueKind

METRIC_VERSION = "market-metrics-1.0"


class MetricUnit(StrEnum):
    DECIMAL_RETURN = "decimal_return"
    LOG_RETURN = "log_return"
    BASIS_POINTS = "bps"


class MetricKind(StrEnum):
    SIMPLE_RETURN = "simple_return"
    LOG_RETURN = "log_return"
    YIELD_CHANGE = "yield_change"
    EXCESS_RETURN = "excess_return"


UNIT_OF = {
    MetricKind.SIMPLE_RETURN: MetricUnit.DECIMAL_RETURN,
    MetricKind.LOG_RETURN: MetricUnit.LOG_RETURN,
    MetricKind.YIELD_CHANGE: MetricUnit.BASIS_POINTS,
    MetricKind.EXCESS_RETURN: MetricUnit.DECIMAL_RETURN,
}


class MissingReason(StrEnum):
    NO_OBSERVATION = "no_observation"  # the provider has no row for the date
    PROVIDER_REPORTED_MISSING = "provider_reported_missing"  # row present, value empty
    WINDOW_NOT_CLOSED = "window_not_closed"  # end observation not fixed by as-of time
    NON_POSITIVE_PRICE = "non_positive_price"
    BENCHMARK_MISSING = "benchmark_missing"
    BENCHMARK_WINDOW_MISMATCH = "benchmark_window_mismatch"  # different calendars or dates


def simple_return(start: float, end: float) -> float:
    if start <= 0 or end <= 0:
        raise ValueError("prices must be positive")
    return end / start - 1.0


def log_return(start: float, end: float) -> float:
    if start <= 0 or end <= 0:
        raise ValueError("prices must be positive")
    return math.log(end / start)


def cumulative_return(daily_returns: Iterable[float]) -> float:
    """Compound daily simple returns into one simple return."""
    growth = 1.0
    for r in daily_returns:
        growth *= 1.0 + r
    return growth - 1.0


def yield_change_bps(start_percent: float, end_percent: float) -> float:
    """Change of a yield quoted in percent, in basis points."""
    return (end_percent - start_percent) * 100.0


def excess_return(asset_return: float, benchmark_return: float) -> float:
    """Benchmark-adjusted return: asset minus benchmark over the same window."""
    return asset_return - benchmark_return


@dataclass(frozen=True)
class WindowSpec:
    """Event window ``[start, end]`` in return days relative to T."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("window end must not precede its start")

    @property
    def name(self) -> str:
        def fmt(n: int) -> str:
            return "0" if n == 0 else f"{n:+d}"

        return f"[{fmt(self.start)},{fmt(self.end)}]"

    @classmethod
    def parse(cls, text: str) -> "WindowSpec":
        inner = text.strip().removeprefix("[").removesuffix("]")
        start, end = (int(part) for part in inner.split(","))
        return cls(start, end)


DEFAULT_WINDOWS = (WindowSpec(0, 0), WindowSpec(0, 1), WindowSpec(0, 5))


@dataclass(frozen=True)
class WindowDates:
    start_date: date  # observation date of session T+start-1
    end_date: date  # observation date of session T+end


def window_dates(
    alignment: EventAlignment, window: WindowSpec, cal: TradingCalendar
) -> WindowDates:
    return WindowDates(
        start_date=cal.shift(alignment.event_day, window.start - 1),
        end_date=cal.shift(alignment.event_day, window.end),
    )


@dataclass(frozen=True)
class EventMetric:
    document_id: UUID
    symbol: str
    window: str
    kind: MetricKind
    unit: MetricUnit
    value: float | None
    missing_reason: MissingReason | None
    event_day: date
    alignment_rule: str
    start_date: date
    end_date: date
    start_value: float | None
    end_value: float | None
    end_observed_at: datetime  # when the end observation is fixed; for point-in-time filters
    provider: str
    as_of: datetime
    computed_at: datetime
    metric_version: str = METRIC_VERSION
    benchmark_symbol: str | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if (self.value is None) == (self.missing_reason is None):
            raise ValueError("a metric has either a value or a missing reason")
        if self.unit is not UNIT_OF[self.kind]:
            raise ValueError(f"{self.kind} is measured in {UNIT_OF[self.kind]}")


def _endpoint(
    observations: Mapping[date, Observation], day: date
) -> tuple[float | None, MissingReason | None]:
    obs = observations.get(day)
    if obs is None:
        return None, MissingReason.NO_OBSERVATION
    if obs.value is None:
        return None, MissingReason.PROVIDER_REPORTED_MISSING
    return obs.value, None


def compute_event_metrics(
    *,
    document_id: UUID,
    instrument: Instrument,
    alignment: EventAlignment,
    cal: TradingCalendar,
    observations: Mapping[date, Observation],
    windows: Iterable[WindowSpec],
    provider: str,
    as_of: datetime,
    computed_at: datetime,
) -> list[EventMetric]:
    """Metrics for one instrument and one event.

    Point-in-time: a window whose end observation is not fixed at ``as_of`` is reported as
    ``WINDOW_NOT_CLOSED`` and observations dated after ``as_of`` are never read.
    """
    if as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    kinds = (
        (MetricKind.SIMPLE_RETURN, MetricKind.LOG_RETURN)
        if instrument.kind is ValueKind.PRICE
        else (MetricKind.YIELD_CHANGE,)
    )
    # Only observations already fixed at ``as_of`` are visible, even if newer data is loaded.
    visible = {
        d: o
        for d, o in observations.items()
        if cal.observation_at(d, instrument.observation_time) <= as_of
    }
    metrics = []
    for window in windows:
        dates = window_dates(alignment, window, cal)
        end_observed_at = cal.observation_at(dates.end_date, instrument.observation_time)
        closed = as_of >= end_observed_at
        start, start_missing = _endpoint(visible, dates.start_date)
        end, end_missing = _endpoint(visible, dates.end_date)
        for kind in kinds:
            value, reason = None, None
            if not closed:
                reason = MissingReason.WINDOW_NOT_CLOSED
            elif start_missing or end_missing:
                reason = start_missing or end_missing
            elif kind is MetricKind.YIELD_CHANGE:
                value = yield_change_bps(start, end)
            elif start <= 0 or end <= 0:
                reason = MissingReason.NON_POSITIVE_PRICE
            elif kind is MetricKind.SIMPLE_RETURN:
                value = simple_return(start, end)
            else:
                value = log_return(start, end)
            metrics.append(
                EventMetric(
                    document_id=document_id,
                    symbol=instrument.symbol,
                    window=window.name,
                    kind=kind,
                    unit=UNIT_OF[kind],
                    value=value,
                    missing_reason=reason,
                    event_day=alignment.event_day,
                    alignment_rule=alignment.rule.value,
                    start_date=dates.start_date,
                    end_date=dates.end_date,
                    start_value=start,
                    end_value=end,
                    end_observed_at=end_observed_at,
                    provider=provider,
                    as_of=as_of,
                    computed_at=computed_at,
                )
            )
    return metrics


def benchmark_adjusted(
    asset: Iterable[EventMetric], benchmark: Iterable[EventMetric], *, computed_at: datetime
) -> list[EventMetric]:
    """Excess simple returns of ``asset`` over ``benchmark`` for matching windows."""
    bench = {m.window: m for m in benchmark if m.kind is MetricKind.SIMPLE_RETURN}
    adjusted = []
    for metric in asset:
        if metric.kind is not MetricKind.SIMPLE_RETURN:
            continue
        other = bench.get(metric.window)
        value, reason = None, metric.missing_reason
        if reason is None:
            if other is None or other.value is None:
                reason = MissingReason.BENCHMARK_MISSING
            elif (other.start_date, other.end_date) != (metric.start_date, metric.end_date):
                reason = MissingReason.BENCHMARK_WINDOW_MISMATCH
            else:
                value = excess_return(metric.value, other.value)
        adjusted.append(
            EventMetric(
                document_id=metric.document_id,
                symbol=metric.symbol,
                window=metric.window,
                kind=MetricKind.EXCESS_RETURN,
                unit=MetricUnit.DECIMAL_RETURN,
                value=value,
                missing_reason=reason,
                event_day=metric.event_day,
                alignment_rule=metric.alignment_rule,
                start_date=metric.start_date,
                end_date=metric.end_date,
                start_value=metric.start_value,
                end_value=metric.end_value,
                end_observed_at=max(
                    metric.end_observed_at,
                    other.end_observed_at if other else metric.end_observed_at,
                ),
                provider=metric.provider,
                as_of=metric.as_of,
                computed_at=computed_at,
                benchmark_symbol=other.symbol if other else None,
            )
        )
    return adjusted
