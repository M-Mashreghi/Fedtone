"""Market-data provider port.

Providers return raw daily observations only. Calendars, windows and every metric are
computed by deterministic domain code (``fedtone.domain.calendar`` / ``.metrics``).
"""

from datetime import date
from typing import Protocol

from fedtone.domain.market import MarketSeries


class MarketDataProvider(Protocol):
    name: str

    def fetch(self, symbol: str, start: date, end: date) -> MarketSeries:
        """Observations for ``symbol`` with ``start <= date <= end``, oldest first.

        Dates the provider lists without a value come back with ``value=None``; dates it
        does not list are simply absent. Raises ``TransientMarketDataError`` or
        ``PermanentMarketDataError`` (``fedtone.domain.market``) on failure.
        """
        ...
