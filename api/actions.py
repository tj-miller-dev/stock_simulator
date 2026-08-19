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

import apidocs
from apidocs import AsOfQ, EndQ, StartQ, SymbolsQ
from fastapi import APIRouter

from engine import GENERATION, RESTATING_TICKERS, actions_in_window

router = APIRouter()

ACTIONS_PATH = "/api/v1/corporate-actions"
MAX_ACTION_SYMBOLS = 50
DEFAULT_LOOKBACK = timedelta(days=180)

_LEDGER_EXAMPLE = {
    "synthetic": True,
    "generation": GENERATION,
    "as_of": "2026-08-19T00:00:00Z",
    "start": "2026-02-20T00:00:00Z",
    "end": "2026-08-19T00:00:00Z",
    "actions": [
        {"symbol": "SPLITS", "type": "split", "ex_date": "2026-06-10",
         "announce_date": "2026-06-01", "process_date": "2026-06-10",
         "ratio": 2.0, "cash_amount": 0.0, "synthetic": True},
        {"symbol": "DIVVY", "type": "dividend", "ex_date": "2026-06-20",
         "announce_date": "2026-06-05", "process_date": "2026-06-27",
         "ratio": 1.0, "cash_amount": 1.8, "synthetic": True},
        {"symbol": "REVISED", "type": "correction", "ex_date": "2026-07-08",
         "announce_date": "2026-07-08", "process_date": "2026-07-13",
         "ratio": 1.0, "cash_amount": 0.0, "synthetic": True},
    ],
    "restating_tickers": ["DIVVY", "REVISED", "SPLITS"],
    "how_to_use": (
        "process_date is when the restatement lands in history: request the same "
        "window with as_of set either side of it and the bars differ. Everything "
        "else here has no corporate actions at all."
    ),
    "docs": "https://cuckootrade.com/docs",
}


@router.get(
    ACTIONS_PATH,
    tags=["cuckoo-native"],
    summary="Corporate actions ledger",
    operation_id="corporate_actions",
    response_description="Every action known as of the given date, with announce, ex and process dates.",
    responses={
        200: apidocs.response(
            "The ledger. `actions` is empty for every symbol outside "
            "`restating_tickers` -- that is the expected answer, not a failure.",
            schema=apidocs.ref("CorporateActionsResponse"),
            examples={
                "ledger": apidocs.example(
                    "All three restating tickers",
                    _LEDGER_EXAMPLE,
                    "Note DIVVY: `ex_date` 2026-06-20 but `process_date` 2026-06-27. "
                    "A job that reconciles on the ex-date and stops looking never "
                    "sees the adjustment land.",
                ),
                "empty": apidocs.example(
                    "An ordinary symbol",
                    {**_LEDGER_EXAMPLE, "actions": []},
                    "AAPL has no corporate actions here, and neither does anything "
                    "else outside the three restating tickers.",
                ),
            },
            links={
                "barsBeforeTheRestatement": apidocs.link(
                    "Bars for this symbol as of the announce date -- before the "
                    "action landed in history. Fill in your own window.",
                    "alpaca_stock_bars",
                    {"symbols": "$response.body#/actions/0/symbol",
                     "as_of": "$response.body#/actions/0/announce_date"},
                ),
                "barsAfterTheRestatement": apidocs.link(
                    "The same bars as of the process date, once the rewrite has "
                    "landed. The difference between these two calls is the whole "
                    "feature.",
                    "alpaca_stock_bars",
                    {"symbols": "$response.body#/actions/0/symbol",
                     "as_of": "$response.body#/actions/0/process_date"},
                ),
            },
        ),
        400: apidocs.response(
            "A malformed symbol list or date.",
            schema=apidocs.ref("AlpacaError"),
            examples={
                "date": apidocs.example(
                    "Unparseable date",
                    {"code": 40010001, "message":
                     "invalid start 'last june': use RFC-3339 or YYYY-MM-DD, e.g. "
                     "start=2026-07-01 or start=2026-07-01T13:30:00Z"},
                )
            },
        ),
    },
    openapi_extra=apidocs.extras(
        samples=(
            (
                "Shell",
                "curl",
                'curl "https://cuckootrade.com/api/v1/corporate-actions'
                '?symbols=SPLITS,DIVVY,REVISED" | jq .',
            ),
            (
                "Python",
                "requests (the full loop)",
                "import requests\n\n"
                'HOST = "https://cuckootrade.com"\n'
                'BARS = f"{HOST}/api/v1/alpaca/v2/stocks/bars"\n\n'
                "# 1. Ask the ledger when the restatement lands.\n"
                'ledger = requests.get(f"{HOST}/api/v1/corporate-actions",\n'
                '                      params={"symbols": "SPLITS"}).json()\n'
                'action = ledger["actions"][0]\n\n'
                "# 2. Ask for the same window either side of that date.\n"
                'window = {"symbols": "SPLITS", "timeframe": "1Day",\n'
                '          "start": "2026-06-01", "end": "2026-06-30"}\n'
                'for as_of in (action["announce_date"], action["process_date"]):\n'
                '    r = requests.get(BARS, params={**window, "as_of": as_of})\n'
                '    print(as_of, r.headers["X-Cuckoo-Restated"])\n'
                '    print("  first close:", r.json()["bars"]["SPLITS"][0]["c"])',
            ),
        ),
    ),
)
def corporate_actions(
    symbols: SymbolsQ,
    start: StartQ = None,
    end: EndQ = None,
    as_of: AsOfQ = None,
):
    """What changed, and when it changed -- the ledger the bar headers refer to.

    A reconciliation job that notices "the closes moved" but cannot say why has
    found an anomaly, not a corporate action. This is what it reconciles
    against: every split, dividend and busted trade the feed knows about as of a
    given date.

    **Three dates, and the gaps between them are the point.** `announce_date` is
    when it became public and nothing changed yet. `ex_date` is the session from
    which it applies -- bars dated *before* it get rewritten, bars on or after it
    never do, which is why a window sitting after every ex-date reports
    `0 actions applied`. `process_date` is when the rewrite actually lands in
    history: request the same window with `as_of` either side of it and the bars
    differ.

    For `DIVVY` those last two are five sessions apart, deliberately. A job that
    polls on the ex-date and stops looking has already decided the month is
    settled by the time the adjustment arrives.

    **The ledger restates too.** Actions whose `process_date` is after the
    requested `as_of` are not yet known and are withheld, exactly as the bars
    would be. Only `SPLITS`, `DIVVY` and `REVISED` have any actions at all;
    every other symbol returns an empty list, which is the correct answer rather
    than a failure.
    """
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
