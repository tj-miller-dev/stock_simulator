"""Magic tickers: reserved symbols with scripted, calendar-anchored behavior.

The product's headline feature (V1_SPEC section 2). Scripts are anchored to the
calendar -- not the query window -- so the same dates show the same drama for
everyone, and every ticker exhibits its signature inside any 30-day view.
Schema stays valid always: a scenario shapes prices/volumes/missing bars, never
malformed fields.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from .hashing import hash_float
from .personality import Personality


@dataclass(frozen=True)
class Scenario:
    personality: Personality
    flat: bool = False                 # o=h=l=c at base_price, constant volume
    intraday_vol_mult: float = 1.0
    day_multiplier: Callable[[date], float] | None = None
    gap_override: Callable[[date, str], float] | None = None   # -> log gap
    halted_minutes: Callable[[date], frozenset[int]] | None = None
    stale_minutes: Callable[[date], frozenset[int]] | None = None
    spike_minutes: Callable[[date], dict[int, float]] | None = None  # minute -> signed wick fraction


def _crash_multiplier(d: date) -> float:
    # Monthly cycle: plateau, three-day 25% crash mid-month, grind back.
    dom = d.day
    if dom < 15:
        return 1.0
    if dom == 15:
        return 0.92
    if dom == 16:
        return 0.83
    if dom == 17:
        return 0.75
    return min(1.0, 0.75 + (dom - 17) * (0.25 / 14.0))


def _moon_multiplier(d: date) -> float:
    # Monthly sawtooth: parabolic grind up ~+115% peaking late in the month,
    # then a sharp correction to base. A full cycle fits in every 30-day
    # window, which is what the scenario contract demands.
    import calendar as _cal
    import math

    days_in_month = _cal.monthrange(d.year, d.month)[1]
    q = (d.day - 1) / days_in_month
    peak_q, strength = 0.85, 0.9
    if q < peak_q:
        return math.exp(strength * q)
    peak = math.exp(strength * peak_q)
    return float(peak + (1.0 - peak) * (q - peak_q) / (1.0 - peak_q))


def _gappy_gap(d: date, key_prefix: str) -> float:
    # +/- 5-15% overnight gaps most days; sign and size from the calendar date.
    u = hash_float(f"{key_prefix}:gapmag:{d.isoformat()}")
    sign = 1.0 if hash_float(f"{key_prefix}:gapsign:{d.isoformat()}") < 0.45 else -1.0
    if hash_float(f"{key_prefix}:gapon:{d.isoformat()}") < 0.25:
        return 0.0  # quiet day, for contrast
    import math

    return sign * math.log1p(0.05 + 0.10 * u)


def _halts_windows(d: date) -> frozenset[int]:
    key = f"scenario:HALTS:{d.isoformat()}"
    if hash_float(f"{key}:on") >= 0.7:
        return frozenset()
    start = 30 + int(hash_float(f"{key}:start") * 280)
    length = 20 + int(hash_float(f"{key}:len") * 26)
    window = set(range(start, min(390, start + length)))
    if hash_float(f"{key}:double") < 0.3:
        start2 = min(360, start + length + 30)
        window |= set(range(start2, min(390, start2 + 15)))
    return frozenset(window)


def _stale_windows(d: date) -> frozenset[int]:
    """HALTS's evil twin. Where a halt deletes bars, a stuck feed keeps
    emitting the last print it saw: the bars are all there, the timestamps
    advance, and nothing moves.

    Minute 389 is never stale, which is deliberate: the session always closes
    on a real print, so the day's volume is never zero and the catch-up bar
    that reconciles the frozen stretch always lands inside the same session.
    """
    key = f"scenario:STALE:{d.isoformat()}"
    if hash_float(f"{key}:on") >= 0.75:
        return frozenset()  # a clean session, for contrast
    start = 20 + int(hash_float(f"{key}:start") * 300)
    length = 15 + int(hash_float(f"{key}:len") * 30)
    return frozenset(range(start, min(389, start + length)))


def _spikey_minutes(d: date) -> dict[int, float]:
    key = f"scenario:SPIKEY:{d.isoformat()}"
    spikes: dict[int, float] = {}
    count = 1 + int(hash_float(f"{key}:count") * 3)  # 1-3 per day
    for k in range(count):
        minute = int(hash_float(f"{key}:at:{k}") * 390)
        size = 0.06 + 0.06 * hash_float(f"{key}:size:{k}")
        sign = 1.0 if hash_float(f"{key}:sign:{k}") < 0.5 else -1.0
        spikes[minute] = sign * size
    return spikes


SCENARIOS: dict[str, Scenario] = {
    "CRASH": Scenario(
        # Calm baseline so the scripted crash is unmistakably the story.
        personality=Personality(90.0, 0.16, 0.0, 12e6),
        day_multiplier=_crash_multiplier,
    ),
    "MOON": Scenario(
        personality=Personality(12.0, 0.30, 0.0, 30e6),
        day_multiplier=_moon_multiplier,
    ),
    "FLAT": Scenario(
        personality=Personality(100.0, 0.0, 0.0, 10_000),
        flat=True,
    ),
    "GAPPY": Scenario(
        personality=Personality(64.0, 0.20, 0.0, 8e6),
        intraday_vol_mult=0.35,
        gap_override=_gappy_gap,
    ),
    "HALTS": Scenario(
        personality=Personality(45.0, 0.30, 0.0, 6e6),
        halted_minutes=_halts_windows,
    ),
    "STALE": Scenario(
        personality=Personality(85.0, 0.28, 0.0, 7e6),
        stale_minutes=_stale_windows,
    ),
    "SPIKEY": Scenario(
        personality=Personality(150.0, 0.15, 0.0, 9e6),
        spike_minutes=_spikey_minutes,
    ),
    "PENNY": Scenario(
        personality=Personality(0.31, 0.85, 0.0, 15e6),
    ),
    "CHOPPY": Scenario(
        personality=Personality(70.0, 0.75, 0.0, 20e6),
    ),
}

SCENARIO_TICKERS = frozenset(SCENARIOS)


def scenario_for(symbol: str) -> Scenario | None:
    return SCENARIOS.get(symbol)
