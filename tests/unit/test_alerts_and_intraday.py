"""Alert rules and the optional intraday calculator (Task 7.5)."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from fedtone.domain.alerts import AlertConfig, AlertRule, evaluate_alerts
from fedtone.domain.intraday import IntradayObservation, intraday_reaction
from fedtone.domain.market import PermanentMarketDataError, ValueKind
from fedtone.domain.metrics import MissingReason
from fedtone.domain.models import (
    DocumentComparison,
    EvidenceItem,
    ToneAnalysis,
    ToneClassification,
)
from fedtone.infrastructure.market.intraday import IntradayCsvProvider

NOW = datetime(2024, 3, 20, 18, 0, tzinfo=UTC)
DOC = uuid4()


def analysis(score: float, label: ToneClassification, confidence=0.8, prompt="p") -> ToneAnalysis:
    return ToneAnalysis(
        document_id=DOC,
        document_hash="a" * 64,
        model="m",
        prompt_version=prompt,
        schema_version="s",
        net_tone_score=score,
        classification=label,
        confidence=confidence,
        evidence=(EvidenceItem("quote"),),
        explanation="x",
        created_at=NOW,
    )


def comparison(current, previous, delta, dims=None) -> DocumentComparison:
    return DocumentComparison(
        document_id=DOC,
        current_analysis_id=current.id,
        created_at=NOW,
        previous_document_id=uuid4(),
        previous_analysis_id=previous.id,
        overall_delta=delta,
        dimension_deltas=dims or {},
    )


def rules(alerts) -> set[AlertRule]:
    return {a.rule for a in alerts}


def test_material_change_fires_configured_rules() -> None:
    prev = analysis(0.5, ToneClassification.HAWKISH)
    cur = analysis(0.1, ToneClassification.NEUTRAL)
    cmp_ = comparison(cur, prev, -0.4, {"inflation": -0.6, "labor": 0.1, "growth": None})

    alerts = evaluate_alerts(AlertConfig(), cur, prev, cmp_, now=NOW)

    assert rules(alerts) == {
        AlertRule.OVERALL_DELTA,
        AlertRule.CLASSIFICATION_CHANGE,
        AlertRule.DIMENSION_DELTA,
    }
    dims = next(a for a in alerts if a.rule is AlertRule.DIMENSION_DELTA)
    assert dims.values["dimension_deltas"] == {"inflation": -0.6}


def test_small_change_fires_nothing() -> None:
    prev = analysis(0.3, ToneClassification.HAWKISH)
    cur = analysis(0.25, ToneClassification.HAWKISH)

    assert evaluate_alerts(AlertConfig(), cur, prev, comparison(cur, prev, -0.05), now=NOW) == []


@pytest.mark.parametrize(
    "config",
    [
        AlertConfig(
            min_abs_overall_delta=None, classification_change=False, min_abs_dimension_delta=None
        ),
        AlertConfig(min_confidence=0.9),  # one side below the confidence floor
    ],
)
def test_rules_can_be_disabled_or_gated(config) -> None:
    prev = analysis(0.5, ToneClassification.HAWKISH)
    cur = analysis(-0.5, ToneClassification.DOVISH)

    assert evaluate_alerts(config, cur, prev, comparison(cur, prev, -1.0), now=NOW) == []


def test_no_alert_for_baseline_or_different_versions() -> None:
    prev = analysis(0.5, ToneClassification.HAWKISH, prompt="old")
    cur = analysis(-0.5, ToneClassification.DOVISH, prompt="new")
    # Different versions: the comparison stores no delta, and labels are not compared.
    assert evaluate_alerts(AlertConfig(), cur, prev, comparison(cur, prev, None), now=NOW) == []
    assert evaluate_alerts(AlertConfig(), cur, None, None, now=NOW) == []


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        AlertConfig(min_abs_overall_delta=0)
    with pytest.raises(ValueError):
        AlertConfig(min_confidence=1.5)


# -- intraday --------------------------------------------------------------------------------

EVENT = datetime(2024, 3, 20, 18, 0, tzinfo=UTC)  # 14:00 New York (EDT)
NY = timezone(timedelta(hours=-4))


def series(*points) -> list[IntradayObservation]:
    return [IntradayObservation(at, value) for at, value in points]


def test_intraday_return_uses_tz_aware_window_edges() -> None:
    obs = series(
        (datetime(2024, 3, 20, 13, 40, tzinfo=NY), 100.0),  # 17:40 UTC: before start
        (datetime(2024, 3, 20, 13, 45, tzinfo=NY), 101.0),  # 17:45 UTC = window start
        (datetime(2024, 3, 20, 14, 30, tzinfo=NY), 102.0),
        (datetime(2024, 3, 20, 15, 0, tzinfo=NY), 103.02),  # 19:00 UTC = window end
        (datetime(2024, 3, 20, 15, 5, tzinfo=NY), 999.0),  # after window
    )

    reaction = intraday_reaction(
        obs,
        symbol="SPX",
        kind=ValueKind.PRICE,
        event_at=EVENT,
        minutes_before=15,
        minutes_after=60,
        as_of=EVENT + timedelta(days=1),
    )

    assert reaction.window == "[-15m,+60m]"
    assert reaction.value == pytest.approx(103.02 / 101.0 - 1)
    assert reaction.start_at == datetime(2024, 3, 20, 17, 45, tzinfo=UTC)
    assert reaction.end_at == datetime(2024, 3, 20, 19, 0, tzinfo=UTC)


def test_intraday_yield_change_in_bps() -> None:
    obs = series((EVENT - timedelta(minutes=20), 4.30), (EVENT + timedelta(minutes=30), 4.25))

    reaction = intraday_reaction(
        obs,
        symbol="DGS10",
        kind=ValueKind.YIELD_PERCENT,
        event_at=EVENT,
        minutes_before=15,
        minutes_after=60,
        as_of=EVENT + timedelta(hours=2),
    )

    assert reaction.value == pytest.approx(-5.0)
    assert reaction.unit.value == "bps"


def test_intraday_point_in_time() -> None:
    obs = series((EVENT - timedelta(minutes=20), 100.0), (EVENT + timedelta(minutes=10), 101.0))

    open_window = intraday_reaction(
        obs,
        symbol="X",
        kind=ValueKind.PRICE,
        event_at=EVENT,
        minutes_before=15,
        minutes_after=60,
        as_of=EVENT + timedelta(minutes=30),
    )
    hidden = intraday_reaction(
        obs,
        symbol="X",
        kind=ValueKind.PRICE,
        event_at=EVENT,
        minutes_before=15,
        minutes_after=5,
        as_of=EVENT + timedelta(minutes=6),
    )

    assert open_window.value is None
    assert open_window.missing_reason is MissingReason.WINDOW_NOT_CLOSED
    # The +10m observation is outside [-15m,+5m]: no end observation, not a later value.
    assert hidden.missing_reason is MissingReason.NO_OBSERVATION


def test_intraday_rejects_naive_times() -> None:
    with pytest.raises(ValueError):
        IntradayObservation(datetime(2024, 3, 20, 18, 0), 1.0)
    with pytest.raises(ValueError):
        intraday_reaction(
            [],
            symbol="X",
            kind=ValueKind.PRICE,
            event_at=datetime(2024, 1, 1),
            minutes_before=1,
            minutes_after=1,
            as_of=NOW,
        )


def test_intraday_csv_provider(tmp_path: Path) -> None:
    (tmp_path / "SPX.csv").write_text(
        "timestamp,value\n2024-03-20T14:00:00-04:00,100\n2024-03-20T17:45:00+00:00,101\n",
        encoding="utf-8",
    )
    (tmp_path / "NAIVE.csv").write_text("timestamp,value\n2024-03-20T14:00:00,100\n", "utf-8")
    (tmp_path / "DUP.csv").write_text(
        "timestamp,value\n2024-03-20T18:00:00+00:00,1\n2024-03-20T14:00:00-04:00,2\n", "utf-8"
    )
    provider = IntradayCsvProvider(tmp_path)

    obs = provider.fetch("SPX")

    assert [o.at for o in obs] == [
        datetime(2024, 3, 20, 17, 45, tzinfo=UTC),
        datetime(2024, 3, 20, 18, 0, tzinfo=UTC),
    ]
    for symbol, message in (("NAIVE", "no offset"), ("DUP", "duplicate"), ("NONE", "no intraday")):
        with pytest.raises(PermanentMarketDataError, match=message):
            provider.fetch(symbol)
