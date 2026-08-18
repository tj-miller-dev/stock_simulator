"""Byte-stability tripwire for the generation-1 determinism guarantee.

The golden files pin exact engine output for fixed requests. If any of these
tests fail, generation 1's output has changed -- that is NEVER acceptable to
ship silently. Either revert the change, or introduce it as GENERATION 2 and
leave gen-1 code paths intact. Regenerate goldens only for a brand-new
generation: python -m tests.test_golden
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from engine import bars_range, demo_price, parse_timeframe

GOLDEN_DIR = Path(__file__).parent / "golden"
NOW = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)

CASES = {
    "aapl_daily_jul2026": ("AAPL", "1Day", "2026-07-01", "2026-07-15"),
    "unknown_minute_day": ("QXJ7", "5Min", "2026-07-06", "2026-07-07"),
    "crash_daily_jul2026": ("CRASH", "1Day", "2026-07-01", "2026-07-31"),
    "spy_weekly_h1_2026": ("SPY", "1Week", "2026-01-01", "2026-07-01"),
    "seeded_universe": ("AAPL", "1Day", "2026-07-01", "2026-07-15"),
}


def generate(name):
    symbol, tf, start, end = CASES[name]
    seed = "42" if name == "seeded_universe" else ""
    bars, _ = bars_range(
        symbol,
        parse_timeframe(tf),
        datetime.fromisoformat(start + "T00:00:00+00:00"),
        datetime.fromisoformat(end + "T23:59:00+00:00"),
        seed=seed,
        now=NOW,
    )
    return bars


@pytest.mark.parametrize("name", CASES)
def test_golden(name):
    path = GOLDEN_DIR / f"{name}.json"
    expected = json.loads(path.read_text())
    assert generate(name) == expected


def test_demo_price_stability():
    assert demo_price("AAPL", 1_765_000_000) == 217.8
    assert demo_price("CUCKOO", 1_765_000_000) == demo_price("CUCKOO", 1_765_000_000)


if __name__ == "__main__":
    GOLDEN_DIR.mkdir(exist_ok=True)
    for name in CASES:
        (GOLDEN_DIR / f"{name}.json").write_text(json.dumps(generate(name), indent=1))
        print(f"wrote golden/{name}.json")
