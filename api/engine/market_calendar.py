"""NYSE trading calendar: weekends, full-closure holidays, session times.

Hand-rolled rather than a dependency: the rules are small, stable, and having
them in ~100 auditable lines beats shipping a calendar package into the image.
Deliberately NOT modeled (documented in the API docs): half-days trade as full
sessions, and historical one-off closures (9/11, Hurricane Sandy, mourning
days) are open days here. Synthetic data doesn't need a perfect past -- it
needs a *plausible* one, cheaply.
"""

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
MINUTES_PER_SESSION = 390


def _easter(year: int) -> date:
    # Anonymous Gregorian computus.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:  # Saturday -> Friday before
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday -> Monday after
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def holidays(year: int) -> frozenset[date]:
    days = set()
    # NYSE Rule 7.2: when Jan 1 falls on Saturday the market does NOT close
    # on the preceding Friday (that Friday is the prior year's last session).
    new_years = date(year, 1, 1)
    if new_years.weekday() != 5:
        days.add(_observed(new_years))
    days.add(_nth_weekday(year, 1, 0, 3))    # MLK Day (Mon)
    days.add(_nth_weekday(year, 2, 0, 3))    # Washington's Birthday (Mon)
    days.add(_easter(year) - timedelta(days=2))  # Good Friday
    days.add(_last_weekday(year, 5, 0))      # Memorial Day (Mon)
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days.add(_observed(date(year, 7, 4)))    # Independence Day
    days.add(_nth_weekday(year, 9, 0, 1))    # Labor Day (Mon)
    days.add(_nth_weekday(year, 11, 3, 4))   # Thanksgiving (Thu)
    days.add(_observed(date(year, 12, 25)))  # Christmas
    return frozenset(days)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


@lru_cache(maxsize=64)
def trading_days_of_year(year: int) -> tuple[date, ...]:
    d = date(year, 1, 1)
    end = date(year, 12, 31)
    out = []
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def session_open_utc(d: date) -> datetime:
    return datetime.combine(d, SESSION_OPEN, tzinfo=ET).astimezone(timezone.utc)


def session_close_utc(d: date) -> datetime:
    return datetime.combine(d, SESSION_CLOSE, tzinfo=ET).astimezone(timezone.utc)


def midnight_et_utc(d: date) -> datetime:
    """Alpaca's timestamp convention for daily-and-coarser bars: midnight
    Eastern on the period's first day, expressed in UTC."""
    return datetime.combine(d, time(0, 0), tzinfo=ET).astimezone(timezone.utc)
