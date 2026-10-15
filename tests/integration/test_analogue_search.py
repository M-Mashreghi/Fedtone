from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from fedtone.application.analogue_search import AnalogueService
from tests.market_harness import RELEASES, MarketHarness

# Four meetings; the April one lets us check that later meetings never become analogues.
WITH_APRIL = RELEASES | {"apr": datetime(2024, 5, 1, 18, 0, tzinfo=UTC)}


def service(h: MarketHarness) -> AnalogueService:
    return AnalogueService(
        documents=h.documents, analyses=h.analyses, comparisons=h.comparisons, metrics=h.metrics
    )


@pytest.fixture
def harness(session_factory, monkeypatch) -> MarketHarness:
    from tests import market_harness

    monkeypatch.setitem(market_harness.TEXTS, "apr", market_harness.TEXTS["jan"])
    h = MarketHarness(session_factory, releases=WITH_APRIL)
    h.reaction_service().run("FED", "fomc_statement")
    return h


def test_only_earlier_meetings_are_analogues(harness) -> None:
    report = service(harness).find(harness.doc("mar").id, top_k=5, min_sample=1)

    found = {m.document_id for m in report.result.matches}
    assert found == {harness.doc("dec").id, harness.doc("jan").id}
    assert harness.doc("apr").id not in found
    assert report.result.cutoff == RELEASES["mar"]


def test_ranking_is_deterministic(harness) -> None:
    first = service(harness).find(harness.doc("apr").id, min_sample=1)
    second = service(harness).find(harness.doc("apr").id, min_sample=1)
    assert [m.document_id for m in first.result.matches] == [
        m.document_id for m in second.result.matches
    ]
    # April has the January wording and score, so January ranks first.
    assert first.result.matches[0].document_id == harness.doc("jan").id


def test_reactions_after_the_cutoff_are_withheld(harness) -> None:
    # Backtest two days after the January meeting: its [0,+5] window had not closed yet, so
    # only the shorter windows may be shown for January as an analogue.
    jan = harness.doc("jan").id
    report = service(harness).find(
        harness.doc("apr").id, min_sample=1, cutoff=RELEASES["jan"] + timedelta(days=2)
    )
    assert [m.document_id for m in report.result.matches] == [jan, harness.doc("dec").id]
    assert jan in report.reactions
    windows = {m.window for m in report.reactions[jan]}
    assert windows == {"[0,0]", "[0,+1]"}
    assert report.withheld_after_cutoff == 3  # [0,+5]: two price metrics + one yield metric
    assert all(m.end_observed_at < report.result.cutoff for m in report.reactions[jan])


def test_insufficient_history_is_flagged(harness) -> None:
    report = service(harness).find(harness.doc("jan").id, min_sample=3)
    assert report.result.sample_size == 1
    assert report.result.insufficient_history


def test_unknown_document_is_reported(harness) -> None:
    with pytest.raises(LookupError):
        service(harness).find(uuid4())
