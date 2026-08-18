from datetime import datetime, timezone

import pytest

from engine import bars_range, latest_bar, parse_timeframe
from engine.generator import Timeframe

NOW = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)  # Friday after the close
JUL1 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def get(symbol, tf, start, end, **kw):
    bars, _ = bars_range(symbol, parse_timeframe(tf), start, end, now=NOW, **kw)
    return bars


def test_timeframe_grammar():
    assert parse_timeframe("15Min") == Timeframe(15, "Min")
    assert parse_timeframe("1day") == Timeframe(1, "Day")
    for bad in ("0Min", "60Min", "24Hour", "2Day", "5Month", "fortnight"):
        with pytest.raises(ValueError):
            parse_timeframe(bad)


def test_determinism_and_seed():
    a = get("AAPL", "1Day", JUL1, NOW)
    b = get("AAPL", "1Day", JUL1, NOW)
    assert a == b and len(a) > 20
    other = get("AAPL", "1Day", JUL1, NOW, seed="42")
    assert other != a  # alternate universe


def test_no_bars_outside_calendar():
    bars = get("MSFT", "1Day", utc(2026, 7, 1), utc(2026, 7, 31, 23, 59))
    days = {b["t"][:10] for b in bars}
    assert "2026-07-03" not in days  # July 4 observed
    assert "2026-07-04" not in days and "2026-07-05" not in days  # weekend
    assert "2026-07-06" in days


def test_intraday_confined_to_rth():
    bars = get("MSFT", "30Min", utc(2026, 8, 10), utc(2026, 8, 11))
    assert len(bars) == 13  # 6.5 hours / 30 min
    assert bars[0]["t"] == "2026-08-10T13:30:00Z"   # 09:30 EDT
    assert bars[-1]["t"] == "2026-08-10T19:30:00Z"  # last bucket starts 15:30 EDT


def test_only_completed_buckets():
    mid_session = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)  # 11:00 EDT Friday
    bars, _ = bars_range("MSFT", parse_timeframe("1Day"), JUL1, mid_session, now=mid_session)
    assert bars[-1]["t"][:10] == "2026-08-13"  # today's partial day absent


def test_limit_and_descending():
    asc, truncated = bars_range("SPY", parse_timeframe("1Day"), JUL1, NOW, max_bars=5, now=NOW)
    assert len(asc) == 5 and truncated
    assert asc[0]["t"] < asc[-1]["t"]
    desc, _ = bars_range(
        "SPY", parse_timeframe("1Day"), JUL1, NOW, max_bars=5, descending=True, now=NOW
    )
    assert desc[0]["t"] > desc[-1]["t"]
    assert desc[0]["t"][:10] == "2026-08-14"


def coherence_check(symbol, day_str):
    day_start = datetime.fromisoformat(day_str + "T00:00:00+00:00")
    day_end = datetime.fromisoformat(day_str + "T23:59:00+00:00")
    minutes = get(symbol, "1Min", day_start, day_end)
    (day,) = [b for b in get(symbol, "1Day", day_start, day_end) if b["t"][:10] == day_str]
    assert day["o"] == minutes[0]["o"]
    assert day["c"] == minutes[-1]["c"]
    assert day["h"] == max(b["h"] for b in minutes)
    assert day["l"] == min(b["l"] for b in minutes)
    assert day["v"] == sum(b["v"] for b in minutes)
    assert day["n"] == sum(b["n"] for b in minutes)
    hours = get(symbol, "1Hour", day_start, day_end)
    assert day["h"] == max(b["h"] for b in hours)
    assert day["v"] == sum(b["v"] for b in hours)


@pytest.mark.parametrize("symbol", ["AAPL", "ZZZZ", "HALTS", "GAPPY", "PENNY"])
def test_cross_timeframe_coherence(symbol):
    coherence_check(symbol, "2026-07-06")


def test_week_and_month_aggregate_from_days():
    days = get("NVDA", "1Day", utc(2026, 6, 1), utc(2026, 7, 1))
    weeks = get("NVDA", "1Week", utc(2026, 6, 1), utc(2026, 6, 14))
    assert len(weeks) == 2  # Jun 1-5 and Jun 8-12 both complete inside the window
    week = weeks[0]
    assert week["t"][:10] == "2026-06-01"
    week_days = [b for b in days if "2026-06-01" <= b["t"][:10] <= "2026-06-05"]
    assert week["o"] == week_days[0]["o"]
    assert week["c"] == week_days[-1]["c"]
    assert week["v"] == sum(b["v"] for b in week_days)

    months = get("NVDA", "1Month", utc(2026, 6, 1), utc(2026, 7, 1))
    (month,) = months
    june_days = [b for b in days if b["t"][:7] == "2026-06"]
    assert month["o"] == june_days[0]["o"]
    assert month["c"] == june_days[-1]["c"]
    assert month["v"] == sum(b["v"] for b in june_days)


def test_partial_periods_are_dropped():
    # Window starts Wednesday, clipping the week of Jun 1-5: that week is
    # dropped rather than served partial; the complete Jun 8-12 week remains.
    weeks = get("NVDA", "1Week", utc(2026, 6, 3), utc(2026, 6, 14))
    assert [w["t"][:10] for w in weeks] == ["2026-06-08"]


def test_curated_prices_stay_plausible():
    bars = get("AAPL", "1Day", utc(2026, 1, 2), utc(2026, 2, 1))
    assert all(120 < b["c"] < 450 for b in bars)
    bars = get("PENNY", "1Day", utc(2026, 7, 1), utc(2026, 8, 1))
    assert all(b["c"] < 2 for b in bars)


def test_bar_shape_and_sanity():
    bars = get("GOOGL", "1Day", JUL1, NOW)
    for b in bars:
        assert set(b) == {"c", "h", "l", "n", "o", "t", "v", "vw"}
        assert b["l"] <= min(b["o"], b["c"]) and b["h"] >= max(b["o"], b["c"])
        assert b["v"] > 0 and b["n"] > 0
        assert b["l"] <= b["vw"] <= b["h"]


def test_latest_bar():
    bar = latest_bar("AAPL", parse_timeframe("1Day"), now=NOW)
    assert bar["t"][:10] == "2026-08-14"
    bar = latest_bar("AAPL", parse_timeframe("1Min"), now=NOW)
    assert bar["t"] == "2026-08-14T19:59:00Z"
