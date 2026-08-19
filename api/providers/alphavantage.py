"""Alpha Vantage-compatible surface: /api/v1/alphavantage/query?function=...

Replicates www.alphavantage.co's wire format, verified against live
responses (Aug 2026): stringified OHLCV maps keyed newest-first, numbered
"Meta Data" fields per function, GLOBAL_QUOTE's zero-padded keys, and --
faithfully -- errors as HTTP 200 with an {"Error Message": ...} body, which
is how Alpha Vantage actually reports them (client libraries sniff for that
key). apikey is accepted and ignored.

Deliberate deviations, documented on the docs page: only completed bars are
served (no partial current day/week/month row -- determinism requires it),
and sessions are RTH-only (no extended hours).
"""

from datetime import date, datetime, timedelta, timezone
from typing import Annotated

import apidocs
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from common import SYMBOL_RE
from engine import GENERATION, Timeframe, bars_range, is_trading_day, parse_timeframe, prev_trading_day
from engine.market_calendar import ET, session_close_utc

router = APIRouter(prefix="/api/v1/alphavantage", tags=["provider: alphavantage"])

AV_EXAMPLE = "/api/v1/alphavantage/query?function=TIME_SERIES_DAILY&symbol=IBM"
INTERVALS = ("1min", "5min", "15min", "30min", "60min")
COMPACT_POINTS = 100
FULL_YEARS = 20

INDEX_ENTRY = {
    "status": "available",
    "base_url": "https://cuckootrade.com/api/v1/alphavantage",
    "compatible_with": "https://www.alphavantage.co",
    "sdk_hint": "any Alpha Vantage client pointed at the base_url; "
    "apikey accepted and ignored",
    "endpoints": [
        {
            "method": "GET",
            "path": "/api/v1/alphavantage/query",
            "purpose": "Alpha Vantage-compatible time series: TIME_SERIES_INTRADAY, "
            "TIME_SERIES_DAILY, TIME_SERIES_WEEKLY, TIME_SERIES_MONTHLY, GLOBAL_QUOTE",
            "example": AV_EXAMPLE,
        },
    ],
}


AV_DOCS = ("Alpha Vantage's own documentation",
           "https://www.alphavantage.co/documentation/")

FUNCTIONS = (
    "TIME_SERIES_INTRADAY", "TIME_SERIES_DAILY", "TIME_SERIES_WEEKLY",
    "TIME_SERIES_MONTHLY", "GLOBAL_QUOTE",
)

# Alpha Vantage puts the endpoint selector in a query parameter, so `function`
# does the work a path would elsewhere. It gets the fullest documentation here
# for that reason: pick the wrong one and nothing else on this page applies.
FunctionQ = Annotated[
    str | None,
    Query(
        description=(
            "Which series to return. Alpha Vantage has one path and switches on this "
            "parameter, so it decides which of the response shapes below you get -- "
            "and which other parameters mean anything.\n\n"
            "- `TIME_SERIES_INTRADAY` -- requires `interval`; honours `month` and "
            "`outputsize`\n"
            "- `TIME_SERIES_DAILY` -- honours `outputsize` (~100 bars compact, ~20 "
            "years full)\n"
            "- `TIME_SERIES_WEEKLY` / `TIME_SERIES_MONTHLY` -- always ~20 years; "
            "`outputsize` is inert\n"
            "- `GLOBAL_QUOTE` -- a single latest-quote object, not a series"
        ),
        json_schema_extra={"enum": list(FUNCTIONS)},
        openapi_examples={
            "daily": {"summary": "Daily bars", "value": "TIME_SERIES_DAILY"},
            "intraday": {
                "summary": "Intraday bars",
                "description": "Requires `interval`, e.g. `interval=5min`.",
                "value": "TIME_SERIES_INTRADAY",
            },
            "weekly": {"summary": "Weekly bars", "value": "TIME_SERIES_WEEKLY"},
            "quote": {
                "summary": "Latest quote",
                "description": "A different shape entirely -- one object, zero-padded keys.",
                "value": "GLOBAL_QUOTE",
            },
        },
    ),
]

AvSymbolQ = Annotated[
    str | None,
    Query(
        description=(
            "One symbol -- this API takes no comma lists. Letters, digits, dot and "
            "dash, 12 characters max, case-insensitive. Any well-formed symbol "
            "returns data; the scenario tickers are the ones with scripted behavior."
        ),
        openapi_examples={
            "ordinary": {"summary": "An ordinary symbol", "value": "IBM"},
            "scenario": {
                "summary": "A scenario ticker",
                "description": "Zero-range bars: open, high, low and close all equal.",
                "value": "FLAT",
            },
        },
    ),
]

IntervalQ = Annotated[
    str | None,
    Query(
        description=(
            "Bar size for `TIME_SERIES_INTRADAY`, where it is **required**. Ignored "
            "by every other function."
        ),
        json_schema_extra={"enum": list(INTERVALS)},
    ),
]

OutputSizeQ = Annotated[
    str,
    Query(
        description=(
            f"`compact` returns the latest {COMPACT_POINTS} points; `full` returns "
            f"the trailing {FULL_YEARS} years (or 30 days for intraday). Inert for "
            "weekly, monthly and `GLOBAL_QUOTE`, which have a fixed size."
        ),
        json_schema_extra={"enum": ["compact", "full"]},
    ),
]

MonthQ = Annotated[
    str | None,
    Query(
        description=(
            "Restrict `TIME_SERIES_INTRADAY` to one calendar month, `YYYY-MM`. Takes "
            "precedence over `outputsize`, and is the way to reach intraday history "
            "older than 30 days."
        ),
        openapi_examples={"month": {"summary": "One month", "value": "2026-07"}},
    ),
]

DatatypeQ = Annotated[
    str,
    Query(
        description=(
            "Only `json` is implemented. `datatype=csv` returns an `Error Message` "
            "rather than pretending -- the real API supports it, this mimic does not "
            "yet."
        ),
        json_schema_extra={"enum": ["json"]},
    ),
]

ApiKeyQ = Annotated[
    str | None,
    Query(
        description=(
            "Accepted and ignored -- CuckooTrade is keyless. Present so a client "
            "already configured with a real key works unchanged. `demo` works too, "
            "as does anything else."
        ),
    ),
]

AdjustedQ = Annotated[
    str | None,
    Query(
        description=(
            "Accepted and ignored: this surface serves as-traded prices and does not "
            "restate. Corporate actions live on the Alpaca surface, behind `as_of`."
        ),
    ),
]

ExtendedHoursQ = Annotated[
    str | None,
    Query(
        description=(
            "Accepted and ignored: sessions here are regular hours only "
            "(09:30-16:00 ET), so there is no extended-hours data to include or omit."
        ),
    ),
]

AvSeedQ = Annotated[str | None, Query(description=apidocs.SEED_TEXT)]
AvGenerationQ = Annotated[int, Query(description=apidocs.GENERATION_TEXT)]


def _error(message: str) -> JSONResponse:
    # Alpha Vantage reports errors as HTTP 200 with this exact key.
    return JSONResponse({"Error Message": message})


def fault_response(status: int, message: str) -> JSONResponse:
    """An injected fault, wearing Alpha Vantage's error shape. The real API
    reports errors as HTTP 200; here the caller named the status they wanted to
    test against, so that wins over the mimicry."""
    return JSONResponse(status_code=status, content={"Error Message": message})


def _values(bar: dict) -> dict:
    return {
        "1. open": f"{bar['o']:.4f}",
        "2. high": f"{bar['h']:.4f}",
        "3. low": f"{bar['l']:.4f}",
        "4. close": f"{bar['c']:.4f}",
        "5. volume": str(bar["v"]),
    }


def _daily_label(bar: dict) -> str:
    return bar["t"][:10]


def _week_label(bar: dict) -> str:
    monday = date.fromisoformat(bar["t"][:10])
    friday = monday + timedelta(days=4)
    return (friday if is_trading_day(friday) else prev_trading_day(friday)).isoformat()


def _month_label(bar: dict) -> str:
    first = date.fromisoformat(bar["t"][:10])
    last_cal = date(first.year + first.month // 12, first.month % 12 + 1, 1) - timedelta(days=1)
    return (last_cal if is_trading_day(last_cal) else prev_trading_day(last_cal)).isoformat()


def _intraday_label(bar: dict, minutes: int) -> str:
    start = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
    end = start + timedelta(minutes=minutes)
    close = session_close_utc(start.astimezone(ET).date())
    return min(end, close).astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")


def _series(symbol, timeframe, start, end, max_bars, seed):
    """Newest-first completed bars, the order Alpha Vantage serializes in."""
    bars, _ = bars_range(
        symbol, timeframe, start, end, seed=seed, max_bars=max_bars, descending=True
    )
    return bars


def _output_size(outputsize: str) -> str:
    return "Full size" if outputsize == "full" else "Compact"


_AV_EXAMPLES = {
    "daily": apidocs.example(
        "TIME_SERIES_DAILY",
        {
            "Meta Data": {
                "1. Information": "Daily Prices (open, high, low, close) and Volumes",
                "2. Symbol": "IBM",
                "3. Last Refreshed": "2026-08-18",
                "4. Output Size": "Compact",
                "5. Time Zone": "US/Eastern",
            },
            "Time Series (Daily)": {
                "2026-08-18": {"1. open": "169.9900", "2. high": "173.5800",
                               "3. low": "169.9000", "4. close": "172.5600",
                               "5. volume": "3416035"},
                "2026-08-17": {"1. open": "173.6900", "2. high": "175.1400",
                               "3. low": "170.1300", "4. close": "170.1900",
                               "5. volume": "3669152"},
            },
        },
        "Every number is a **string**, keys are numbered, and the series is a map "
        "keyed newest-first rather than an array. All three are Alpha Vantage's "
        "real conventions, reproduced exactly.",
    ),
    "intraday": apidocs.example(
        "TIME_SERIES_INTRADAY",
        {
            "Meta Data": {
                "1. Information": "Intraday (5min) open, high, low, close prices and volume",
                "2. Symbol": "IBM",
                "3. Last Refreshed": "2026-08-18 16:00:00",
                "4. Interval": "5min",
                "5. Output Size": "Compact",
                "6. Time Zone": "US/Eastern",
            },
            "Time Series (5min)": {
                "2026-08-18 16:00:00": {"1. open": "172.4100", "2. high": "172.6200",
                                        "3. low": "172.3300", "4. close": "172.5600",
                                        "5. volume": "48219"},
            },
        },
        "Intraday labels are the bar's **close** in US/Eastern local time, not its "
        "open in UTC -- the opposite convention to the Alpaca surface. The meta "
        "block gains a `4. Interval` field, shifting Time Zone to `6.`.",
    ),
    "quote": apidocs.example(
        "GLOBAL_QUOTE",
        {"Global Quote": {
            "01. symbol": "IBM", "02. open": "169.9900", "03. high": "173.5800",
            "04. low": "169.9000", "05. price": "172.5600", "06. volume": "3416035",
            "07. latest trading day": "2026-08-18", "08. previous close": "170.1900",
            "09. change": "2.3700", "10. change percent": "1.3926%"}},
        "A different shape entirely: one object, zero-padded keys, and "
        "`10. change percent` carrying a literal `%` inside the string.",
    ),
    "error": apidocs.example(
        "An error -- still HTTP 200",
        {"Error Message":
         "Invalid API call. This synthetic mimic supports function="
         "TIME_SERIES_INTRADAY, TIME_SERIES_DAILY, TIME_SERIES_WEEKLY, "
         "TIME_SERIES_MONTHLY, or GLOBAL_QUOTE. Working example: " + AV_EXAMPLE},
        "**This is the important one.** Alpha Vantage reports failures with a 200 "
        "and an `Error Message` key, and so does this. Check for the key; the "
        "status code will not tell you.",
    ),
}


@router.get(
    "/query",
    summary="Time series and quotes (function-switched)",
    operation_id="alphavantage_query",
    response_description="The shape selected by `function` -- or an `Error Message`, also with status 200.",
    responses={
        200: apidocs.response(
            "**Success and failure both arrive as 200.** A body carrying an "
            "`Error Message` key is a failure however healthy the status line "
            "looks; anything else is one of the series shapes below, chosen by "
            "`function`.",
            schema={
                "oneOf": [
                    apidocs.ref("AlphaVantageSeries"),
                    apidocs.ref("AlphaVantageQuote"),
                    apidocs.ref("AlphaVantageError"),
                ]
            },
            examples=_AV_EXAMPLES,
        ),
    },
    openapi_extra=apidocs.extras(
        mimics=AV_DOCS,
        samples=(
            (
                "Shell",
                "curl",
                'curl "https://cuckootrade.com/api/v1/alphavantage/query'
                '?function=TIME_SERIES_DAILY&symbol=IBM"',
            ),
            (
                "Python",
                "requests",
                "import requests\n\n"
                'r = requests.get("https://cuckootrade.com/api/v1/alphavantage/query",\n'
                '                 params={"function": "TIME_SERIES_DAILY",\n'
                '                         "symbol": "IBM"}).json()\n\n'
                "# Check the key, not the status code -- errors arrive as 200.\n"
                'if "Error Message" in r:\n'
                '    raise SystemExit(r["Error Message"])\n\n'
                'series = r["Time Series (Daily)"]\n'
                'for day, ohlcv in list(series.items())[:5]:      # newest first\n'
                '    print(day, float(ohlcv["4. close"]))         # values are strings',
            ),
        ),
    ),
)
def query(
    function: FunctionQ = None,
    symbol: AvSymbolQ = None,
    interval: IntervalQ = None,
    outputsize: OutputSizeQ = "compact",
    month: MonthQ = None,
    datatype: DatatypeQ = "json",
    apikey: ApiKeyQ = None,              # accepted, ignored: keyless by design
    adjusted: AdjustedQ = None,          # accepted, ignored: no corporate actions
    extended_hours: ExtendedHoursQ = None,  # accepted, ignored: RTH only
    seed: AvSeedQ = None,                # Cuckoo extension: alternate dataset
    generation: AvGenerationQ = GENERATION,  # Cuckoo extension: pin generator version
):
    """Alpha Vantage's single `query` endpoint, switched by `function`.

    **Errors come back as HTTP 200.** That is not a bug here: the real API does
    it, and its client libraries sniff for the `Error Message` key rather than
    checking the status. Any code pointed at this surface must do the same. The
    one exception is `scenario=status:CODE`, where you have explicitly asked for
    a status to test against and that wins over the mimicry.

    **Everything is a string.** Prices and volumes are quoted, keys are numbered
    (`"1. open"`, `"5. volume"`), and series are maps keyed newest-first rather
    than arrays. Parsers that assume JSON numbers break here, which is a large
    part of why this surface is worth testing against.

    **Labelling differs per function.** Daily rows are keyed `YYYY-MM-DD`; weekly
    and monthly rows are keyed by the *last trading day* of the period; intraday
    rows are keyed by the bar's **close** in US/Eastern local time -- the
    opposite convention to the Alpaca surface's UTC open timestamps.

    **Deviations:** only completed bars are served, so there is no partial
    current day/week/month row (determinism requires it); sessions are regular
    hours only, so `adjusted` and `extended_hours` are inert; `apikey` is
    accepted and ignored; and `datatype=csv` is not implemented.
    """
    if generation != GENERATION:
        return _error(
            f"unknown generation {generation}: this deployment serves generation "
            f"{GENERATION}. Omit the parameter for the current generation."
        )
    if datatype not in ("json", "csv"):
        return _error(f"invalid datatype {datatype!r}: use datatype=json.")
    if datatype == "csv":
        return _error(
            "datatype=csv is not supported by this synthetic mimic yet; omit the "
            "parameter or pass datatype=json."
        )
    if function not in FUNCTIONS:
        return _error(
            f"Invalid API call. This synthetic mimic supports function="
            f"TIME_SERIES_INTRADAY, TIME_SERIES_DAILY, TIME_SERIES_WEEKLY, "
            f"TIME_SERIES_MONTHLY, or GLOBAL_QUOTE. Working example: {AV_EXAMPLE}"
        )
    if not symbol or not SYMBOL_RE.match(symbol.strip()):
        return _error(
            f"invalid or missing symbol: letters, digits, dot and dash, max 12 "
            f"chars. Any well-formed symbol works. Working example: {AV_EXAMPLE}"
        )
    if outputsize not in ("compact", "full"):
        return _error(f"invalid outputsize {outputsize!r}: use compact or full.")

    sym = symbol.strip().upper()
    seed = seed or ""
    now = datetime.now(timezone.utc)

    if function == "GLOBAL_QUOTE":
        bars = _series(sym, parse_timeframe("1Day"), now - timedelta(days=15), now, 2, seed)
        if len(bars) < 2:
            return {"Global Quote": {}}
        last, prev = bars[0], bars[1]
        change = round(last["c"] - prev["c"], 4)
        return {
            "Global Quote": {
                "01. symbol": sym,
                "02. open": f"{last['o']:.4f}",
                "03. high": f"{last['h']:.4f}",
                "04. low": f"{last['l']:.4f}",
                "05. price": f"{last['c']:.4f}",
                "06. volume": str(last["v"]),
                "07. latest trading day": _daily_label(last),
                "08. previous close": f"{prev['c']:.4f}",
                "09. change": f"{change:.4f}",
                "10. change percent": f"{change / prev['c'] * 100:.4f}%",
            }
        }

    if function == "TIME_SERIES_INTRADAY":
        if interval not in INTERVALS:
            return _error(
                f"invalid or missing interval for TIME_SERIES_INTRADAY: use one of "
                f"{', '.join(INTERVALS)}. Working example: {AV_EXAMPLE.replace('TIME_SERIES_DAILY', 'TIME_SERIES_INTRADAY')}&interval=5min"
            )
        timeframe = Timeframe(int(interval.removesuffix("min")), "Min")
        if month is not None:
            try:
                anchor = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
            except ValueError:
                return _error(f"invalid month {month!r}: use YYYY-MM, e.g. month=2026-07.")
            month_end = (anchor.replace(day=28) + timedelta(days=6)).replace(
                day=1
            ) - timedelta(seconds=1)
            start, end, max_bars = anchor, min(month_end, now), 10_000
        elif outputsize == "full":
            start, end, max_bars = now - timedelta(days=30), now, 10_000
        else:
            start, end, max_bars = now - timedelta(days=10), now, COMPACT_POINTS
        bars = _series(sym, timeframe, start, end, max_bars, seed)
        if not bars:
            return _error(
                f"no completed bars for {sym} in that window -- the NYSE calendar "
                f"has no sessions there, or the window is in the future."
            )
        label = lambda b: _intraday_label(b, timeframe.minutes)  # noqa: E731
        return {
            "Meta Data": {
                "1. Information": f"Intraday ({interval}) open, high, low, close prices and volume",
                "2. Symbol": sym,
                "3. Last Refreshed": label(bars[0]),
                "4. Interval": interval,
                "5. Output Size": _output_size(outputsize),
                "6. Time Zone": "US/Eastern",
            },
            f"Time Series ({interval})": {label(b): _values(b) for b in bars},
        }

    # The daily/weekly/monthly family shares one shape.
    spec = {
        "TIME_SERIES_DAILY": ("1Day", "Daily Prices (open, high, low, close) and Volumes",
                              "Time Series (Daily)", _daily_label),
        "TIME_SERIES_WEEKLY": ("1Week", "Weekly Prices (open, high, low, close) and Volumes",
                               "Weekly Time Series", _week_label),
        "TIME_SERIES_MONTHLY": ("1Month", "Monthly Prices (open, high, low, close) and Volumes",
                                "Monthly Time Series", _month_label),
    }[function]
    timeframe_text, information, series_key, label = spec
    if function == "TIME_SERIES_DAILY" and outputsize == "compact":
        start, max_bars = now - timedelta(days=220), COMPACT_POINTS
    else:
        # daily full and weekly/monthly: Alpha Vantage serves ~20 years.
        start, max_bars = now - timedelta(days=FULL_YEARS * 365), 10_000
    bars = _series(sym, parse_timeframe(timeframe_text), start, now, max_bars, seed)
    if not bars:
        return _error(f"no completed bars for {sym} in the trailing window.")
    meta = {
        "1. Information": information,
        "2. Symbol": sym,
        "3. Last Refreshed": label(bars[0]),
    }
    if function == "TIME_SERIES_DAILY":
        meta["4. Output Size"] = _output_size(outputsize)
        meta["5. Time Zone"] = "US/Eastern"
    else:
        meta["4. Time Zone"] = "US/Eastern"
    return {"Meta Data": meta, series_key: {label(b): _values(b) for b in bars}}
