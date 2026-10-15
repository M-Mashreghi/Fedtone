from datetime import UTC, date, datetime, time

import pytest

from fedtone.domain.calendar import (
    NEW_YORK,
    AlignmentRule,
    align_event,
    calendar,
    easter_sunday,
)
from fedtone.domain.metrics import WindowSpec, window_dates

NYSE = calendar("nyse")
TREASURY = calendar("us_treasury")
CLOSE = time(16, 0)


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2000, date(2000, 4, 23)),
        (2019, date(2019, 4, 21)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2038, date(2038, 4, 25)),
    ],
)
def test_easter_sunday(year: int, expected: date) -> None:
    assert easter_sunday(year) == expected


def test_nyse_2024_holidays() -> None:
    holidays = [
        date(2024, 1, 1),
        date(2024, 1, 15),
        date(2024, 2, 19),
        date(2024, 3, 29),
        date(2024, 5, 27),
        date(2024, 6, 19),
        date(2024, 7, 4),
        date(2024, 9, 2),
        date(2024, 11, 28),
        date(2024, 12, 25),
    ]
    for day in holidays:
        assert not NYSE.is_trading_day(day), day
    # Columbus Day and Veterans Day are NYSE trading days.
    assert NYSE.is_trading_day(date(2024, 10, 14))
    assert NYSE.is_trading_day(date(2024, 11, 11))
    trading_days = sum(
        NYSE.is_trading_day(date.fromordinal(o))
        for o in range(date(2024, 1, 1).toordinal(), date(2024, 12, 31).toordinal() + 1)
    )
    assert trading_days == 252


@pytest.mark.parametrize(
    ("closed", "open_"),
    [
        # Sunday holidays move to Monday.
        (date(2022, 6, 20), date(2022, 6, 17)),
        (date(2022, 12, 26), date(2022, 12, 23)),
        (date(2021, 7, 5), date(2021, 7, 2)),
        # Saturday holidays move to Friday.
        (date(2021, 12, 24), date(2021, 12, 23)),
        (date(2026, 7, 3), date(2026, 7, 2)),
        # One-off closures.
        (date(2018, 12, 5), date(2018, 12, 4)),
        (date(2025, 1, 9), date(2025, 1, 8)),
    ],
)
def test_nyse_weekend_rules_and_special_closures(closed: date, open_: date) -> None:
    assert not NYSE.is_trading_day(closed)
    assert NYSE.is_trading_day(open_)


def test_new_years_day_on_saturday_is_not_observed() -> None:
    assert NYSE.is_trading_day(date(2021, 12, 31))
    assert NYSE.is_trading_day(date(2022, 1, 3))


def test_juneteenth_only_from_2022() -> None:
    assert NYSE.is_trading_day(date(2021, 6, 18))
    assert not NYSE.is_trading_day(date(2023, 6, 19))


def test_treasury_calendar_adds_columbus_and_veterans_day() -> None:
    assert not TREASURY.is_trading_day(date(2024, 10, 14))
    assert not TREASURY.is_trading_day(date(2024, 11, 11))
    assert not TREASURY.is_trading_day(date(2023, 11, 10))  # Veterans Day on Saturday
    assert not TREASURY.is_trading_day(date(2024, 3, 29))
    # NYSE one-off closures are not part of the Treasury calendar.
    assert TREASURY.is_trading_day(date(2025, 1, 9))


def test_nyse_early_closes() -> None:
    assert NYSE.early_close(date(2024, 7, 3)) == time(13, 0)
    assert NYSE.early_close(date(2024, 11, 29)) == time(13, 0)
    assert NYSE.early_close(date(2024, 12, 24)) == time(13, 0)
    assert NYSE.early_close(date(2024, 12, 23)) is None
    assert TREASURY.early_close(date(2024, 11, 29)) is None
    assert NYSE.observation_at(date(2024, 11, 29), CLOSE).time() == time(13, 0)


def test_shift_skips_weekends_and_holidays() -> None:
    assert NYSE.shift(date(2024, 3, 28), 1) == date(2024, 4, 1)  # over Good Friday
    assert NYSE.shift(date(2024, 4, 1), -1) == date(2024, 3, 28)
    assert NYSE.shift(date(2024, 1, 31), 5) == date(2024, 2, 7)
    with pytest.raises(ValueError):
        NYSE.shift(date(2024, 3, 29), 1)


def _ny(*args) -> datetime:
    return datetime(*args, tzinfo=NEW_YORK)


def test_fomc_release_before_the_close_is_same_session() -> None:
    released = datetime(2024, 1, 31, 19, 0, tzinfo=UTC)  # 14:00 EST
    alignment = align_event(released, NYSE, CLOSE)
    assert alignment.event_day == date(2024, 1, 31)
    assert alignment.rule is AlignmentRule.BEFORE_OBSERVATION
    assert alignment.released_local == _ny(2024, 1, 31, 14, 0)


def test_release_after_a_noon_fixing_moves_to_next_session() -> None:
    released = datetime(2024, 1, 31, 19, 0, tzinfo=UTC)
    alignment = align_event(released, TREASURY, time(12, 0))
    assert alignment.event_day == date(2024, 2, 1)
    assert alignment.rule is AlignmentRule.AFTER_OBSERVATION


def test_release_exactly_at_the_close_counts_as_after() -> None:
    alignment = align_event(_ny(2024, 1, 31, 16, 0), NYSE, CLOSE)
    assert alignment.event_day == date(2024, 2, 1)


def test_release_on_weekend_or_holiday_moves_to_next_session() -> None:
    weekend = align_event(_ny(2024, 2, 3, 10, 0), NYSE, CLOSE)
    holiday = align_event(_ny(2024, 3, 29, 10, 0), NYSE, CLOSE)
    assert weekend.event_day == date(2024, 2, 5)
    assert holiday.event_day == date(2024, 4, 1)
    assert weekend.rule is holiday.rule is AlignmentRule.NON_TRADING_DAY


def test_release_after_an_early_close_moves_to_next_session() -> None:
    alignment = align_event(_ny(2024, 11, 29, 13, 30), NYSE, CLOSE)
    assert alignment.event_day == date(2024, 12, 2)


def test_daylight_saving_time_is_respected() -> None:
    # 18:00 UTC is 14:00 EDT on 2024-03-20 but would be 13:00 EST.
    alignment = align_event(datetime(2024, 3, 20, 18, 0, tzinfo=UTC), NYSE, CLOSE)
    assert alignment.released_local.hour == 14
    assert alignment.event_day == date(2024, 3, 20)


def test_naive_release_time_is_rejected() -> None:
    with pytest.raises(ValueError):
        align_event(datetime(2024, 1, 31, 14, 0), NYSE, CLOSE)


@pytest.mark.parametrize(
    ("window", "start", "end"),
    [
        ("[0,0]", date(2024, 3, 26), date(2024, 3, 27)),
        ("[0,+1]", date(2024, 3, 26), date(2024, 3, 28)),
        ("[0,+5]", date(2024, 3, 26), date(2024, 4, 4)),  # Good Friday skipped
        ("[-1,+1]", date(2024, 3, 25), date(2024, 3, 28)),
    ],
)
def test_window_boundaries(window: str, start: date, end: date) -> None:
    alignment = align_event(_ny(2024, 3, 27, 14, 0), NYSE, CLOSE)
    dates = window_dates(alignment, WindowSpec.parse(window), NYSE)
    assert (dates.start_date, dates.end_date) == (start, end)


def test_window_spec_names_and_validation() -> None:
    assert [WindowSpec.parse(t).name for t in ("[0,0]", "[0,+1]", "[-1,+5]")] == [
        "[0,0]",
        "[0,+1]",
        "[-1,+5]",
    ]
    with pytest.raises(ValueError):
        WindowSpec(2, 1)
