"""Every magic ticker must show its signature inside any 30-day window.
These tests use an arbitrary window (nothing special about July 2026) --
if a scenario only performs in hand-picked months, it fails its contract."""

from datetime import datetime, timezone

from engine import bars_range, demo_clock, demo_price, parse_timeframe

NOW = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
WINDOW = (datetime(2026, 7, 5, tzinfo=timezone.utc), datetime(2026, 8, 4, tzinfo=timezone.utc))
WEEK = ("2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10")


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
    short_days = [d for d in WEEK if len(minutes("HALTS", d)) < 390]
    assert short_days, "no halted session found in a full week"


def _frozen_runs(bars):
    """(start, end) index pairs of consecutive zero-volume bars."""
    runs, start = [], None
    for i, b in enumerate(bars):
        if b["v"] == 0 and start is None:
            start = i
        elif b["v"] != 0 and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(bars)))
    return runs


def test_stale_freezes_without_dropping_bars():
    """STALE is HALTS inverted: every bar arrives on time and none of them
    mean anything. A client checking "did I get a response" sees green."""
    found = 0
    for day in WEEK:
        bars = minutes("STALE", day)
        assert len(bars) == 390, f"{day}: STALE lost bars -- that is a halt, not a stall"
        for start, end in _frozen_runs(bars):
            found += 1
            price = bars[start]["c"]
            for b in bars[start:end]:
                assert b["o"] == b["h"] == b["l"] == b["c"] == price
                assert b["n"] == 0
            assert end - start >= 15
    assert found, "no stale window found in a full week"


def test_stale_catches_up_before_the_close():
    """The window never runs to the bell, so the move the feed slept through
    always lands in a real bar inside the same session."""
    gaps = []
    for day in WEEK:
        bars = minutes("STALE", day)
        for start, end in _frozen_runs(bars):
            assert end < len(bars), "stale window ran to the close; no catch-up bar"
            assert bars[end]["v"] > 0
            gaps.append(abs(bars[end]["o"] / bars[start]["c"] - 1.0))
    assert max(gaps) > 0.001, "no catch-up gap: the frozen stretch cost nothing"


def test_stale_demo_clock_freezes_while_the_wall_clock_moves():
    base = 1_765_000_000 - (1_765_000_000 % 60)  # top of some minute
    live = [base + s for s in (0, 10, 39)]
    stuck = [base + s for s in (40, 50, 59)]
    assert [demo_clock("STALE", t) for t in live] == live
    # The reported instant stops dead; the socket, the ticks and the
    # heartbeats all keep going.
    assert {demo_clock("STALE", t) for t in stuck} == {base + 40}
    assert {demo_price("STALE", demo_clock("STALE", t)) for t in stuck} == {
        demo_price("STALE", base + 40)
    }
    assert [demo_clock("AAPL", t) for t in stuck] == stuck  # ordinary symbols unaffected


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
