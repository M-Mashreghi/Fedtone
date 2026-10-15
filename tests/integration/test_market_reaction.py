from datetime import UTC, date, datetime, time

import pytest

from fedtone.application.market_reaction import ReactionOutcome
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.domain.market import (
    Instrument,
    PermanentMarketDataError,
    TransientMarketDataError,
    ValueKind,
)
from fedtone.domain.metrics import MetricKind, MissingReason
from tests.fake_market import FakeMarketProvider
from tests.market_harness import INSTRUMENTS, MarketHarness, market_data


def metric(metrics, symbol, window, kind):
    (found,) = [m for m in metrics if (m.symbol, m.window, m.kind) == (symbol, window, kind)]
    return found


def test_fake_provider_metrics_are_persisted_for_a_meeting(session_factory) -> None:
    h = MarketHarness(session_factory)
    assert {h.doc(n).status for n in ("dec", "jan", "mar")} == {DocumentStatus.COMPARED}

    report = h.reaction_service().run("FED", "fomc_statement")

    assert report.outcomes[ReactionOutcome.LINKED] == 3
    jan = h.doc("jan")
    assert jan.status is DocumentStatus.MARKET_LINKED
    metrics = h.metrics.for_document(jan.id)
    # 2 price metrics x 3 windows + 1 yield metric x 3 windows
    assert len(metrics) == 9
    data = market_data()
    spx = metric(metrics, "SP500", "[0,+1]", MetricKind.SIMPLE_RETURN)
    assert (spx.start_date, spx.end_date) == (date(2024, 1, 30), date(2024, 2, 1))
    assert spx.value == pytest.approx(
        data["SP500"][date(2024, 2, 1)] / data["SP500"][date(2024, 1, 30)] - 1
    )
    tsy = metric(metrics, "DGS10", "[0,+5]", MetricKind.YIELD_CHANGE)
    assert tsy.end_date == date(2024, 2, 7)
    expected_bps = (data["DGS10"][date(2024, 2, 7)] - data["DGS10"][date(2024, 1, 30)]) * 100
    assert tsy.value == pytest.approx(expected_bps)
    assert spx.alignment_rule == "released_before_same_day_observation"


def test_observations_are_stored_once_and_rerun_is_idempotent(session_factory) -> None:
    h = MarketHarness(session_factory)
    provider = FakeMarketProvider(market_data())
    h.reaction_service(provider).run("FED", "fomc_statement")
    stored = h.observations.get_series("fake", "SP500", date(2023, 12, 1), date(2024, 4, 1))
    assert stored[date(2024, 1, 31)].value == market_data()["SP500"][date(2024, 1, 31)]

    again = h.reaction_service(provider).run("FED", "fomc_statement")
    assert again.outcomes[ReactionOutcome.LINKED] == 0
    assert (
        h.observations.add_series(provider.fetch("SP500", date(2024, 1, 1), date(2024, 2, 1))) == 0
    )


def test_recent_meeting_waits_until_its_windows_close(session_factory) -> None:
    h = MarketHarness(session_factory)
    as_of = datetime(2024, 3, 22, tzinfo=UTC)  # Mar 20 meeting: T+5 (Mar 27) still open

    report = h.reaction_service().run("FED", "fomc_statement", as_of=as_of)

    assert report.outcomes[ReactionOutcome.LINKED] == 2
    assert report.outcomes[ReactionOutcome.WINDOW_OPEN] == 1
    assert h.doc("mar").status is DocumentStatus.COMPARED
    assert h.metrics.for_document(h.doc("mar").id) == []

    later = h.reaction_service().run("FED", "fomc_statement")
    assert later.outcomes[ReactionOutcome.LINKED] == 1
    assert h.doc("mar").status is DocumentStatus.MARKET_LINKED


def test_backtest_as_of_never_reads_later_prices(session_factory) -> None:
    h = MarketHarness(session_factory)
    provider = FakeMarketProvider(market_data())
    as_of = datetime(2024, 2, 15, tzinfo=UTC)

    h.reaction_service(provider).run("FED", "fomc_statement", as_of=as_of)

    assert all(end <= date(2024, 2, 15) for _, _, end in provider.calls)
    assert h.doc("mar").status is DocumentStatus.COMPARED  # released after as_of


def test_missing_observation_is_stored_explicitly(session_factory) -> None:
    h = MarketHarness(session_factory)
    data = market_data()
    data["DGS10"][date(2024, 2, 1)] = None
    del data["SP500"][date(2024, 2, 1)]

    h.reaction_service(FakeMarketProvider(data)).run("FED", "fomc_statement")

    metrics = h.metrics.for_document(h.doc("jan").id)
    spx = metric(metrics, "SP500", "[0,+1]", MetricKind.SIMPLE_RETURN)
    tsy = metric(metrics, "DGS10", "[0,+1]", MetricKind.YIELD_CHANGE)
    assert (spx.value, spx.missing_reason) == (None, MissingReason.NO_OBSERVATION)
    assert (tsy.value, tsy.missing_reason) == (None, MissingReason.PROVIDER_REPORTED_MISSING)
    assert metric(metrics, "SP500", "[0,0]", MetricKind.SIMPLE_RETURN).value is not None
    assert h.doc("jan").status is DocumentStatus.MARKET_LINKED


def test_provider_outage_keeps_documents_compared(session_factory) -> None:
    h = MarketHarness(session_factory)
    provider = FakeMarketProvider(market_data(), errors={"DGS10": TransientMarketDataError("503")})

    report = h.reaction_service(provider).run("FED", "fomc_statement")

    assert report.outcomes[ReactionOutcome.DATA_UNAVAILABLE] == 3
    assert "DGS10" in report.unavailable_instruments
    assert h.doc("jan").status is DocumentStatus.COMPARED
    assert h.reaction_service().run("FED", "fomc_statement").outcomes[ReactionOutcome.LINKED] == 3


def test_permanent_provider_error_aborts_without_touching_documents(session_factory) -> None:
    h = MarketHarness(session_factory)
    provider = FakeMarketProvider(
        market_data(), errors={"SP500": PermanentMarketDataError("bad id")}
    )

    report = h.reaction_service(provider).run("FED", "fomc_statement")

    assert report.aborted == "bad id"
    assert {h.doc(n).status for n in ("dec", "jan", "mar")} == {DocumentStatus.COMPARED}


def test_benchmark_adjusted_metrics_are_added(session_factory) -> None:
    qqq = Instrument("QQQ", "Nasdaq proxy", ValueKind.PRICE, "nyse", time(16, 0))
    data = market_data() | {"QQQ": {d: v / 10 + 1 for d, v in market_data()["SP500"].items()}}
    h = MarketHarness(session_factory)
    h.reaction_service(
        FakeMarketProvider(data), instruments=(*INSTRUMENTS, qqq), benchmark_symbol="SP500"
    ).run("FED", "fomc_statement")

    metrics = h.metrics.for_document(h.doc("jan").id)
    excess = metric(metrics, "QQQ", "[0,+1]", MetricKind.EXCESS_RETURN)
    qqq_r = metric(metrics, "QQQ", "[0,+1]", MetricKind.SIMPLE_RETURN).value
    spx_r = metric(metrics, "SP500", "[0,+1]", MetricKind.SIMPLE_RETURN).value
    assert excess.value == pytest.approx(qqq_r - spx_r)
    assert excess.benchmark_symbol == "SP500"


def test_benchmark_must_be_a_configured_instrument(session_factory) -> None:
    h = MarketHarness(session_factory)
    with pytest.raises(ValueError):
        h.reaction_service(benchmark_symbol="NOPE")
