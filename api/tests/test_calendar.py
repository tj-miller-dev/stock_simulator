from datetime import date

from engine.market_calendar import (
    holidays,
    is_trading_day,
    next_trading_day,
    prev_trading_day,
    session_close_utc,
    session_open_utc,
)


def test_2026_holidays():
    h = holidays(2026)
    assert date(2026, 1, 1) in h          # New Year's (Thursday)
    assert date(2026, 1, 19) in h         # MLK Day
    assert date(2026, 2, 16) in h         # Washington's Birthday
    assert date(2026, 4, 3) in h          # Good Friday (Easter 2026-04-05)
    assert date(2026, 5, 25) in h         # Memorial Day
    assert date(2026, 6, 19) in h         # Juneteenth (Friday)
    assert date(2026, 7, 3) in h          # July 4 observed (Sat -> Fri)
    assert date(2026, 9, 7) in h          # Labor Day
    assert date(2026, 11, 26) in h        # Thanksgiving
    assert date(2026, 12, 25) in h        # Christmas (Friday)


def test_new_years_saturday_not_observed_friday():
    # NYSE Rule 7.2: Jan 1 2022 was a Saturday; Friday Dec 31 2021 traded.
    assert is_trading_day(date(2021, 12, 31))
    assert date(2021, 12, 31) not in holidays(2021)


def test_juneteenth_only_from_2022():
    assert date(2021, 6, 18) not in holidays(2021)  # Jun 19 2021 was a Saturday
    assert date(2023, 6, 19) in holidays(2023)


def test_weekends_and_navigation():
    assert not is_trading_day(date(2026, 8, 15))  # Saturday
    assert not is_trading_day(date(2026, 8, 16))  # Sunday
    assert is_trading_day(date(2026, 8, 17))      # Monday
    assert next_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)  # skips Jul 3 + weekend
    assert prev_trading_day(date(2026, 7, 6)) == date(2026, 7, 2)


def test_session_times_dst_and_standard():
    # August: EDT, open = 13:30Z. January: EST, open = 14:30Z.
    assert session_open_utc(date(2026, 8, 14)).strftime("%H:%M") == "13:30"
    assert session_close_utc(date(2026, 8, 14)).strftime("%H:%M") == "20:00"
    assert session_open_utc(date(2026, 1, 5)).strftime("%H:%M") == "14:30"
    assert session_close_utc(date(2026, 1, 5)).strftime("%H:%M") == "21:00"
