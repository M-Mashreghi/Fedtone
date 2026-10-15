"""In-memory market-data provider for tests (no network)."""

from datetime import UTC, date, datetime, timedelta

from fedtone.domain.market import MarketSeries, Observation

RETRIEVED_AT = datetime(2024, 6, 1, tzinfo=UTC)


def weekday_series(start: date, end: date, first: float, step: float) -> dict[date, float]:
    """One value per weekday: first, first+step, ... (holidays are included on purpose)."""
    values, day, value = {}, start, first
    while day <= end:
        if day.weekday() < 5:
            values[day] = round(value, 6)
            value += step
        day += timedelta(days=1)
    return values


class FakeMarketProvider:
    name = "fake"

    def __init__(
        self,
        data: dict[str, dict[date, float | None]],
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.data = data
        self.errors = errors or {}
        self.calls: list[tuple[str, date, date]] = []

    def fetch(self, symbol: str, start: date, end: date) -> MarketSeries:
        self.calls.append((symbol, start, end))
        if symbol in self.errors:
            raise self.errors[symbol]
        values = self.data.get(symbol, {})
        observations = tuple(
            Observation(symbol, d, v, self.name, RETRIEVED_AT)
            for d, v in sorted(values.items())
            if start <= d <= end
        )
        return MarketSeries(symbol, self.name, RETRIEVED_AT, observations)
