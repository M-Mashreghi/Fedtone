import pytest

from fedtone.domain.evidence import MatchKind, match_quote

SOURCE = (
    "Recent indicators suggest that economic activity has been expanding at a solid pace. "
    "Job gains have moderated since early last year but remain strong, and the unemployment "
    "rate has remained low. Inflation has eased over the past year but remains elevated.\n\n"
    "The Committee does not expect it will be appropriate to reduce the target range until it "
    "has gained greater confidence that inflation is moving sustainably toward 2 percent."
)


@pytest.mark.parametrize(
    "quote",
    [
        "Inflation has eased over the past year but remains elevated.",
        "inflation has eased over the past year",
        "greater confidence that inflation is moving sustainably toward 2 percent",
        "“Job gains have moderated since early last year”",
        "remains elevated. The Committee does not expect",  # spans a paragraph break
    ],
)
def test_real_quotes_match_exactly(quote: str) -> None:
    match = match_quote(quote, SOURCE)

    assert match.kind is MatchKind.EXACT
    assert match.supported


def test_punctuation_differences_match_fuzzily() -> None:
    match = match_quote(
        "Job gains have moderated since early last year, but remain strong and the unemployment",
        SOURCE,
    )

    assert match.kind is MatchKind.FUZZY


def test_partial_word_is_not_a_match() -> None:
    assert not match_quote("ation has eased over the past", SOURCE).supported


@pytest.mark.parametrize(
    "quote",
    [
        "The Committee expects to cut rates at its next meeting.",
        "Inflation has risen sharply over the past year and remains elevated.",
        "economic activity has been contracting at a solid pace",
        "rates higher",  # not present
        "Inflation, has eased",  # too short for punctuation-insensitive matching
        "Job gains have moderated since early last year but remain very strong",
        "   ",
    ],
)
def test_fabricated_or_altered_quotes_fail(quote: str) -> None:
    assert match_quote(quote, SOURCE).kind is MatchKind.UNSUPPORTED


def test_negation_flip_is_not_accepted() -> None:
    quote = "The Committee does expect it will be appropriate to reduce the target range"

    assert not match_quote(quote, SOURCE).supported


def tone_output(evidence, dimensions=None, overall=0.3):
    from fedtone.domain.tone_schema import ToneAnalysisOutput, label_for_score

    return ToneAnalysisOutput.model_validate(
        {
            "schema_version": "tone-analysis-1.0",
            "overall_score": overall,
            "label": label_for_score(overall),
            "confidence": 0.8,
            "dimensions": dimensions
            or [{"dimension": "inflation", "score": 0.4, "rationale": "elevated"}],
            "evidence": evidence,
            "rationale": "r",
        }
    )


def span(quote, dimension="inflation", direction="hawkish"):
    return {"quote": quote, "dimension": dimension, "direction": direction}


def test_tone_evidence_valid_when_quotes_supported_and_dimensions_covered() -> None:
    from fedtone.domain.evidence import validate_tone_evidence

    report = validate_tone_evidence(tone_output([span("remains elevated")]), SOURCE)

    assert report.valid
    assert report.supported_count == 1


def test_tone_evidence_rejects_any_fabricated_quote() -> None:
    from fedtone.domain.evidence import validate_tone_evidence

    output = tone_output([span("remains elevated"), span("inflation is accelerating rapidly")])

    report = validate_tone_evidence(output, SOURCE)

    assert not report.valid
    assert report.problems == ("unsupported quote: 'inflation is accelerating rapidly'",)


def test_tone_evidence_requires_coverage_of_material_dimensions() -> None:
    from fedtone.domain.evidence import validate_tone_evidence

    dims = [
        {"dimension": "inflation", "score": 0.4, "rationale": "x"},
        {"dimension": "labor", "score": -0.3, "rationale": "x"},
        {"dimension": "growth", "score": 0.1, "rationale": "immaterial"},
        {"dimension": "balance_sheet", "score": None, "rationale": "absent"},
    ]

    report = validate_tone_evidence(tone_output([span("remains elevated")], dims), SOURCE)

    assert report.problems == ("material dimension labor has no supported evidence",)


def test_tone_evidence_requires_at_least_one_supported_quote() -> None:
    from fedtone.domain.evidence import validate_tone_evidence

    dims = [{"dimension": "growth", "score": 0.0, "rationale": "balanced"}]

    report = validate_tone_evidence(tone_output([], dims, overall=0.0), SOURCE)

    assert report.problems == ("no supported evidence",)


def test_comparison_quotes_checked_against_the_right_document() -> None:
    from fedtone.domain.evidence import validate_comparison_evidence
    from fedtone.domain.tone_schema import ComparisonOutput

    previous = "The Committee will take into account the cumulative tightening of monetary policy."
    output = ComparisonOutput.model_validate(
        {
            "schema_version": "tone-comparison-1.0",
            "summary": "s",
            "changes": [
                {
                    "kind": "removed",
                    "dimension": "policy_guidance",
                    "direction": "dovish",
                    "previous_quote": "cumulative tightening of monetary policy",
                    "explanation": "e",
                },
                {
                    "kind": "added",
                    "dimension": "policy_guidance",
                    "direction": "dovish",
                    # Present only in the previous text, so invalid as a current quote.
                    "current_quote": "cumulative tightening of monetary policy",
                    "explanation": "e",
                },
            ],
        }
    )

    report = validate_comparison_evidence(output, SOURCE, previous)

    assert report.problems == (
        "unsupported current quote: 'cumulative tightening of monetary policy'",
    )
