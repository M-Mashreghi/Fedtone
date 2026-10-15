import ast
import math
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import uuid4

import pytest

from fedtone.domain.calendar import NEW_YORK, align_event, calendar
from fedtone.domain.market import Instrument, Observation, ValueKind
from fedtone.domain.metrics import (
    DEFAULT_WINDOWS,
    METRIC_VERSION,
    EventMetric,
    MetricKind,
    MetricUnit,
    MissingReason,
    WindowSpec,
    benchmark_adjusted,
    compute_event_metrics,
    cumulative_return,
    excess_return,
    log_return,
    simple_return,
    yield_change_bps,
)

NYSE = calendar("nyse")
TREASURY = calendar("us_treasury")
RETRIEVED = datetime(2024, 3, 1, tzinfo=UTC)
SPX = Instrument("SP500", "S&P 500 index", ValueKind.PRICE, "nyse", time(16, 0))
DGS10 = Instrument(
    "DGS10", "10-Year Treasury yield", ValueKind.YIELD_PERCENT, "us_treasury", time(15, 30)
)
RELEASE = datetime(2024, 1, 31, 19, 0, tzinfo=UTC)  # 14:00 EST
AFTER_ALL = datetime(2024, 3, 1, tzinfo=UTC)


def test_hand_calculated_returns() -> None:
    assert simple_return(4800.0, 4848.0) == pytest.approx(0.01, abs=1e-15)
    assert log_return(4800.0, 4848.0) == pytest.approx(0.009950330853168, abs=1e-12)
    assert cumulative_return([0.01, -0.02]) == pytest.approx(-0.0102, abs=1e-15)
    assert cumulative_return([]) == 0.0
    assert excess_return(0.01, 0.004) == pytest.approx(0.006, abs=1e-15)


def test_yield_change_is_in_basis_points() -> None:
    assert yield_change_bps(4.10, 3.95) == pytest.approx(-15.0, abs=1e-9)
    assert yield_change_bps(4.00, 4.00) == 0.0


def test_cumulative_of_daily_returns_equals_endpoint_return() -> None:
    prices = [100.0, 101.5, 99.0, 102.25]
    daily = [simple_return(a, b) for a, b in zip(prices, prices[1:], strict=False)]
    assert cumulative_return(daily) == pytest.approx(simple_return(prices[0], prices[-1]))


@pytest.mark.parametrize("bad", [(0.0, 1.0), (1.0, -2.0)])
def test_non_positive_prices_are_rejected(bad) -> None:
    with pytest.raises(ValueError):
        simple_return(*bad)
    with pytest.raises(ValueError):
        log_return(*bad)


def series(symbol: str, values: dict[date, float | None]) -> dict[date, Observation]:
    return {d: Observation(symbol, d, v, "fixture", RETRIEVED) for d, v in values.items()}


SPX_VALUES = {
    date(2024, 1, 30): 4924.97,
    date(2024, 1, 31): 4845.65,
    date(2024, 2, 1): 4906.19,
    date(2024, 2, 7): 4995.06,
}
DGS10_VALUES = {
    date(2024, 1, 30): 4.06,
    date(2024, 1, 31): 3.99,
    date(2024, 2, 1): 3.87,
    date(2024, 2, 7): None,  # provider listed the date without a value
}


def metrics_for(instrument, values, *, as_of=AFTER_ALL, windows=DEFAULT_WINDOWS):
    cal = calendar(instrument.calendar)
    return compute_event_metrics(
        document_id=uuid4(),
        instrument=instrument,
        alignment=align_event(RELEASE, cal, instrument.observation_time),
        cal=cal,
        observations=series(instrument.symbol, values),
        windows=windows,
        provider="fixture",
        as_of=as_of,
        computed_at=AFTER_ALL,
    )


def by_key(metrics):
    return {(m.window, m.kind): m for m in metrics}


def test_price_metrics_match_hand_calculation() -> None:
    m = by_key(metrics_for(SPX, SPX_VALUES))
    day0 = m[("[0,0]", MetricKind.SIMPLE_RETURN)]
    assert day0.value == pytest.approx(4845.65 / 4924.97 - 1)
    assert day0.value == pytest.approx(-0.0161056819, abs=1e-10)
    assert day0.unit is MetricUnit.DECIMAL_RETURN
    assert (day0.start_date, day0.end_date) == (date(2024, 1, 30), date(2024, 1, 31))
    assert m[("[0,+1]", MetricKind.SIMPLE_RETURN)].value == pytest.approx(-0.0038132212, abs=1e-10)
    assert m[("[0,+1]", MetricKind.LOG_RETURN)].value == pytest.approx(math.log(4906.19 / 4924.97))
    assert m[("[0,+5]", MetricKind.SIMPLE_RETURN)].value == pytest.approx(0.0142315588, abs=1e-10)
    assert all(x.metric_version == METRIC_VERSION for x in m.values())


def test_yield_metrics_are_basis_points_and_missing_is_explicit() -> None:
    m = by_key(metrics_for(DGS10, DGS10_VALUES))
    assert set(k for _, k in m) == {MetricKind.YIELD_CHANGE}
    assert m[("[0,0]", MetricKind.YIELD_CHANGE)].value == pytest.approx(-7.0)
    assert m[("[0,+1]", MetricKind.YIELD_CHANGE)].value == pytest.approx(-19.0)
    assert m[("[0,+1]", MetricKind.YIELD_CHANGE)].unit is MetricUnit.BASIS_POINTS
    week = m[("[0,+5]", MetricKind.YIELD_CHANGE)]
    assert week.value is None
    assert week.missing_reason is MissingReason.PROVIDER_REPORTED_MISSING


def test_absent_date_is_never_filled_from_neighbours() -> None:
    values = dict(SPX_VALUES)
    del values[date(2024, 2, 1)]
    m = by_key(metrics_for(SPX, values))
    metric = m[("[0,+1]", MetricKind.SIMPLE_RETURN)]
    assert metric.value is None
    assert metric.missing_reason is MissingReason.NO_OBSERVATION
    assert metric.end_value is None


def test_window_not_closed_and_later_observations_are_invisible() -> None:
    # As of 15:00 New York on Feb 1 the Feb 1 close is not fixed, even though the dict
    # (e.g. a re-run on newer data) already contains it.
    as_of = datetime(2024, 2, 1, 15, 0, tzinfo=NEW_YORK)
    m = by_key(metrics_for(SPX, SPX_VALUES, as_of=as_of))
    assert m[("[0,0]", MetricKind.SIMPLE_RETURN)].value is not None
    for window in ("[0,+1]", "[0,+5]"):
        metric = m[(window, MetricKind.SIMPLE_RETURN)]
        assert metric.missing_reason is MissingReason.WINDOW_NOT_CLOSED
        assert metric.end_value is None


def test_end_observed_at_records_when_the_window_closed() -> None:
    m = by_key(metrics_for(SPX, SPX_VALUES))
    assert m[("[0,+1]", MetricKind.SIMPLE_RETURN)].end_observed_at == datetime(
        2024, 2, 1, 16, 0, tzinfo=NEW_YORK
    )


def test_benchmark_adjusted_returns() -> None:
    asset = metrics_for(SPX, SPX_VALUES)
    bench_values = {d: v * 1.0 if d != date(2024, 2, 1) else 4900.0 for d, v in SPX_VALUES.items()}
    bench = [
        EventMetric(**{**m.__dict__, "symbol": "BENCH", "id": uuid4()})
        for m in metrics_for(SPX, bench_values)
    ]
    adjusted = by_key(benchmark_adjusted(asset, bench, computed_at=AFTER_ALL))
    day1 = adjusted[("[0,+1]", MetricKind.EXCESS_RETURN)]
    assert day1.value == pytest.approx((4906.19 / 4924.97 - 1) - (4900.0 / 4924.97 - 1))
    assert day1.benchmark_symbol == "BENCH"
    assert adjusted[("[0,0]", MetricKind.EXCESS_RETURN)].value == pytest.approx(0.0)


def test_benchmark_on_a_different_calendar_is_not_mixed() -> None:
    asset = metrics_for(SPX, SPX_VALUES)
    dollar = Instrument("DXY", "dollar", ValueKind.PRICE, "us_treasury", time(12, 0))
    days = [date(2024, 1, d) for d in range(29, 32)] + [date(2024, 2, d) for d in range(1, 10)]
    bench = metrics_for(dollar, dict.fromkeys(days, 100.0))
    assert all(m.value is not None for m in bench)
    adjusted = benchmark_adjusted(asset, bench, computed_at=AFTER_ALL)
    assert {m.missing_reason for m in adjusted} == {MissingReason.BENCHMARK_WINDOW_MISMATCH}


def test_metric_requires_value_or_reason_and_matching_unit() -> None:
    metric = metrics_for(SPX, SPX_VALUES, windows=[WindowSpec(0, 0)])[0]
    with pytest.raises(ValueError):
        EventMetric(**{**metric.__dict__, "missing_reason": MissingReason.NO_OBSERVATION})
    with pytest.raises(ValueError):
        EventMetric(**{**metric.__dict__, "unit": MetricUnit.BASIS_POINTS})


def test_metric_modules_never_import_llm_code() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "fedtone" / "domain"
    for name in ("metrics.py", "calendar.py", "analogues.py", "market.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any("llm" in m or "prompt" in m for m in imported), name
