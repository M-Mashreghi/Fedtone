"""Deterministic evidence gate for agentic claims (Task 7.4)."""

import pytest

from fedtone.domain.agent_notes import Section, check_claim, is_market_fact
from fedtone.domain.summary import Fact

FACTS = {
    f.id: f
    for f in (
        Fact("tone", "overall tone score (-1 dovish to +1 hawkish)", "+0.20"),
        Fact("delta", "overall tone change versus previous statement", "-0.30"),
        Fact("quote1", "evidence quote (inflation)", "Inflation remains elevated."),
        Fact("SP500_simple_return_[0,+1]", "S&P 500 index return over window [0,+1]", "+0.21%"),
        Fact("DGS10_yield_change_[0,+1]", "10-Year yield change over window [0,+1]", "-2.0 bps"),
    )
}


def problems(section, text, cited):
    return check_claim(section, text, cited, FACTS)


def test_supported_claims_pass() -> None:
    assert problems(Section.POLICY, "Tone was +0.20, down -0.30.", ["tone", "delta"]) == []
    assert problems(Section.POLICY, 'It said "Inflation remains elevated."', ["quote1"]) == []
    assert (
        problems(
            Section.MARKET,
            "The 10-year yield moved -2.0 bps over [0,+1] after the release.",
            ["DGS10_yield_change_[0,+1]"],
        )
        == []
    )


@pytest.mark.parametrize(
    ("section", "text", "cited", "expected"),
    [
        (Section.POLICY, "Tone was +0.20.", ["nope"], "unknown fact ids"),
        (Section.POLICY, "Tone was +0.20.", [], "no valid citation"),
        # The number exists in a fact, but not in a *cited* one.
        (Section.POLICY, "Tone was +0.20 and stocks rose 0.21%.", ["tone"], "0.21"),
        (Section.POLICY, "Tone was -0.20.", ["tone"], "-0.20"),  # sign flipped
        (Section.POLICY, "Tone was 0.2.", ["tone"], "0.2"),  # rounded
        (Section.POLICY, 'It said "Inflation is elevated."', ["quote1"], "quote not in"),
        (
            Section.MARKET,
            "Yields fell -2.0 bps because of the statement.",
            ["DGS10_yield_change_[0,+1]"],
            "causal",
        ),
        (Section.MARKET, "Tone was +0.20.", ["tone"], "no market fact"),
        (
            Section.POLICY,
            "Stocks returned +0.21%.",
            ["SP500_simple_return_[0,+1]"],
            "only market facts",
        ),
    ],
)
def test_unsupported_claims_are_rejected(section, text, cited, expected) -> None:
    found = problems(section, text, cited)

    assert found and any(expected in p for p in found), found


def test_market_fact_detection() -> None:
    assert is_market_fact("SP500_simple_return_[0,+1]")
    assert is_market_fact("DGS10_yield_change_[0,+5]")
    assert not is_market_fact("tone") and not is_market_fact("dim_inflation")
