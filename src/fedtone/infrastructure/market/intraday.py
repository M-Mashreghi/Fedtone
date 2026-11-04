"""Local intraday CSV provider (optional, Task 7.5).

Files ``<SYMBOL>.csv`` with header ``timestamp,value``; timestamps are ISO 8601 with an explicit
UTC offset (``2024-03-20T18:00:00+00:00``). Naive timestamps, unparsable values and duplicate
timestamps are permanent errors, so time-zone mistakes cannot silently shift event windows.
"""

import csv
from datetime import datetime
from pathlib import Path

from fedtone.domain.intraday import IntradayObservation
from fedtone.domain.market import PermanentMarketDataError


class IntradayCsvProvider:
    name = "intraday-csv"

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def fetch(self, symbol: str) -> list[IntradayObservation]:
        path = self._directory / f"{symbol}.csv"
        if not path.is_file():
            raise PermanentMarketDataError(f"no intraday file for {symbol}")
        observations: dict[datetime, IntradayObservation] = {}
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["timestamp", "value"]:
                raise PermanentMarketDataError(f"{path.name}: header must be timestamp,value")
            for line, row in enumerate(reader, start=2):
                try:
                    at = datetime.fromisoformat(row["timestamp"])
                    value = float(row["value"])
                except (TypeError, ValueError):
                    raise PermanentMarketDataError(f"{path.name}:{line}: unreadable row") from None
                if at.utcoffset() is None:
                    raise PermanentMarketDataError(f"{path.name}:{line}: timestamp has no offset")
                if at in observations:
                    raise PermanentMarketDataError(f"{path.name}:{line}: duplicate timestamp")
                observations[at] = IntradayObservation(at, value)
        return sorted(observations.values(), key=lambda o: o.at)
