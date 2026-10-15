import json

import pytest
from pydantic import ValidationError

from fedtone.domain.models import ToneClassification
from fedtone.domain.tone_schema import (
    ComparisonOutput,
    ToneAnalysisOutput,
    label_for_score,
)


def valid_payload(**overrides) -> dict:
    payload = {
        "schema_version": "tone-analysis-1.0",
        "overall_score": 0.3,
        "label": "hawkish",
        "confidence": 0.8,
        "dimensions": [
            {"dimension": "inflation", "score": 0.5, "rationale": "Inflation remains elevated."},
            {"dimension": "balance_sheet", "score": None, "rationale": "Not addressed."},
        ],
        "evidence": [
            {
                "quote": "Inflation remains elevated",
                "dimension": "inflation",
                "direction": "hawkish",
            }
        ],
        "rationale": "Elevated inflation language.",
        "uncertainty_flags": [],
    }
    payload.update(overrides)
    return payload


def test_valid_json_is_accepted() -> None:
    output = ToneAnalysisOutput.model_validate_json(json.dumps(valid_payload()))

    assert output.label is ToneClassification.HAWKISH
    assert output.dimension_scores() == {
        "inflation": 0.5,
        "labor": None,
        "growth": None,
        "policy_guidance": None,
        "balance_sheet": None,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"overall_score": 1.2, "label": "strongly_hawkish"},
        {"overall_score": -1.01, "label": "strongly_dovish"},
        {"confidence": 1.5},
        {"confidence": -0.2},
        {"label": "very_hawkish"},
        {"schema_version": "tone-analysis-0.9"},
        {"dimensions": []},
        {"dimensions": [{"dimension": "fx", "score": 0.1, "rationale": "x"}]},
        {"dimensions": [{"dimension": "labor", "score": 2.0, "rationale": "x"}]},
        {
            "dimensions": [
                {"dimension": "labor", "score": 0.1, "rationale": "x"},
                {"dimension": "labor", "score": 0.2, "rationale": "y"},
            ]
        },
        {"evidence": [{"quote": "", "dimension": "labor", "direction": "hawkish"}]},
        {"evidence": [{"quote": "abc def", "dimension": "labor", "direction": "up"}]},
        {"rationale": ""},
        {"unexpected_field": True},
        {"label": "neutral"},  # inconsistent with overall_score 0.3
    ],
)
def test_malformed_or_out_of_range_values_are_rejected(overrides) -> None:
    with pytest.raises(ValidationError):
        ToneAnalysisOutput.model_validate(valid_payload(**overrides))


@pytest.mark.parametrize("raw", ["not json", "{", "[]", '{"schema_version": "tone-analysis-1.0"}'])
def test_unparseable_json_is_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        ToneAnalysisOutput.model_validate_json(raw)


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (-1.0, "strongly_dovish"),
        (-0.6, "dovish"),
        (-0.21, "dovish"),
        (-0.2, "neutral"),
        (0.0, "neutral"),
        (0.2, "hawkish"),
        (0.59, "hawkish"),
        (0.6, "strongly_hawkish"),
        (1.0, "strongly_hawkish"),
    ],
)
def test_label_bands(score: float, label: str) -> None:
    assert label_for_score(score) == label


def test_comparison_change_quotes_must_match_kind() -> None:
    base = {"schema_version": "tone-comparison-1.0", "summary": "s", "uncertainty_flags": []}
    added = {"kind": "added", "dimension": "labor", "direction": "dovish", "explanation": "e"}

    with pytest.raises(ValidationError, match="current_quote"):
        ComparisonOutput.model_validate({**base, "changes": [added]})
    with pytest.raises(ValidationError, match="previous_quote"):
        ComparisonOutput.model_validate(
            {**base, "changes": [{**added, "kind": "softened", "current_quote": "now"}]}
        )
    ok = ComparisonOutput.model_validate(
        {**base, "changes": [{**added, "current_quote": "new text"}]}
    )
    assert ok.changes[0].previous_quote is None
