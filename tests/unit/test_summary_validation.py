import pytest
from pydantic import ValidationError

from fedtone.domain.summary import Fact, SummaryOutput, validate_summary

FACTS = [
    Fact("release", "statement release date", "2024-01-31"),
    Fact("tone", "overall tone score (-1 dovish to +1 hawkish)", "+0.35"),
    Fact("delta", "overall tone change versus previous statement", "-0.15"),
    Fact("SP500_simple_return_[0,+1]", "S&P 500 index return over window [0,+1]", "-0.38%"),
    Fact(
        "DGS10_yield_change_[0,+1]",
        "10-Year Treasury yield yield change over window [0,+1]",
        "-19.0 bps",
    ),
    Fact(
        "DGS2_yield_change_[0,+5]",
        "2-Year Treasury yield yield change over window [0,+5]",
        "not available (no_observation)",
    ),
    Fact(
        "quote1", "evidence quote (inflation)", "Inflation has eased but remains above 2 percent."
    ),
]


def output(summary: str, headline: str = "Tone turned less hawkish", cited=None) -> SummaryOutput:
    return SummaryOutput(
        schema_version="research-summary-1.0",
        headline=headline,
        summary=summary,
        cited_fact_ids=cited or ["tone", "SP500_simple_return_[0,+1]"],
    )


def test_summary_that_copies_canonical_values_passes() -> None:
    text = (
        "The 2024-01-31 statement scored +0.35, a change of -0.15 from the previous one. "
        "Over the [0,+1] window the S&P 500 return was -0.38% and the 10-Year Treasury "
        "yield moved -19.0 bps. The statement said inflation remains above 2 percent. "
        "The 2-Year yield change is not available."
    )
    assert validate_summary(output(text), FACTS) == []


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("The S&P 500 fell 0.75% after the statement.", "0.75"),  # invented number
        ("The S&P 500 return was -0.4%.", "0.4"),  # rounded instead of copied
        ("The S&P 500 return was +0.38%.", "+0.38"),  # sign flipped
        ("Yields moved -38.0 bps over two days.", "38.0"),  # recomputed
        ("The 2-Year yield rose 12.5 bps.", "12.5"),  # filled a missing metric
    ],
)
def test_unsupported_number_injection_fails(text: str, problem: str) -> None:
    problems = validate_summary(output(text), FACTS)
    assert any(problem in p for p in problems), problems


@pytest.mark.parametrize(
    "text",
    [
        "The hawkish statement caused the S&P 500 return of -0.38%.",
        "Equities fell due to the statement.",
        "The statement drove yields lower.",
        "The decision led to a -19.0 bps move.",
        "Stocks dropped because of the guidance.",
    ],
)
def test_causal_language_is_rejected(text: str) -> None:
    assert any("causal" in p for p in validate_summary(output(text), FACTS))


def test_association_language_is_accepted() -> None:
    text = "The S&P 500 return of -0.38% coincided with the release and followed the statement."
    assert validate_summary(output(text), FACTS) == []


def test_unknown_fact_ids_are_rejected() -> None:
    problems = validate_summary(output("No numbers here.", cited=["tone", "made_up"]), FACTS)
    assert problems == ["unknown fact ids: made_up"]


def test_numbers_in_the_headline_are_checked_too() -> None:
    problems = validate_summary(output("Fine.", headline="Stocks slid 3%"), FACTS)
    assert problems == ["number not in facts: 3"]


def test_schema_rejects_extra_fields_and_empty_citations() -> None:
    with pytest.raises(ValidationError):
        SummaryOutput.model_validate(
            {
                "schema_version": "research-summary-1.0",
                "headline": "h",
                "summary": "s",
                "cited_fact_ids": [],
            }
        )
    with pytest.raises(ValidationError):
        SummaryOutput.model_validate(
            {
                "schema_version": "research-summary-1.0",
                "headline": "h",
                "summary": "s",
                "cited_fact_ids": ["tone"],
                "sp500_return": 0.01,
            }
        )
