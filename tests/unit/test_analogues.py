import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from fedtone.domain.analogues import MeetingFeatures, distance, find_analogues

T0 = datetime(2024, 1, 31, 19, 0, tzinfo=UTC)


def meeting(n: int, days: int, overall: float, inflation: float | None = None, delta=None):
    return MeetingFeatures(
        document_id=UUID(int=n),
        published_at=T0 + timedelta(days=days),
        overall=overall,
        dimensions={"inflation": inflation, "labor": None},
        overall_delta=delta,
    )


TARGET = meeting(0, 0, 0.40, inflation=0.50, delta=0.20)
HISTORY = [
    meeting(1, -400, 0.38, inflation=0.50, delta=0.20),  # very close
    meeting(2, -300, 0.10, inflation=0.10, delta=-0.10),
    meeting(3, -200, 0.45, inflation=0.40, delta=0.10),
    meeting(4, -100, -0.50, inflation=-0.40, delta=-0.60),
    meeting(5, -50, 0.42, inflation=0.50),  # no delta available
]
FUTURE = meeting(9, 45, 0.40, inflation=0.50, delta=0.20)  # identical, but later


def test_distance_is_rms_over_shared_features() -> None:
    d, shared = distance(TARGET, HISTORY[2])
    assert shared == ("delta", "dim:inflation", "overall")
    expected = math.sqrt(
        ((0.20 - 0.10) / 2) ** 2 / 3 + (0.5 - 0.4) ** 2 / 3 + (0.4 - 0.45) ** 2 / 3
    )
    assert d == pytest.approx(expected)


def test_fixed_dataset_gives_deterministic_ranking() -> None:
    first = find_analogues(TARGET, HISTORY, top_k=3)
    second = find_analogues(TARGET, list(reversed(HISTORY)), top_k=3)
    ranking = [m.document_id.int for m in first.matches]
    assert ranking == [1, 5, 3]
    assert [m.document_id.int for m in second.matches] == ranking
    assert first.sample_size == 5
    assert not first.insufficient_history


def test_future_meetings_are_excluded() -> None:
    result = find_analogues(TARGET, [*HISTORY, FUTURE], top_k=10)
    ids = {m.document_id.int for m in result.matches}
    assert 9 not in ids
    assert result.sample_size == len(HISTORY)
    assert all(m.published_at < TARGET.published_at for m in result.matches)


def test_earlier_cutoff_for_backtests_and_later_cutoff_rejected() -> None:
    result = find_analogues(TARGET, HISTORY, cutoff=T0 - timedelta(days=250))
    assert {m.document_id.int for m in result.matches} == {1, 2}
    assert result.insufficient_history
    with pytest.raises(ValueError):
        find_analogues(TARGET, HISTORY, cutoff=T0 + timedelta(days=1))


def test_ties_break_by_recency_then_id() -> None:
    a = meeting(7, -10, 0.40, inflation=0.50, delta=0.20)
    b = meeting(6, -20, 0.40, inflation=0.50, delta=0.20)
    c = meeting(8, -10, 0.40, inflation=0.50, delta=0.20)
    result = find_analogues(TARGET, [b, c, a])
    assert [m.document_id.int for m in result.matches] == [7, 8, 6]


def test_target_itself_is_never_its_own_analogue() -> None:
    result = find_analogues(TARGET, [TARGET, *HISTORY])
    assert all(m.document_id != TARGET.document_id for m in result.matches)


@pytest.mark.parametrize("kwargs", [{"top_k": 0}, {"min_sample": 0}])
def test_invalid_parameters(kwargs) -> None:
    with pytest.raises(ValueError):
        find_analogues(TARGET, HISTORY, **kwargs)
