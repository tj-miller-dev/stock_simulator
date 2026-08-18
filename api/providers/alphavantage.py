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

from fastapi import APIRouter
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


def _error(message: str) -> JSONResponse:
    # Alpha Vantage reports errors as HTTP 200 with this exact key.
    return JSONResponse({"Error Message": message})


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


@router.get("/query")
def query(
    function: str | None = None,
    symbol: str | None = None,
    interval: str | None = None,
    outputsize: str = "compact",
    month: str | None = None,
    datatype: str = "json",
    apikey: str | None = None,          # accepted, ignored: keyless by design
    adjusted: str | None = None,        # accepted, ignored: no corporate actions
    extended_hours: str | None = None,  # accepted, ignored: RTH only
    seed: str | None = None,            # Cuckoo extension: alternate dataset
    generation: int = GENERATION,       # Cuckoo extension: pin generator version
):
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
    if function not in (
        "TIME_SERIES_INTRADAY", "TIME_SERIES_DAILY", "TIME_SERIES_WEEKLY",
        "TIME_SERIES_MONTHLY", "GLOBAL_QUOTE",
    ):
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
