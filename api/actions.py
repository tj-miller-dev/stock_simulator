"""GET /api/v1/corporate-actions -- what changed, and when it changed.

A reconciliation job that notices "the closes moved" but cannot say why has
found an anomaly, not a corporate action. This is the ledger it reconciles
against: every split, dividend and busted trade the feed knows about as of a
given date, carrying the three dates that actually matter -- announced, ex,
and processed.

The gap between ex and processed is the entire point of DIVVY. The adjustment
lands days *after* the ex-date, long after a job that polls on the ex-date has
decided the month is settled and stopped looking.

Cuckoo-native, so unlike the wire-compat surfaces this one carries its full
metadata in the body (V1_SPEC section 3).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from engine import GENERATION, RESTATING_TICKERS, actions_in_window

router = APIRouter()

ACTIONS_PATH = "/api/v1/corporate-actions"
MAX_ACTION_SYMBOLS = 50
DEFAULT_LOOKBACK = timedelta(days=180)


@router.get(ACTIONS_PATH)
def corporate_actions(
    symbols: str,
    start: str | None = None,
    end: str | None = None,
    as_of: str | None = None,
):
    from common import PUBLIC_HOST, parse_history, parse_symbols, parse_time

    example = f"{ACTIONS_PATH}?symbols=SPLITS,DIVVY"
    symbol_list = parse_symbols(symbols, MAX_ACTION_SYMBOLS, example=example)
    as_of_dt, _ = parse_history(as_of, "all")

    now = datetime.now(timezone.utc)
    effective = as_of_dt or now
    end_dt = parse_time(end, "end") if end else effective
    start_dt = parse_time(start, "start") if start else end_dt - DEFAULT_LOOKBACK

    found = []
    for symbol in symbol_list:
        for action in actions_in_window(
            symbol, start_dt.date(), end_dt.date(), effective.date()
        ):
            found.append(action.as_json())
    found.sort(key=lambda a: (a["ex_date"], a["symbol"]))

    return {
        "synthetic": True,
        "generation": GENERATION,
        "as_of": effective.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actions": found,
        "restating_tickers": sorted(RESTATING_TICKERS),
        "how_to_use": (
            "process_date is when the restatement lands in history: request the "
            "same window with as_of set either side of it and the bars differ. "
            "Everything else here has no corporate actions at all."
        ),
        "docs": f"{PUBLIC_HOST}/docs",
    }
