"""Optional intraday event-window reaction (Task 7.5). Deterministic; no LLM involved.

All timestamps are timezone-aware and compared in UTC, so daylight-saving changes and
different market time zones cannot shift a window. Point-in-time rules match the daily
engine: observations after ``as_of`` are never read, and a window that has not ended at
``as_of`` is reported as not closed instead of being computed from partial data.

Window ``[-b, +a]`` minutes: start = last observation at or before ``event - b``;
end = last observation after the event and at or before ``event + a``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from fedtone.domain.market import ValueKind
from fedtone.domain.metrics import MetricUnit, MissingReason, simple_return, yield_change_bps


@dataclass(frozen=True)
class IntradayObservation:
    at: datetime
    value: float

    def __post_init__(self) -> None:
        if self.at.utcoffset() is None:
            raise ValueError("intraday timestamps must be timezone-aware")


@dataclass(frozen=True)
class IntradayReaction:
    symbol: str
    event_at: datetime
    window: str
    unit: MetricUnit
    value: float | None
    missing_reason: MissingReason | None
    start_at: datetime | None
    end_at: datetime | None
    start_value: float | None
    end_value: float | None
    as_of: datetime


def intraday_reaction(
    observations: Iterable[IntradayObservation],
    *,
    symbol: str,
    kind: ValueKind,
    event_at: datetime,
    minutes_before: int,
    minutes_after: int,
    as_of: datetime,
) -> IntradayReaction:
    for name, value in (("event_at", event_at), ("as_of", as_of)):
        if value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
    if minutes_before < 0 or minutes_after <= 0:
        raise ValueError("minutes_before must be >= 0 and minutes_after > 0")
    unit = MetricUnit.BASIS_POINTS if kind is ValueKind.YIELD_PERCENT else MetricUnit.DECIMAL_RETURN
    window_start = event_at - timedelta(minutes=minutes_before)
    window_end = event_at + timedelta(minutes=minutes_after)
    label = f"[-{minutes_before}m,+{minutes_after}m]"

    visible = sorted((o for o in observations if o.at <= as_of), key=lambda o: o.at)
    start = next((o for o in reversed(visible) if o.at <= window_start), None)
    end = next((o for o in reversed(visible) if event_at < o.at <= window_end), None)

    reason = None
    value = None
    if as_of < window_end:
        reason = MissingReason.WINDOW_NOT_CLOSED
    elif start is None or end is None:
        reason = MissingReason.NO_OBSERVATION
    elif kind is ValueKind.YIELD_PERCENT:
        value = yield_change_bps(start.value, end.value)
    elif start.value <= 0 or end.value <= 0:
        reason = MissingReason.NON_POSITIVE_PRICE
    else:
        value = simple_return(start.value, end.value)
    return IntradayReaction(
        symbol=symbol,
        event_at=event_at,
        window=label,
        unit=unit,
        value=value,
        missing_reason=reason,
        start_at=start.at if start and reason is None else None,
        end_at=end.at if end and reason is None else None,
        start_value=start.value if start and reason is None else None,
        end_value=end.value if end and reason is None else None,
        as_of=as_of,
    )
