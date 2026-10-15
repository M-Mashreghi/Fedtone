"""FRED daily series, read from FRED's public CSV download (no API key needed).

The same CSV layout backs ``CsvFileProvider``, so a file downloaded by hand from
https://fred.stlouisfed.org works offline.

CSV layout: a header ``observation_date,<SERIES>`` (older files use ``DATE``), then one row per
date; a missing value is ``.`` or empty. Values are the latest published figures, not
historical vintages; daily market closes are rarely revised.
"""

import csv
import io
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode

from fedtone.application.sources import PermanentSourceError, TransientSourceError
from fedtone.domain.market import (
    MarketSeries,
    Observation,
    PermanentMarketDataError,
    TransientMarketDataError,
)
from fedtone.infrastructure.sources.http import HttpFetcher

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_DATE_HEADERS = {"observation_date", "date"}
_MISSING = {"", "."}


def parse_fred_csv(
    content: str,
    *,
    symbol: str,
    provider: str,
    retrieved_at: datetime,
    start: date,
    end: date,
) -> MarketSeries:
    rows = list(csv.reader(io.StringIO(content.lstrip("﻿"))))
    if not rows or len(rows[0]) != 2 or rows[0][0].strip().lower() not in _DATE_HEADERS:
        raise PermanentMarketDataError(f"{symbol}: not a FRED CSV (unexpected header)")
    if rows[0][1].strip().upper() != symbol.upper():
        raise PermanentMarketDataError(f"{symbol}: CSV holds series {rows[0][1].strip()!r}")
    observations = []
    for line_no, row in enumerate(rows[1:], start=2):
        if not row:
            continue
        if len(row) != 2:
            raise PermanentMarketDataError(f"{symbol}: malformed CSV line {line_no}")
        try:
            day = date.fromisoformat(row[0].strip())
            raw = row[1].strip()
            value = None if raw in _MISSING else float(raw)
        except ValueError as exc:
            raise PermanentMarketDataError(f"{symbol}: malformed CSV line {line_no}") from exc
        if start <= day <= end:
            observations.append(Observation(symbol, day, value, provider, retrieved_at))
    observations.sort(key=lambda o: o.date)
    if len({o.date for o in observations}) != len(observations):
        raise PermanentMarketDataError(f"{symbol}: duplicate dates in CSV")
    return MarketSeries(symbol, provider, retrieved_at, tuple(observations))


class FredCsvProvider:
    name = "fred"

    def __init__(
        self,
        fetcher: HttpFetcher,
        *,
        base_url: str = FRED_CSV_URL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._fetcher = fetcher
        self._base_url = base_url
        self._clock = clock

    def fetch(self, symbol: str, start: date, end: date) -> MarketSeries:
        query = urlencode({"id": symbol, "cosd": start.isoformat(), "coed": end.isoformat()})
        try:
            response = self._fetcher.get(f"{self._base_url}?{query}")
        except TransientSourceError as exc:
            raise TransientMarketDataError(str(exc)) from exc
        except PermanentSourceError as exc:
            raise PermanentMarketDataError(str(exc)) from exc
        return parse_fred_csv(
            response.content.decode("utf-8", errors="replace"),
            symbol=symbol,
            provider=self.name,
            retrieved_at=self._clock(),
            start=start,
            end=end,
        )


class CsvFileProvider:
    """Reads ``<directory>/<SYMBOL>.csv`` files in FRED CSV layout."""

    name = "csv"

    def __init__(
        self, directory: Path, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._directory = directory
        self._clock = clock

    def fetch(self, symbol: str, start: date, end: date) -> MarketSeries:
        path = self._directory / f"{symbol}.csv"
        if not path.is_file():
            raise PermanentMarketDataError(f"{symbol}: no file {path}")
        return parse_fred_csv(
            path.read_text(encoding="utf-8"),
            symbol=symbol,
            provider=self.name,
            retrieved_at=self._clock(),
            start=start,
            end=end,
        )
