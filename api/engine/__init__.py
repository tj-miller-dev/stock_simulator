"""CuckooTrade price engine.

Deterministic, stateless synthetic market data. Every bar is a pure function of
(symbol, timestamp, generation, seed) -- see docs/V1_SPEC.md section 1. The engine
is an internal queryable service: the HTTP layer (api.py) is one consumer, the
V2 "fake broker" will be another. Nothing in here may hold mutable state.
"""

from .generator import (
    GENERATION,
    Timeframe,
    bars_range,
    demo_price,
    latest_bar,
    parse_timeframe,
)
from .market_calendar import is_trading_day, next_trading_day, prev_trading_day
from .personality import personality
from .scenarios import SCENARIO_TICKERS

__all__ = [
    "GENERATION",
    "Timeframe",
    "bars_range",
    "demo_price",
    "latest_bar",
    "parse_timeframe",
    "is_trading_day",
    "next_trading_day",
    "prev_trading_day",
    "personality",
    "SCENARIO_TICKERS",
]
