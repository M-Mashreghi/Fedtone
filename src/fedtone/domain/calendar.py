"""Rule-based U.S. trading calendars and announcement-to-session alignment.

Two calendars are provided:

- ``nyse``: NYSE full-day holidays (New Year's Day, Martin Luther King Jr. Day, Washington's
  Birthday, Good Friday, Memorial Day, Juneteenth from 2022, Independence Day, Labor Day,
  Thanksgiving, Christmas) with NYSE weekend rules (Saturday holidays move to Friday except
  New Year's Day, Sunday holidays move to Monday), listed one-off closures since 2001, and
  13:00 early closes on July 3, the day after Thanksgiving and December 24.
- ``us_treasury``: the NYSE holiday rules plus Columbus Day and Veterans Day, as observed by
  the U.S. government bond market and the H.15/H.10 releases. One-off closures are not
  modeled; a missing provider value on such a day shows up as an explicit missing
  observation instead.

The calendars are exact for the rules above; they are not a full exchange-calendar service.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from functools import lru_cache
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
EARLY_CLOSE = time(13, 0)

# NYSE closures outside the regular holiday rules (national days of mourning, 9/11, Sandy).
NYSE_SPECIAL_CLOSURES = frozenset(
    {
        date(2001, 9, 11),
        date(2001, 9, 12),
        date(2001, 9, 13),
        date(2001, 9, 14),
        date(2004, 6, 11),
        date(2007, 1, 2),
        date(2012, 10, 29),
        date(2012, 10, 30),
        date(2018, 12, 5),
        date(2025, 1, 9),
    }
)


def easter_sunday(year: int) -> date:
    """Gregorian Easter (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    following = date(year + month // 12, month % 12 + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date, *, saturday_to_friday: bool = True) -> date | None:
    if day.weekday() == 5:
        return day - timedelta(days=1) if saturday_to_friday else None
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


MONDAY, THURSDAY = 0, 3


def _nyse_rule_holidays(year: int) -> set[date]:
    days = {
        _observed(date(year, 1, 1), saturday_to_friday=False),
        _nth_weekday(year, 1, MONDAY, 3),
        _nth_weekday(year, 2, MONDAY, 3),
        easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, MONDAY),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, MONDAY, 1),
        _nth_weekday(year, 11, THURSDAY, 4),
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))
    # New Year's Day of the following year falling on Saturday is not observed on Dec 31.
    days.discard(None)
    return days


class CalendarName(StrEnum):
    NYSE = "nyse"
    US_TREASURY = "us_treasury"


@lru_cache(maxsize=256)
def _holidays(name: CalendarName, year: int) -> frozenset[date]:
    days = _nyse_rule_holidays(year)
    if name is CalendarName.NYSE:
        days |= {d for d in NYSE_SPECIAL_CLOSURES if d.year == year}
    else:
        days.add(_nth_weekday(year, 10, MONDAY, 2))
        days.add(_observed(date(year, 11, 11)))
    return frozenset(days)


@dataclass(frozen=True)
class TradingCalendar:
    name: CalendarName

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in _holidays(self.name, day.year)

    def early_close(self, day: date) -> time | None:
        """NYSE 13:00 early close, if ``day`` is an early-close session."""
        if self.name is not CalendarName.NYSE or not self.is_trading_day(day):
            return None
        thanksgiving = _nth_weekday(day.year, 11, THURSDAY, 4)
        if day in {date(day.year, 7, 3), thanksgiving + timedelta(days=1), date(day.year, 12, 24)}:
            return EARLY_CLOSE
        return None

    def next_trading_day(self, day: date) -> date:
        candidate = day + timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    def previous_trading_day(self, day: date) -> date:
        candidate = day - timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate

    def shift(self, day: date, sessions: int) -> date:
        """The trading day ``sessions`` sessions after (or before, if negative) ``day``.

        ``day`` itself must be a trading day.
        """
        if not self.is_trading_day(day):
            raise ValueError(f"{day} is not a {self.name} trading day")
        for _ in range(abs(sessions)):
            day = self.next_trading_day(day) if sessions > 0 else self.previous_trading_day(day)
        return day

    def observation_at(self, day: date, observation_time: time) -> datetime:
        """When the daily value for ``day`` is fixed, as an aware New York datetime."""
        early = self.early_close(day)
        cutoff = min(observation_time, early) if early else observation_time
        return datetime.combine(day, cutoff, tzinfo=NEW_YORK)


def calendar(name: str) -> TradingCalendar:
    return TradingCalendar(CalendarName(name))


class AlignmentRule(StrEnum):
    BEFORE_OBSERVATION = "released_before_same_day_observation"
    AFTER_OBSERVATION = "released_after_observation_next_session"
    NON_TRADING_DAY = "released_on_non_trading_day_next_session"


@dataclass(frozen=True)
class EventAlignment:
    """Maps an announcement to its event session T for one instrument.

    T is the first session whose daily observation is fixed after the announcement, so the
    close of T-1 is the last observation that cannot contain the announcement.
    """

    released_at: datetime
    released_local: datetime
    event_day: date
    rule: AlignmentRule
    calendar: CalendarName


def align_event(
    released_at: datetime, trading_calendar: TradingCalendar, observation_time: time
) -> EventAlignment:
    if released_at.utcoffset() is None:
        raise ValueError("released_at must be timezone-aware")
    local = released_at.astimezone(NEW_YORK)
    day = local.date()
    if not trading_calendar.is_trading_day(day):
        event_day, rule = trading_calendar.next_trading_day(day), AlignmentRule.NON_TRADING_DAY
    elif local < trading_calendar.observation_at(day, observation_time):
        event_day, rule = day, AlignmentRule.BEFORE_OBSERVATION
    else:
        event_day, rule = trading_calendar.next_trading_day(day), AlignmentRule.AFTER_OBSERVATION
    return EventAlignment(released_at, local, event_day, rule, trading_calendar.name)
