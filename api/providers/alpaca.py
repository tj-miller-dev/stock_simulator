"""Alpaca-compatible surface: /api/v1/alpaca/<Alpaca's own paths>.

Replicates data.alpaca.markets: same paths, params, JSON shapes, pagination
semantics, and error style ({"code", "message"}). The acceptance test
(tests/test_alpaca_sdk.py) is the contract: alpaca-py pointed here via
url_override works unmodified.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from common import (
    EXAMPLE,
    api_error,
    maybe_cache_forever,
    parse_common,
    parse_symbols,
    paginate_bars,
)
from engine import GENERATION, latest_bar

router = APIRouter(prefix="/api/v1/alpaca", tags=["provider: alpaca"])

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


@router.get("/v2/stocks/bars")
def stock_bars(
    symbols: str,
    timeframe: str = "1Day",
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    page_token: str | None = None,
    sort: str = "asc",
    adjustment: str | None = None,  # accepted, ignored: no corporate actions in V1
    feed: str | None = None,        # accepted, ignored: there is only one feed here
    asof: str | None = None,        # accepted, ignored
    currency: str | None = None,    # accepted, ignored
    seed: str | None = None,        # Cuckoo extension: alternate dataset
    generation: int = GENERATION,   # Cuckoo extension: pin generator version
):
    symbol_list = parse_symbols(symbols)
    tf, start_dt, end_dt, capped_limit, seed = parse_common(
        timeframe, start, end, limit, seed, generation
    )
    if sort not in ("asc", "desc"):
        api_error(400, 40010001, f"invalid sort {sort!r}: use sort=asc or sort=desc")
    bars, next_token = paginate_bars(
        symbol_list, tf, start_dt, end_dt, capped_limit, seed, page_token, sort == "desc"
    )
    response = JSONResponse({"bars": bars, "next_page_token": next_token})
    maybe_cache_forever(response, bool(end), end_dt)
    return response


@router.get("/v2/stocks/bars/latest")
def stock_bars_latest(
    symbols: str,
    timeframe: str = "1Min",
    seed: str | None = None,
    generation: int = GENERATION,
):
    symbol_list = parse_symbols(symbols)
    tf, _, _, _, seed = parse_common(timeframe, None, None, None, seed, generation)
    return {
        "bars": {
            s: bar for s in symbol_list if (bar := latest_bar(s, tf, seed=seed)) is not None
        }
    }


@router.get("/v2/stocks/{symbol}/bars")
def stock_bars_single(
    symbol: str,
    timeframe: str = "1Day",
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    page_token: str | None = None,
    sort: str = "asc",
    adjustment: str | None = None,
    feed: str | None = None,
    asof: str | None = None,
    currency: str | None = None,
    seed: str | None = None,
    generation: int = GENERATION,
):
    (sym,) = parse_symbols(symbol, cap=1)
    tf, start_dt, end_dt, capped_limit, seed = parse_common(
        timeframe, start, end, limit, seed, generation
    )
    if sort not in ("asc", "desc"):
        api_error(400, 40010001, f"invalid sort {sort!r}: use sort=asc or sort=desc")
    bars, next_token = paginate_bars(
        [sym], tf, start_dt, end_dt, capped_limit, seed, page_token, sort == "desc"
    )
    response = JSONResponse(
        {"bars": bars[sym], "symbol": sym, "next_page_token": next_token}
    )
    maybe_cache_forever(response, bool(end), end_dt)
    return response
