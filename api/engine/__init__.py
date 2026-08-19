"""CuckooTrade price engine.

Deterministic, stateless synthetic market data. Every bar is a pure function of
(symbol, timestamp, generation, seed, as_of) -- see docs/V1_SPEC.md section 1
and docs/V1_1_SPEC.md section 3 for the as_of axis. The engine
is an internal queryable service: the HTTP layer (api.py) is one consumer, the
V2 "fake broker" will be another. Nothing in here may hold mutable state.
"""

from .generator import (
    GENERATION,
    Timeframe,
    bars_range,
    demo_clock,
    demo_price,
    latest_bar,
    parse_timeframe,
)
from .corporate_actions import (
    RESTATING_TICKERS,
    actions_for,
    in_window as actions_in_window,
    parse_adjustment,
)
from .market_calendar import is_trading_day, next_trading_day, prev_trading_day
from .personality import personality
from .scenarios import SCENARIO_TICKERS

__all__ = [
    "GENERATION",
    "Timeframe",
    "bars_range",
    "demo_clock",
    "demo_price",
    "latest_bar",
    "parse_timeframe",
    "is_trading_day",
    "next_trading_day",
    "prev_trading_day",
    "personality",
    "SCENARIO_TICKERS",
    "RESTATING_TICKERS",
    "actions_for",
    "actions_in_window",
    "parse_adjustment",
]
