import pytest

from fedtone.domain.diff import diff_statements, split_sentences, tone_delta

PREVIOUS = [
    "Recent indicators suggest that growth of economic activity has slowed. "
    "Job gains have moderated but remain strong. Inflation remains elevated.",
    "In determining the extent of any additional policy firming that may be appropriate, the "
    "Committee will take into account the cumulative tightening of monetary policy.",
    "The Committee is strongly committed to returning inflation to its 2 percent objective.",
]
CURRENT = [
    "Recent indicators suggest that economic activity has been expanding at a solid pace. "
    "Job gains have moderated but remain strong. Inflation remains elevated.",
    "The Committee judges that the risks to achieving its goals are moving into better balance.",
    "The Committee is strongly committed to returning inflation to its 2 percent objective.",
]


def test_sentence_split_respects_abbreviations() -> None:
    text = [
        "The U.S. economy grew. Inflation, at 3.1 percent, eased. Rates were held at 2 p.m. today."
    ]

    assert split_sentences(text) == [
        "The U.S. economy grew.",
        "Inflation, at 3.1 percent, eased.",
        "Rates were held at 2 p.m. today.",
    ]


def test_known_fixture_diff_detects_intended_changes() -> None:
    diff = diff_statements(PREVIOUS, CURRENT)

    assert diff.unchanged == (
        "Job gains have moderated but remain strong.",
        "Inflation remains elevated.",
        "The Committee is strongly committed to returning inflation to its 2 percent objective.",
    )
    assert [c.current for c in diff.changed] == [
        "Recent indicators suggest that economic activity has been expanding at a solid pace."
    ]
    assert diff.removed == (
        "In determining the extent of any additional policy firming that may be appropriate, the "
        "Committee will take into account the cumulative tightening of monetary policy.",
    )
    assert diff.added == (CURRENT[1],)
    assert diff.has_changes
    assert 0 < diff.similarity < 1


def test_identical_statements_have_no_changes() -> None:
    diff = diff_statements(PREVIOUS, PREVIOUS)

    assert not diff.has_changes
    assert diff.similarity == 1.0


def test_diff_is_deterministic_and_serializable() -> None:
    first, second = diff_statements(PREVIOUS, CURRENT), diff_statements(PREVIOUS, CURRENT)

    assert first == second
    assert first.to_dict()["version"] == "sentence-diff-1"


def test_tone_delta_from_stored_scores() -> None:
    delta = tone_delta(
        current_overall=0.1,
        previous_overall=0.4,
        current_dimensions={"inflation": 0.3, "labor": -0.2, "balance_sheet": None},
        previous_dimensions={"inflation": 0.5, "labor": None, "balance_sheet": 0.1},
    )

    assert delta.overall == pytest.approx(-0.3)
    assert delta.dimensions["inflation"] == pytest.approx(-0.2)
    assert delta.dimensions["labor"] is None  # missing previous dimension is not imputed
    assert delta.dimensions["balance_sheet"] is None


def test_tone_delta_without_previous_is_baseline() -> None:
    delta = tone_delta(0.2, None, {"inflation": 0.3}, None)

    assert delta.overall is None
    assert delta.dimensions == {"inflation": None}
