"""Alpaca-compatible surface: /api/v1/alpaca/<Alpaca's own paths>.

Replicates data.alpaca.markets: same paths, params, JSON shapes, pagination
semantics, and error style ({"code", "message"}). The acceptance test
(tests/test_alpaca_sdk.py) is the contract: alpaca-py pointed here via
url_override works unmodified.

The OpenAPI decorations come from apidocs.py -- parameter descriptions are
shared with every other surface that takes the same parameter, and the
response examples below are real captures, not invented ones.
"""

import apidocs
from apidocs import (
    AdjustmentQ,
    AlpacaAsofQ,
    AsOfQ,
    CurrencyQ,
    EndQ,
    FeedQ,
    GenerationQ,
    LimitQ,
    PageTokenQ,
    SeedQ,
    SortQ,
    StartQ,
    SymbolPathQ,
    SymbolsQ,
    TimeframeQ,
)
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from common import (
    EXAMPLE,
    api_error,
    mark_restatement,
    maybe_cache_forever,
    parse_common,
    parse_history,
    parse_symbols,
    paginate_bars,
)
from engine import GENERATION, latest_bar

router = APIRouter(prefix="/api/v1/alpaca", tags=["provider: alpaca"])

ALPACA_DOCS = ("Alpaca's own reference for this endpoint",
               "https://docs.alpaca.markets/reference/stockbars")

INDEX_ENTRY = {
    "status": "available",
    "base_url": "https://cuckootrade.com/api/v1/alpaca",
    "compatible_with": "https://data.alpaca.markets",
    "sdk_hint": "StockHistoricalDataClient(url_override="
    "'https://cuckootrade.com/api/v1/alpaca') with any non-empty keys",
    "endpoints": [
        {
            "method": "GET",
            "path": "/api/v1/alpaca/v2/stocks/bars",
            "purpose": "Alpaca-compatible historical bars",
            "example": EXAMPLE,
        },
        {
            "method": "GET",
            "path": "/api/v1/alpaca/v2/stocks/bars",
            "cuckoo_extensions": {
                "as_of": "answer as the feed would have on this date (RFC-3339). "
                "Pin it and the bytes never change; omit it and restating "
                "symbols answer as of today. Not Alpaca's `asof`.",
                "adjustment": "raw | split | dividend | all (default all)",
                "seed": "alternate but equally deterministic universe",
                "generation": "pin the generator version",
            },
        },
        {
            "method": "GET",
            "path": "/api/v1/alpaca/v2/stocks/bars/latest",
            "purpose": "latest completed bar per symbol",
            "example": "/api/v1/alpaca/v2/stocks/bars/latest?symbols=AAPL,SPY",
        },
        {
            "method": "GET",
            "path": "/api/v1/alpaca/v2/stocks/{symbol}/bars",
            "purpose": "single-symbol variant",
            "example": "/api/v1/alpaca/v2/stocks/AAPL/bars?timeframe=15Min",
        },
    ],
}

# Real captures, trimmed to two bars. Invented examples drift from the wire the
# moment anything changes; these were copied out of actual responses.
_AAPL_BARS = [
    {"c": 325.41, "h": 325.52, "l": 311.91, "n": 755356, "o": 312.38,
     "t": "2026-07-01T04:00:00Z", "v": 112387921, "vw": 317.038105},
    {"c": 315.71, "h": 321.97, "l": 314.97, "n": 482943, "o": 321.97,
     "t": "2026-07-02T04:00:00Z", "v": 71856587, "vw": 318.143324},
]
_SPLITS_BARS = [
    {"c": 213.63, "h": 214.48, "l": 212.89, "n": 135050, "o": 213.66,
     "t": "2026-06-01T04:00:00Z", "v": 38384492, "vw": 213.589206},
    {"c": 210.15, "h": 213.62, "l": 209.91, "n": 62673, "o": 213.24,
     "t": "2026-06-02T04:00:00Z", "v": 17811672, "vw": 211.902982},
]

_BARS_EXAMPLES = {
    "ordinary": apidocs.example(
        "An ordinary symbol",
        {"bars": {"AAPL": _AAPL_BARS}, "next_page_token": apidocs.NULL},
        "`next_page_token: null` is the window being exhausted -- the only "
        "reliable stop condition.",
    ),
    "restated": apidocs.example(
        "A restated window (SPLITS with as_of)",
        {"bars": {"SPLITS": _SPLITS_BARS}, "next_page_token": apidocs.NULL},
        "Asked with `as_of=2026-07-09`, so the June bars come back rescaled by the "
        "2026-06-10 split. The same request with `as_of=2026-06-09` returns the "
        "pre-split prices instead. `X-Cuckoo-Restated` names the action that ran.",
    ),
    "paginated": apidocs.example(
        "A partial page",
        {"bars": {"AAPL": _AAPL_BARS},
         "next_page_token": "djF8QUFQTHwyMDI2LTA3LTAyVDA0OjAwOjAwWg=="},
        "`limit` ran out. Send the token back as `page_token` -- unmodified -- to "
        "resume exactly after the last bar served.",
    ),
    "empty": apidocs.example(
        "A window with no sessions",
        {"bars": {"AAPL": []}, "next_page_token": apidocs.NULL},
        "A requested symbol with nothing in the window is present with an empty "
        "array, not omitted. Weekends, holidays and future windows all land here.",
    ),
    "multi": apidocs.example(
        "Several symbols",
        {"bars": {"AAPL": _AAPL_BARS[:1], "CRASH": [
            {"c": 74.11, "h": 99.02, "l": 73.88, "n": 401288, "o": 98.77,
             "t": "2026-07-01T04:00:00Z", "v": 88410233, "vw": 81.442017}]},
         "next_page_token": apidocs.NULL},
        "Keyed by symbol, alphabetically. CRASH is mid-drawdown here -- roughly "
        "-25% in a session, which is the point of it.",
    ),
}

_BARS_ERRORS = {
    "timeframe": apidocs.example(
        "Unparseable timeframe",
        {"code": 40010001, "message":
         "invalid timeframe 'daily': expected [N]Min (1-59), [N]Hour (1-23), 1Day, "
         "1Week, or [N]Month with N in [1, 2, 3, 4, 6, 12] -- e.g. timeframe=15Min "
         f"or timeframe=1Day -- working example: {EXAMPLE}"},
        "Every error states the valid grammar and includes a URL that works. Errors "
        "get read at the moment someone is stuck.",
    ),
    "symbol": apidocs.example(
        "Malformed symbol",
        {"code": 40010001, "message":
         "invalid symbol 'NOT A TICKER': letters, digits, dot and dash, max 12 "
         "chars. Any well-formed symbol works -- unknown ones get a stable "
         f"hash-derived personality. Working example: {EXAMPLE}"},
        "Note what is *not* an error: an unknown-but-well-formed symbol. Those "
        "always return data.",
    ),
    "generation": apidocs.example(
        "Unknown generation",
        {"code": 40010001, "message":
         f"unknown generation 7: this deployment serves generation {GENERATION}. "
         "Omit the parameter for the current generation."},
        "A wrong `generation` fails loudly rather than silently serving the current "
        "one -- pinned golden files depend on that.",
    ),
    "token": apidocs.example(
        "Edited page token",
        {"code": 40010001, "message":
         "invalid page_token: pass the next_page_token value from the previous "
         "response, unmodified."},
    ),
}

_NEXT_PAGE_LINK = {
    "nextPage": apidocs.link(
        "When `next_page_token` is not null, send it back as `page_token` -- "
        "unmodified -- to get the next page of this same query.",
        "alpaca_stock_bars",
        {"page_token": "$response.body#/next_page_token"},
    )
}

_BARS_RESPONSES = {
    200: apidocs.response(
        "Bars keyed by symbol. Check `X-Cuckoo-Restated` to see whether any "
        "corporate action rewrote them.",
        schema=apidocs.ref("AlpacaBarsResponse"),
        examples=_BARS_EXAMPLES,
        headers=apidocs.BAR_HEADERS,
        links=_NEXT_PAGE_LINK,
    ),
    400: apidocs.response(
        "A malformed parameter, in Alpaca's error shape.",
        schema=apidocs.ref("AlpacaError"),
        examples=_BARS_ERRORS,
    ),
}


def fault_response(status: int, message: str) -> JSONResponse:
    """An injected fault, wearing Alpaca's error shape (scenario= in effects.py)."""
    return JSONResponse(
        status_code=status, content={"code": status * 100000 + 10000, "message": message}
    )


@router.get(
    "/v2/stocks/bars",
    summary="Historical bars",
    operation_id="alpaca_stock_bars",
    response_description="Bars keyed by symbol, plus a pagination token.",
    responses=_BARS_RESPONSES,
    openapi_extra=apidocs.extras(
        mimics=ALPACA_DOCS,
        samples=(
            (
                "Shell",
                "curl",
                'curl -i "https://cuckootrade.com/api/v1/alpaca/v2/stocks/bars'
                '?symbols=AAPL,CRASH&timeframe=1Day&start=2026-07-01&end=2026-07-31"',
            ),
            (
                "Python",
                "alpaca-py",
                "from alpaca.data.historical import StockHistoricalDataClient\n"
                "from alpaca.data.requests import StockBarsRequest\n"
                "from alpaca.data.timeframe import TimeFrame\n\n"
                "# The keys are required by the constructor and ignored by the server.\n"
                "client = StockHistoricalDataClient(\n"
                '    "any", "thing",\n'
                '    url_override="https://cuckootrade.com/api/v1/alpaca",\n'
                ")\n"
                "bars = client.get_stock_bars(StockBarsRequest(\n"
                '    symbol_or_symbols=["AAPL", "CRASH"],\n'
                "    timeframe=TimeFrame.Day,\n"
                '    start="2026-07-01",\n'
                "))",
            ),
            (
                "Shell",
                "curl (restatement)",
                "# The same window either side of the split, and the bars differ\n"
                'BASE="https://cuckootrade.com/api/v1/alpaca/v2/stocks/bars"\n'
                'Q="symbols=SPLITS&timeframe=1Day&start=2026-06-01&end=2026-06-30"\n'
                'curl -sD- "$BASE?$Q&as_of=2026-06-09" | grep -i x-cuckoo-restated\n'
                'curl -sD- "$BASE?$Q&as_of=2026-07-09" | grep -i x-cuckoo-restated',
            ),
            (
                "Shell",
                "curl (fault injection)",
                "# Fails twice, succeeds on the third attempt -- every time\n"
                "curl --retry 3 --retry-all-errors \\\n"
                '  "https://cuckootrade.com/api/v1/alpaca/v2/stocks/bars'
                '?symbols=AAPL&scenario=flap:2"',
            ),
        ),
    ),
)
def stock_bars(
    symbols: SymbolsQ,
    timeframe: TimeframeQ = "1Day",
    start: StartQ = None,
    end: EndQ = None,
    limit: LimitQ = None,
    page_token: PageTokenQ = None,
    sort: SortQ = "asc",
    adjustment: AdjustmentQ = None,
    feed: FeedQ = None,
    asof: AlpacaAsofQ = None,
    currency: CurrencyQ = None,
    seed: SeedQ = None,
    generation: GenerationQ = GENERATION,
    as_of: AsOfQ = None,
):
    """Alpaca's `GET /v2/stocks/bars`, byte-faithful enough for its own SDK.

    **The window.** `start` defaults to 30 days before `end`; `end` defaults to
    now. Both are inclusive. Bars are calendar-aligned rather than
    query-aligned, so the same bar comes back whatever window contains it, and
    aggregating finer timeframes reproduces the coarser ones exactly.

    Watch the boundary: `end=2026-07-03` parses as midnight *UTC*, while daily
    bars are stamped midnight *ET* (`04:00:00Z`), so that request excludes the
    July 3rd bar. Pass `end=2026-07-03T23:59:59Z` to include it.

    **Pagination.** `limit` counts total bars across *all* requested symbols,
    not per symbol. When it runs out you get a `next_page_token`; send it back
    as `page_token`, unmodified, and the next page resumes exactly after the
    last bar served. Stop when `next_page_token` is `null` -- a full page can
    still be the last one, so bar count is not a stop condition.

    **Restatement.** `as_of` picks the vantage point and `adjustment` picks
    which action classes apply (default `all`, unlike Alpaca's `raw`). Only
    `SPLITS`, `DIVVY` and `REVISED` have any actions to apply; for everything
    else these are no-ops, and `X-Cuckoo-Restated: 0 actions applied` says so
    rather than leaving you guessing.

    **Caching.** A window that closed more than a day ago is immutable and comes
    back `Cache-Control: public, max-age=31536000, immutable` -- except when a
    restating symbol is in the request without an `as_of` to pin it, since that
    history is precisely what can still change.

    **Accepted and ignored:** `feed`, `currency`, and Alpaca's own `asof`
    (its symbol-mapping date -- not `as_of`, the restatement knob).
    """
    symbol_list = parse_symbols(symbols)
    tf, start_dt, end_dt, capped_limit, seed = parse_common(
        timeframe, start, end, limit, seed, generation
    )
    as_of_dt, adjust = parse_history(as_of, adjustment)
    if sort not in ("asc", "desc"):
        api_error(400, 40010001, f"invalid sort {sort!r}: use sort=asc or sort=desc")
    bars, next_token = paginate_bars(
        symbol_list, tf, start_dt, end_dt, capped_limit, seed, page_token, sort == "desc",
        as_of=as_of_dt, adjustment=adjust,
    )
    response = JSONResponse({"bars": bars, "next_page_token": next_token})
    mark_restatement(response, symbol_list, bars, as_of_dt, adjust)
    maybe_cache_forever(response, bool(end), end_dt, symbols=symbol_list, as_of=as_of_dt)
    return response


@router.get(
    "/v2/stocks/bars/latest",
    summary="Latest completed bar per symbol",
    operation_id="alpaca_latest_bars",
    response_description="One bar per symbol -- an object, not an array.",
    responses={
        200: apidocs.response(
            "The most recent completed bar for each symbol.",
            schema=apidocs.ref("AlpacaLatestBarsResponse"),
            examples={
                "latest": apidocs.example(
                    "One symbol",
                    {"bars": {"CUCKOO": {
                        "c": 545.41, "h": 545.68, "l": 545.14, "n": 1281, "o": 545.16,
                        "t": "2026-08-19T19:06:00Z", "v": 111339, "vw": 545.347196}}},
                    "Note the shape: a bar object per symbol, where "
                    "`/v2/stocks/bars` gives an array.",
                ),
                "omitted": apidocs.example(
                    "Nothing to report",
                    {"bars": {}},
                    "A symbol whose latest bar cannot be determined is omitted "
                    "rather than returned as null. On `HALTS` this is a real "
                    "outcome, not a failure.",
                ),
            },
            headers=apidocs.BAR_HEADERS,
        ),
        400: apidocs.response(
            "A malformed parameter, in Alpaca's error shape.",
            schema=apidocs.ref("AlpacaError"),
            examples={"symbol": _BARS_ERRORS["symbol"]},
        ),
    },
    openapi_extra=apidocs.extras(
        mimics=("Alpaca's own latest-bars reference",
                "https://docs.alpaca.markets/reference/stocklatestbars"),
        samples=(
            (
                "Shell",
                "curl",
                'curl "https://cuckootrade.com/api/v1/alpaca/v2/stocks/bars/latest'
                '?symbols=AAPL,SPY,STALE"',
            ),
        ),
    ),
)
def stock_bars_latest(
    symbols: SymbolsQ,
    timeframe: TimeframeQ = "1Min",
    seed: SeedQ = None,
    generation: GenerationQ = GENERATION,
    adjustment: AdjustmentQ = None,
    as_of: AsOfQ = None,
):
    """The most recent *completed* bar for each symbol, on the NYSE calendar.

    Completed is the operative word: no partial in-progress bar is ever served,
    because a bar that is still forming could not be deterministic. Outside
    market hours this returns the last bar of the previous session rather than
    nothing.

    `timeframe` defaults to `1Min` here, not `1Day`. Two symbols are worth
    pointing at it: `STALE` returns a fresh-looking bar whose timestamp has
    stopped advancing, and `HALTS` can legitimately have no latest bar at all.
    """
    symbol_list = parse_symbols(symbols)
    tf, _, _, _, seed = parse_common(timeframe, None, None, None, seed, generation)
    as_of_dt, adjust = parse_history(as_of, adjustment)
    bars = {
        s: bar
        for s in symbol_list
        if (bar := latest_bar(s, tf, seed=seed, as_of=as_of_dt, adjustment=adjust))
        is not None
    }
    response = JSONResponse({"bars": bars})
    # One bar per symbol, so the window each action is measured against is
    # that single bar.
    mark_restatement(response, symbol_list, {k: [v] for k, v in bars.items()},
                     as_of_dt, adjust)
    return response


@router.get(
    "/v2/stocks/{symbol}/bars",
    summary="Historical bars (single symbol)",
    operation_id="alpaca_stock_bars_single",
    response_description="A bare array of bars, plus the symbol and a pagination token.",
    responses={
        200: apidocs.response(
            "Bars for the one symbol in the path.",
            schema=apidocs.ref("AlpacaSingleBarsResponse"),
            examples={
                "single": apidocs.example(
                    "One symbol",
                    {"bars": _AAPL_BARS, "symbol": "AAPL", "next_page_token": apidocs.NULL},
                    "`bars` is an array here, not a map keyed by symbol -- the one "
                    "shape difference from `/v2/stocks/bars`.",
                )
            },
            headers=apidocs.BAR_HEADERS,
            links={
                "nextPage": apidocs.link(
                    "When `next_page_token` is not null, send it back as "
                    "`page_token` -- unmodified -- for the next page.",
                    "alpaca_stock_bars_single",
                    {"symbol": "$response.body#/symbol",
                     "page_token": "$response.body#/next_page_token"},
                )
            },
        ),
        400: apidocs.response(
            "A malformed parameter, in Alpaca's error shape.",
            schema=apidocs.ref("AlpacaError"),
            examples=_BARS_ERRORS,
        ),
    },
    openapi_extra=apidocs.extras(
        mimics=ALPACA_DOCS,
        samples=(
            (
                "Shell",
                "curl",
                'curl "https://cuckootrade.com/api/v1/alpaca/v2/stocks/AAPL/bars'
                '?timeframe=15Min&start=2026-07-01&end=2026-07-02"',
            ),
        ),
    ),
)
def stock_bars_single(
    symbol: SymbolPathQ,
    timeframe: TimeframeQ = "1Day",
    start: StartQ = None,
    end: EndQ = None,
    limit: LimitQ = None,
    page_token: PageTokenQ = None,
    sort: SortQ = "asc",
    adjustment: AdjustmentQ = None,
    feed: FeedQ = None,
    asof: AlpacaAsofQ = None,
    currency: CurrencyQ = None,
    seed: SeedQ = None,
    generation: GenerationQ = GENERATION,
    as_of: AsOfQ = None,
):
    """The single-symbol variant, taking the symbol in the path.

    Identical to `/v2/stocks/bars` in every respect but the response shape:
    `bars` is a bare array rather than a map, and `symbol` echoes back the
    normalized ticker. Every parameter above behaves the same way, pagination
    and restatement included.
    """
    (sym,) = parse_symbols(symbol, cap=1)
    tf, start_dt, end_dt, capped_limit, seed = parse_common(
        timeframe, start, end, limit, seed, generation
    )
    as_of_dt, adjust = parse_history(as_of, adjustment)
    if sort not in ("asc", "desc"):
        api_error(400, 40010001, f"invalid sort {sort!r}: use sort=asc or sort=desc")
    bars, next_token = paginate_bars(
        [sym], tf, start_dt, end_dt, capped_limit, seed, page_token, sort == "desc",
        as_of=as_of_dt, adjustment=adjust,
    )
    response = JSONResponse(
        {"bars": bars[sym], "symbol": sym, "next_page_token": next_token}
    )
    mark_restatement(response, [sym], bars, as_of_dt, adjust)
    maybe_cache_forever(response, bool(end), end_dt, symbols=[sym], as_of=as_of_dt)
    return response
