"""Market instruments and daily observations.

Units are explicit: a ``PRICE`` series holds index/price levels, a ``YIELD_PERCENT`` series
holds yields in percent (4.25 means 4.25%). An observation whose value is ``None`` is a
date the provider listed without a value; it is never filled or interpolated.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum


class MarketDataError(Exception):
    """Base class for market-data provider failures."""


class TransientMarketDataError(MarketDataError):
    """A failure that may succeed later (network error, timeout, 5xx, 429)."""


class PermanentMarketDataError(MarketDataError):
    """A failure that retrying will not fix (unknown series, unexpected file format)."""


class ValueKind(StrEnum):
    PRICE = "price"
    YIELD_PERCENT = "yield_percent"


@dataclass(frozen=True)
class Instrument:
    """A tracked series.

    ``observation_time`` is the New York local time the daily value refers to (an equity
    close, the H.15 yield reference time, a noon FX fixing). It decides whether an
    announcement falls before or after that day's observation.
    """

    symbol: str
    name: str
    kind: ValueKind
    calendar: str
    observation_time: time

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.name.strip():
            raise ValueError("instrument symbol and name are required")


@dataclass(frozen=True)
class Observation:
    symbol: str
    date: date
    value: float | None
    provider: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")


@dataclass(frozen=True)
class MarketSeries:
    symbol: str
    provider: str
    retrieved_at: datetime
    observations: tuple[Observation, ...]

    def by_date(self) -> dict[date, Observation]:
        return {o.date: o for o in self.observations}
