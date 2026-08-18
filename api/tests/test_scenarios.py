"""Every magic ticker must show its signature inside any 30-day window.
These tests use an arbitrary window (nothing special about July 2026) --
if a scenario only performs in hand-picked months, it fails its contract."""

from datetime import datetime, timezone

from engine import bars_range, parse_timeframe

NOW = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
WINDOW = (datetime(2026, 7, 5, tzinfo=timezone.utc), datetime(2026, 8, 4, tzinfo=timezone.utc))


def daily(symbol):
    bars, _ = bars_range(symbol, parse_timeframe("1Day"), *WINDOW, now=NOW)
    return bars


def minutes(symbol, day):
    start = datetime.fromisoformat(day + "T00:00:00+00:00")
    end = datetime.fromisoformat(day + "T23:59:00+00:00")
    bars, _ = bars_range(symbol, parse_timeframe("1Min"), start, end, now=NOW)
    return bars


def test_crash_crashes():
    closes = [b["c"] for b in daily("CRASH")]
    assert min(closes) / max(closes) < 0.82


def test_moon_moons():
    closes = [b["c"] for b in daily("MOON")]
    assert max(closes) / min(closes) > 1.5


def test_flat_flatlines():
    for b in daily("FLAT"):
        assert b["o"] == b["h"] == b["l"] == b["c"] == 100.0


def test_gappy_gaps():
    bars = daily("GAPPY")
    gaps = [
        abs(b["o"] / prev["c"] - 1.0) for prev, b in zip(bars, bars[1:])
    ]
    assert sum(1 for g in gaps if g > 0.04) >= 5


def test_halts_halt():
    short_days = [
        d
        for d in ("2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10")
        if len(minutes("HALTS", d)) < 390
    ]
    assert short_days, "no halted session found in a full week"


def test_spikey_spikes():
    found = False
    for day in ("2026-07-06", "2026-07-07", "2026-07-08"):
        for b in minutes("SPIKEY", day):
            body_hi = max(b["o"], b["c"])
            body_lo = min(b["o"], b["c"])
            if (b["h"] - body_hi) / b["c"] > 0.05 or (body_lo - b["l"]) / b["c"] > 0.05:
                found = True
    assert found


def test_penny_is_sub_dollar():
    assert all(b["c"] < 1.0 for b in daily("PENNY"))
    # sub-$1 prices carry 4 decimals
    assert any(round(b["c"], 2) != b["c"] for b in daily("PENNY"))


def test_choppy_chops():
    bars = daily("CHOPPY")
    rets = [abs(b["c"] / prev["c"] - 1.0) for prev, b in zip(bars, bars[1:])]
    assert sum(rets) / len(rets) > 0.02  # ~2%+ average daily move
