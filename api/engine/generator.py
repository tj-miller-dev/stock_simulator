"""Hierarchical deterministic bar generator.

Layout (V1_SPEC section 1.1):

    yearly anchors  -> per-(symbol, year) hashed annual returns   O(|years|)
    daily skeleton  -> per-year seeded log-space bridge between
                       anchors, heteroscedastic (monthly vol
                       regimes), plus overnight gaps              O(<=252)
    minute path     -> per-day seeded bridge from that day's
                       open to close, 390 regular-session minutes O(390)

The minute path is the source of truth: daily H/L/V/n are aggregates of the
day's minute bars, and every coarser timeframe aggregates exactly from finer
ones. That is the cross-timeframe coherence guarantee, and it holds by
construction rather than by reconciliation.

Only *completed* buckets are emitted (a bucket whose end is <= now). This is a
deliberate deviation from Alpaca (which serves the in-progress bar): a partial
bar mutates as time passes, and byte-stability of anything we have ever served
outranks fidelity on this point.

RNG stream order inside a scope is frozen forever (see hashing.py). Within
"minutes:{date}": path z (390), high wiggle (390), low wiggle (390),
day-volume noise (1), volume weight noise (390). Reordering is a
generation-breaking change.
"""

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import numpy as np

from .hashing import hash_float, hash_norm, rng, scope_key, value_noise
from .market_calendar import (
    MINUTES_PER_SESSION,
    is_trading_day,
    midnight_et_utc,
    next_trading_day,
    prev_trading_day,
    session_close_utc,
    session_open_utc,
    trading_days_of_year,
)
from .personality import Personality, personality
from .corporate_actions import factors_for
from .scenarios import Scenario, scenario_for

GENERATION = 1
REFERENCE_YEAR = 2026          # anchor prices are pinned at Jan 1 of this year
EARLIEST_YEAR = 1970
INTRADAY_VARIANCE_FRACTION = 0.5
# Year-end anchors move with damped volatility: the daily bridge already adds
# a full year of wiggle around the anchor line, and undamped anchors let
# curated tickers drift out of their "plausible ballpark" within the
# reference year itself.
ANCHOR_VOL_DAMP = 0.6
_SQRT_252 = math.sqrt(252.0)

_TIMEFRAME_RE = re.compile(r"^(\d+)(Min|Hour|Day|Week|Month)$", re.IGNORECASE)
_UNIT_CANON = {"min": "Min", "hour": "Hour", "day": "Day", "week": "Week", "month": "Month"}
_UNIT_LIMITS = {
    "Min": (1, 59),
    "Hour": (1, 23),
    "Day": (1, 1),
    "Week": (1, 1),
}
_MONTH_AMOUNTS = (1, 2, 3, 4, 6, 12)


@dataclass(frozen=True)
class Timeframe:
    amount: int
    unit: str  # Min | Hour | Day | Week | Month

    @property
    def is_intraday(self) -> bool:
        return self.unit in ("Min", "Hour")

    @property
    def minutes(self) -> int:
        return self.amount if self.unit == "Min" else self.amount * 60


def parse_timeframe(text: str) -> Timeframe:
    """Alpaca's timeframe grammar. Raises ValueError with a message that
    teaches -- errors are read at the exact moment someone is stuck."""
    match = _TIMEFRAME_RE.match(text.strip())
    if match:
        amount = int(match.group(1))
        unit = _UNIT_CANON[match.group(2).lower()]
        if unit == "Month":
            if amount in _MONTH_AMOUNTS:
                return Timeframe(amount, unit)
        else:
            low, high = _UNIT_LIMITS[unit]
            if low <= amount <= high:
                return Timeframe(amount, unit)
    raise ValueError(
        f"invalid timeframe {text!r}: expected [N]Min (1-59), [N]Hour (1-23), "
        f"1Day, 1Week, or [N]Month with N in {list(_MONTH_AMOUNTS)} -- "
        f"e.g. timeframe=15Min or timeframe=1Day"
    )


def _effective(symbol: str) -> tuple[Personality, Scenario | None]:
    scen = scenario_for(symbol)
    if scen is not None:
        return scen.personality, scen
    return personality(symbol), None


@lru_cache(maxsize=4096)
def _log_anchor(symbol: str, year: int, seed: str) -> float:
    """Log price at the start of `year`'s first session. Pinned to the
    personality's base price at REFERENCE_YEAR; other years chain annual
    returns forward or backward from there."""
    p, _ = _effective(symbol)
    ref = math.log(p.base_price)
    if year == REFERENCE_YEAR:
        return ref

    def annual_return(y: int) -> float:
        z = hash_norm(scope_key(GENERATION, seed, symbol, f"year:{y}"))
        vol = p.annual_vol * ANCHOR_VOL_DAMP
        return (p.annual_drift - 0.5 * vol**2) + vol * z

    total = 0.0
    if year > REFERENCE_YEAR:
        for y in range(REFERENCE_YEAR, year):
            total += annual_return(y)
    else:
        for y in range(year, REFERENCE_YEAR):
            total -= annual_return(y)
    return ref + total


def _cluster_mult(symbol: str, year: int, month: int, seed: str) -> float:
    """Slow volatility-regime multiplier, one draw per (symbol, month)."""
    z = hash_norm(scope_key(GENERATION, seed, symbol, f"vol:{year}-{month:02d}"))
    return float(min(2.5, max(0.5, math.exp(0.45 * z))))


@lru_cache(maxsize=512)
def _daily_skeleton(symbol: str, year: int, seed: str):
    """Per-day (open, close, sigma) for one year: a heteroscedastic log-space
    bridge between the year's anchors, with hashed overnight gaps."""
    p, scen = _effective(symbol)
    days = trading_days_of_year(year)
    n = len(days)
    ln_a0 = _log_anchor(symbol, year, seed)
    ln_a1 = _log_anchor(symbol, year + 1, seed)

    if p.annual_vol == 0.0:  # FLAT: a perfectly still market
        closes = np.full(n, math.exp(ln_a0))
        return days, closes.copy(), closes, np.zeros(n)

    mult = np.array([_cluster_mult(symbol, d.year, d.month, seed) for d in days])
    sigma = (p.annual_vol / _SQRT_252) * mult

    g = rng(scope_key(GENERATION, seed, symbol, f"daily:{year}"))
    z = g.standard_normal(n)
    steps = sigma * z
    walk = np.cumsum(steps)
    variance = np.cumsum(sigma**2)
    weight = variance / variance[-1]
    log_closes = ln_a0 + walk - weight * (walk[-1] - (ln_a1 - ln_a0))
    closes = np.exp(log_closes)

    if scen is not None and scen.gap_override is not None:
        # Gap-driven scenario (GAPPY): the price level is carried by the gaps
        # themselves, so closes follow opens instead of the anchor bridge --
        # otherwise every gap would be exactly faded intraday.
        prefix = scope_key(GENERATION, seed, symbol, "gap")
        gaps = np.array([scen.gap_override(d, prefix) for d in days])
        opens = np.empty(n)
        closes = np.empty(n)
        prev = math.exp(ln_a0)
        for i in range(n):
            opens[i] = prev * math.exp(gaps[i])
            closes[i] = opens[i] * math.exp(0.25 * sigma[i] * z[i])
            prev = closes[i]
        return days, opens, closes, sigma

    prev_closes = np.concatenate(([math.exp(ln_a0)], closes[:-1]))
    gaps = np.array(
        [
            0.25 * s * hash_norm(scope_key(GENERATION, seed, symbol, f"gap:{d.isoformat()}"))
            for d, s in zip(days, sigma)
        ]
    )
    opens = prev_closes * np.exp(gaps)
    return days, opens, closes, sigma


def _day_context(symbol: str, d: date, seed: str):
    days, opens, closes, sigma = _daily_skeleton(symbol, d.year, seed)
    i = days.index(d)
    prev_mult = 1.0
    _, scen = _effective(symbol)
    mult = 1.0
    if scen is not None and scen.day_multiplier is not None:
        mult = scen.day_multiplier(d)
        prev_day = days[i - 1] if i > 0 else prev_trading_day(d)
        prev_mult = scen.day_multiplier(prev_day)
    return float(opens[i]), float(closes[i]), float(sigma[i]), mult, prev_mult


def _trade_size(symbol: str) -> float:
    return 80.0 + 240.0 * hash_float(f"personality:{symbol}:tradesize")


def _day_arrays(symbol: str, d: date, seed: str):
    """The day's minute data as numpy arrays: (minute_idx, o, h, l, c, v, n,
    vw), halted minutes already removed (Alpaca omits bars with no trades).
    Source of truth for every coarser timeframe. Array form keeps daily and
    coarser queries fast -- per-minute dicts only materialize for intraday
    output."""
    p, scen = _effective(symbol)
    open_px, close_px, sigma_d, mult, prev_mult = _day_context(symbol, d, seed)
    n_min = MINUTES_PER_SESSION

    if scen is not None and scen.flat:
        px = np.full(n_min, p.base_price)
        volume = np.full(n_min, max(1, int(p.daily_volume // n_min)), dtype=np.int64)
        trades = np.maximum(1, (volume / _trade_size(symbol)).astype(np.int64))
        idx = np.arange(n_min)
        return idx, px, px, px, px, volume, trades, px

    g = rng(scope_key(GENERATION, seed, symbol, f"minutes:{d.isoformat()}"))
    z = g.standard_normal(n_min)
    ivm = scen.intraday_vol_mult if scen is not None else 1.0
    sig_min = max(sigma_d, 1e-8) * math.sqrt(INTRADAY_VARIANCE_FRACTION / n_min) * ivm

    ln_o, ln_c = math.log(open_px), math.log(close_px)
    walk = np.cumsum(sig_min * z)
    frac = np.arange(1, n_min + 1) / n_min
    log_path = ln_o + walk - frac * (walk[-1] - (ln_c - ln_o))
    path = np.empty(n_min + 1)
    path[0] = open_px
    path[1:] = np.exp(log_path)
    path *= mult  # scenario day multiplier scales the whole session

    wiggle_hi = np.abs(g.standard_normal(n_min)) * sig_min * 0.35
    wiggle_lo = np.abs(g.standard_normal(n_min)) * sig_min * 0.35
    opens = path[:-1]
    closes = path[1:]
    highs = np.maximum(opens, closes) * (1.0 + wiggle_hi)
    lows = np.minimum(opens, closes) * (1.0 - wiggle_lo)

    if scen is not None and scen.spike_minutes is not None:
        for minute, size in scen.spike_minutes(d).items():
            if size > 0:
                highs[minute] = max(highs[minute], closes[minute] * (1.0 + size))
            else:
                lows[minute] = min(lows[minute], closes[minute] * (1.0 + size))

    # Day volume: base level, boosted when the day actually moved (including
    # a scenario multiplier jump -- crash days trade heavy).
    day_move = abs(math.log(close_px / open_px)) + abs(math.log(mult / prev_mult))
    boost = min(4.0, 0.7 + 0.6 * day_move / max(sigma_d, 1e-8))
    vol_noise = math.exp(0.35 * float(g.standard_normal(1)[0]))
    day_volume = p.daily_volume * boost * vol_noise

    # Intraday distribution: U-shaped (heavy open/close) and move-following.
    x = np.arange(n_min) / (n_min - 1)
    u_shape = 0.55 + 3.6 * (x - 0.5) ** 2
    minute_move = np.abs(np.diff(np.log(path)))
    intensity = u_shape * (1.0 + 2.5 * minute_move / max(sig_min, 1e-12))
    intensity *= np.exp(0.25 * g.standard_normal(n_min))
    weights = intensity / intensity.sum()
    volumes = np.maximum(1, np.rint(weights * day_volume)).astype(np.int64)
    trade_size = _trade_size(symbol)
    trades = np.maximum(1, np.rint(volumes / trade_size)).astype(np.int64)
    vwaps = (opens + highs + lows + closes) / 4.0

    stale = scen.stale_minutes(d) if scen is not None and scen.stale_minutes else frozenset()
    if stale:
        # A stuck feed repeats its last print: flat bar, no trades. Timestamps
        # still advance -- a bar's timestamp *is* its bucket, and freezing that
        # would be malformed. The staleness shows up as v=0 against an
        # unchanging price, and the first live minute afterwards carries the
        # whole move as a catch-up gap.
        # opens/closes are overlapping views of `path` (opens[i] is path[i] is
        # closes[i-1]); copy before writing or freezing one bar corrupts its
        # neighbour. Copies are made here only, so no other symbol pays.
        opens, closes = opens.copy(), closes.copy()
        frozen = float(opens[0])
        for i in range(n_min):
            if i in stale:
                opens[i] = highs[i] = lows[i] = closes[i] = vwaps[i] = frozen
                volumes[i] = 0
                trades[i] = 0
            else:
                frozen = float(closes[i])

    halted = scen.halted_minutes(d) if scen is not None and scen.halted_minutes else frozenset()
    idx = np.arange(n_min)
    if halted:
        keep = np.array([i not in halted for i in idx])
        idx = idx[keep]
        opens, highs, lows, closes = opens[keep], highs[keep], lows[keep], closes[keep]
        volumes, trades, vwaps = volumes[keep], trades[keep], vwaps[keep]
    return idx, opens, highs, lows, closes, volumes, trades, vwaps


def _day_minute_bars(symbol: str, d: date, seed: str, factors=None) -> list[dict]:
    idx, o, h, l, c, v, n, vw = _day_arrays(symbol, d, seed)
    # One factor for the whole day, applied before anything aggregates: that
    # is what keeps a week straddling an ex-date coherent with its own minutes
    # (see corporate_actions.py).
    pf, vf = factors(d) if factors is not None else (1.0, 1)
    if pf != 1.0 or vf != 1:
        o, h, l, c, vw = o * pf, h * pf, l * pf, c * pf, vw * pf
        v = v * vf
    open_utc = session_open_utc(d)
    return [
        {
            "t": open_utc + timedelta(minutes=int(idx[k])),
            "o": float(o[k]), "h": float(h[k]), "l": float(l[k]), "c": float(c[k]),
            "v": int(v[k]), "n": int(n[k]), "vw": float(vw[k]),
        }
        for k in range(len(idx))
    ]


# (open, high, low, close, volume, trades, vw-numerator) per day -- hashable,
# immutable, safe to memoize. The cache is pure-function memoization, not
# state: identical inputs always produce identical entries.
@lru_cache(maxsize=131072)
def _day_summary(symbol: str, d: date, seed: str) -> tuple | None:
    idx, o, h, l, c, v, n, vw = _day_arrays(symbol, d, seed)
    if len(idx) == 0:
        return None
    volume = int(v.sum())
    return (
        float(o[0]),
        float(h.max()),
        float(l.min()),
        float(c[-1]),
        volume,
        int(n.sum()),
        float((vw * v).sum()),
    )


def _aggregate(minutes: list[dict], t: datetime) -> dict | None:
    if not minutes:
        return None
    volume = sum(m["v"] for m in minutes)
    return {
        "t": t,
        "o": minutes[0]["o"],
        "h": max(m["h"] for m in minutes),
        "l": min(m["l"] for m in minutes),
        "c": minutes[-1]["c"],
        "v": volume,
        "n": sum(m["n"] for m in minutes),
        # A bucket sitting entirely inside a STALE window has no trades to
        # weight by; the frozen price is the only honest answer.
        "vw": (sum(m["vw"] * m["v"] for m in minutes) / volume) if volume else minutes[-1]["c"],
    }


def _adjust_summary(summary: tuple, pf: float, vf: int) -> tuple:
    o, h, l, c, volume, trades, vw_num = summary
    # Trade count survives untouched: a split changes how many shares a trade
    # was for, never how many trades happened.
    return (o * pf, h * pf, l * pf, c * pf, volume * vf, trades, vw_num * pf * vf)


def _aggregate_days(symbol: str, days: list[date], seed: str, t: datetime,
                    factors=None) -> dict | None:
    summaries = []
    for d in days:
        summary = _day_summary(symbol, d, seed)
        if summary is None:
            continue
        if factors is not None:
            pf, vf = factors(d)
            if pf != 1.0 or vf != 1:
                summary = _adjust_summary(summary, pf, vf)
        summaries.append(summary)
    if not summaries:
        return None
    volume = sum(s[4] for s in summaries)
    return {
        "t": t,
        "o": summaries[0][0],
        "h": max(s[1] for s in summaries),
        "l": min(s[2] for s in summaries),
        "c": summaries[-1][3],
        "v": volume,
        "n": sum(s[5] for s in summaries),
        "vw": (sum(s[6] for s in summaries) / volume) if volume else summaries[-1][3],
    }


def _round_price(x: float) -> float:
    return round(x, 4 if x < 1.0 else 2)


def _format_bar(bar: dict) -> dict:
    return {
        "c": _round_price(bar["c"]),
        "h": _round_price(bar["h"]),
        "l": _round_price(bar["l"]),
        "n": bar["n"],
        "o": _round_price(bar["o"]),
        "t": bar["t"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "v": bar["v"],
        "vw": round(bar["vw"], 6),
    }


def _intraday_buckets(symbol: str, d: date, step: int, seed: str,
                      factors=None) -> list[dict]:
    minutes = _day_minute_bars(symbol, d, seed, factors)
    open_utc = session_open_utc(d)
    buckets: dict[int, list[dict]] = {}
    for m in minutes:
        idx = int((m["t"] - open_utc).total_seconds()) // 60 // step
        buckets.setdefault(idx, []).append(m)
    out = []
    for idx in sorted(buckets):
        t = open_utc + timedelta(minutes=idx * step)
        bar = _aggregate(buckets[idx], t)
        bar["_end"] = min(t + timedelta(minutes=step), session_close_utc(d))
        out.append(bar)
    return out


def _daily_bar(symbol: str, d: date, seed: str, factors=None) -> dict | None:
    bar = _aggregate_days(symbol, [d], seed, midnight_et_utc(d), factors)
    if bar is not None:
        bar["_end"] = session_close_utc(d)
    return bar


def _iter_days(start_d: date, end_d: date, descending: bool):
    d = end_d if descending else start_d
    if not is_trading_day(d):
        d = prev_trading_day(d) if descending else next_trading_day(d)
    while start_d <= d <= end_d:
        yield d
        d = prev_trading_day(d) if descending else next_trading_day(d)


def _week_anchor(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _month_anchor(d: date, amount: int) -> date:
    bucket_month = ((d.month - 1) // amount) * amount + 1
    return date(d.year, bucket_month, 1)


def bars_range(
    symbol: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    *,
    seed: str = "",
    max_bars: int = 10_000,
    descending: bool = False,
    now: datetime | None = None,
    as_of: datetime | None = None,
    adjustment: str = "all",
) -> tuple[list[dict], bool]:
    """Formatted bars for [start, end], oldest-first unless descending.
    Returns (bars, truncated). Work is bounded by max_bars: iteration walks
    day by day from the near end and stops as soon as the budget is spent,
    so a huge window with a small limit stays cheap.

    `as_of` answers "what would this feed have said on that date" -- the
    second axis of the determinism contract. Pin it and the bytes are frozen
    forever; leave it out and a restating symbol answers as of today, which is
    not what it answered last month. See corporate_actions.py."""
    now = now or datetime.now(timezone.utc)
    factors = factors_for(symbol, (as_of or now).date(), adjustment)
    end = min(end, now)
    start = max(start, datetime(EARLIEST_YEAR, 1, 1, tzinfo=timezone.utc))
    if start > end:
        return [], False

    start_d, end_d = start.date(), end.date()
    bars: list[dict] = []
    truncated = False

    def push(bar: dict | None) -> bool:
        """Append if the bucket is complete and inside the window; return
        False once the budget is exhausted."""
        nonlocal truncated
        if bar is None:
            return True
        if bar["_end"] > now or not (start <= bar["t"] <= end):
            return True
        if len(bars) >= max_bars:
            truncated = True
            return False
        del bar["_end"]
        bars.append(_format_bar(bar))
        return True

    if timeframe.is_intraday:
        step = timeframe.minutes
        for d in _iter_days(start_d, end_d, descending):
            day_bars = _intraday_buckets(symbol, d, step, seed, factors)
            for bar in reversed(day_bars) if descending else day_bars:
                if not push(bar):
                    return bars, truncated
    elif timeframe.unit == "Day":
        for d in _iter_days(start_d, end_d, descending):
            if not push(_daily_bar(symbol, d, seed, factors)):
                return bars, truncated
    else:
        if timeframe.unit == "Week":
            anchor_of = _week_anchor
        else:
            anchor_of = lambda d: _month_anchor(d, timeframe.amount)  # noqa: E731

        def period_bounds(anchor: date) -> tuple[date, date]:
            if timeframe.unit == "Week":
                last_cal = anchor + timedelta(days=4)
            else:
                months = anchor.month - 1 + timeframe.amount
                last_cal = date(anchor.year + months // 12, months % 12 + 1, 1) - timedelta(days=1)
            first = anchor if is_trading_day(anchor) else next_trading_day(anchor)
            last = last_cal if is_trading_day(last_cal) else prev_trading_day(last_cal)
            return first, last

        seen: set[date] = set()
        for d in _iter_days(start_d, end_d, descending):
            anchor = anchor_of(d)
            if anchor in seen:
                continue
            seen.add(anchor)
            first, last = period_bounds(anchor)
            if first < start_d or last > end_d:
                # The window clips this period; a partial week/month bar would
                # be a lie -- the client widens the window to get it.
                continue
            period_days = list(_iter_days(first, last, False))
            bar = _aggregate_days(symbol, period_days, seed, midnight_et_utc(anchor),
                                  factors)
            if bar is not None:
                bar["_end"] = session_close_utc(last)
            if not push(bar):
                return bars, truncated

    return bars, truncated


def latest_bar(symbol: str, timeframe: Timeframe, *, seed: str = "",
               now: datetime | None = None, as_of: datetime | None = None,
               adjustment: str = "all") -> dict | None:
    now = now or datetime.now(timezone.utc)
    lookback = {
        "Min": timedelta(days=7),
        "Hour": timedelta(days=7),
        "Day": timedelta(days=10),
        "Week": timedelta(days=30),
        "Month": timedelta(days=800),
    }[timeframe.unit]
    bars, _ = bars_range(
        symbol, timeframe, now - lookback, now, seed=seed, max_bars=1, descending=True,
        now=now, as_of=as_of, adjustment=adjustment,
    )
    return bars[0] if bars else None


# --- SSE demo clock -------------------------------------------------------

_DEMO_OCTAVES = (  # (wavelength seconds, weight)
    (4.0, 0.05),
    (16.0, 0.08),
    (64.0, 0.12),
    (256.0, 0.18),
    (1024.0, 0.27),
    (4096.0, 0.40),
    (16384.0, 0.60),
    (65536.0, 0.90),
    (262144.0, 1.35),
)


DEMO_STALE_PERIOD = 60    # seconds
DEMO_STALE_FROM = 40      # ...stale for the last 20 of every minute


def demo_clock(symbol: str, unix_second: float) -> float:
    """The instant a symbol's demo feed believes it is.

    Normally now. For STALE it sticks at the top of the stale window while the
    wall clock keeps moving, so the quote's own timestamp goes stale -- the
    failure the ticker exists to reproduce, and the one most clients never
    check for because the connection stays perfectly healthy throughout.

    Twenty seconds out of every minute, on a fixed schedule rather than a
    hashed one: anyone watching the landing page should see it freeze without
    having to wait, and a test should not have to hunt for the window.
    """
    scen = scenario_for(symbol)
    if scen is None or scen.stale_minutes is None:
        return unix_second
    offset = unix_second % DEMO_STALE_PERIOD
    if offset < DEMO_STALE_FROM:
        return unix_second
    return unix_second - offset + DEMO_STALE_FROM


def demo_price(symbol: str, unix_second: float, *, seed: str = "") -> float:
    """Price for the always-open demo session: a pure function of the wall
    clock, so every replica -- and every viewer -- sees the same tick at the
    same instant with no shared state."""
    p, _ = _effective(symbol)
    key = scope_key(GENERATION, seed, symbol, "demo")
    total = sum(w * value_noise(unix_second / lam, f"{key}:{int(lam)}") for lam, w in _DEMO_OCTAVES)
    swing = max(p.annual_vol, 0.05)
    return _round_price(p.base_price * math.exp(0.35 * swing * total))
