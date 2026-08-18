"""CuckooTrade HTTP API: provider-wire-compatible synthetic market data.

Path scheme: /api/v1/{provider}/<provider's own path>. Everything through the
provider segment is CuckooTrade's namespace (the v1 versions this API's
surface; the `generation` param versions the data). Everything after it
mimics that provider's wire format, so its SDKs work with only a base-URL
override. Alpaca is the first provider; future ones (an Alpha Vantage-shaped
/api/v1/alphavantage/query, an IBKR surface, ...) mount alongside as their
own routers without touching existing paths.

The compatibility contract (V1_SPEC 3.1): alpaca-py's historical data client,
pointed at /api/v1/alpaca via url_override, works unmodified. Wire-compat
responses carry no extra body fields -- strict SDK parsers must never choke
-- so the synthetic marking rides in X-Cuckoo-* headers instead.
Cuckoo-native endpoints (/api, /api/v1/stream) carry full metadata in the
body.

Error messages teach: they state the valid grammar and include a working
example, because errors are read at the exact moment someone (or some agent)
is stuck.
"""

import base64
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engine import GENERATION, bars_range, latest_bar, parse_timeframe
from ratelimit import TokenBucketLimiter, client_ip
from stream import router as stream_router

DOCS_URL = "https://cuckootrade.com/docs"
DISCLAIMER = (
    "All data is synthetic. CuckooTrade exists for exercising code paths -- "
    "development, CI, demos, tutorials -- never for validating trading "
    "strategies: a profitable backtest on synthetic data means nothing."
)

BARS_DEFAULT_LIMIT = 1000
BARS_MAX_LIMIT = 10_000
BARS_DEFAULT_LOOKBACK = timedelta(days=30)
BARS_MAX_SYMBOLS = 50
_SYMBOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,11}$")

EXAMPLE = "/api/v1/alpaca/v2/stocks/bars?symbols=AAPL,CRASH&timeframe=1Day&start=2026-07-01"

# The docs endpoints hang off the app, not off the /api-prefixed routes, so
# they must be prefixed by hand or the ALB routes them to the frontend.
app = FastAPI(
    title="CuckooTrade",
    description=DISCLAIMER,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

limiter = TokenBucketLimiter()


def api_error(status: int, code: int, message: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if not isinstance(detail, dict):
        detail = {"code": 40010000, "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err['loc'][1:])}: {err['msg']}" for err in exc.errors()
    )
    return JSONResponse(
        status_code=400,
        content={
            "code": 40010001,
            "message": f"invalid request ({problems}) -- working example: {EXAMPLE}",
        },
    )


@app.middleware("http")
async def cuckoo_middleware(request: Request, call_next):
    """Synthetic marking on every response, plus keyless per-IP rate limiting
    (health checks exempt so kubelet probes never starve behind a scan)."""
    path = request.url.path
    limited = path.startswith("/api") and path != "/api/health"
    if limited:
        allowed, remaining, reset = limiter.check(client_ip(request))
        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={
                    "code": 42910000,
                    "message": "rate limit exceeded: 60 requests/minute sustained "
                    "(burst 120) per address, no key required. Back off using the "
                    "RateLimit-Reset header.",
                },
            )
        else:
            response = await call_next(request)
        response.headers["RateLimit-Limit"] = str(int(limiter.capacity))
        response.headers["RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["RateLimit-Reset"] = str(reset)
    else:
        response = await call_next(request)
    response.headers["X-Cuckoo-Synthetic"] = "true"
    response.headers["X-Cuckoo-Generation"] = str(GENERATION)
    response.headers["X-Cuckoo-Docs"] = DOCS_URL
    return response


def parse_symbols(raw: str, cap: int = BARS_MAX_SYMBOLS) -> list[str]:
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not symbols:
        api_error(400, 40010001, f"symbols is required -- working example: {EXAMPLE}")
    if len(symbols) > cap:
        api_error(400, 40010001, f"too many symbols: {len(symbols)} (max {cap})")
    for s in symbols:
        if not _SYMBOL_RE.match(s):
            api_error(
                400,
                40010001,
                f"invalid symbol {s!r}: letters, digits, dot and dash, max 12 chars. "
                f"Any well-formed symbol works -- unknown ones get a stable "
                f"hash-derived personality. Working example: {EXAMPLE}",
            )
    # Deduplicate preserving nothing but membership: responses key by symbol,
    # and pagination iterates alphabetically like Alpaca.
    return sorted(set(symbols))


def parse_time(value: str, param: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        api_error(
            400,
            40010001,
            f"invalid {param} {value!r}: use RFC-3339 or YYYY-MM-DD, e.g. "
            f"{param}=2026-07-01 or {param}=2026-07-01T13:30:00Z",
        )
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_common(timeframe, start, end, limit, seed, generation):
    try:
        tf = parse_timeframe(timeframe)
    except ValueError as e:
        api_error(400, 40010001, f"{e} -- working example: {EXAMPLE}")
    if generation != GENERATION:
        api_error(
            400,
            40010001,
            f"unknown generation {generation}: this deployment serves generation "
            f"{GENERATION}. Omit the parameter for the current generation.",
        )
    now = datetime.now(timezone.utc)
    end_dt = parse_time(end, "end") if end else now
    start_dt = parse_time(start, "start") if start else end_dt - BARS_DEFAULT_LOOKBACK
    if limit is not None and not (1 <= limit <= BARS_MAX_LIMIT):
        api_error(
            400,
            40010001,
            f"invalid limit {limit}: 1 to {BARS_MAX_LIMIT}. limit counts total bars "
            f"across all requested symbols; page through next_page_token for more.",
        )
    return tf, start_dt, end_dt, (limit or BARS_DEFAULT_LIMIT), seed or ""


def encode_token(symbol: str, last_t: str) -> str:
    return base64.urlsafe_b64encode(f"v1|{symbol}|{last_t}".encode()).decode()


def decode_token(token: str) -> tuple[str, str]:
    try:
        version, symbol, last_t = base64.urlsafe_b64decode(token.encode()).decode().split("|")
        assert version == "v1"
        return symbol, last_t
    except Exception:
        api_error(
            400,
            40010001,
            "invalid page_token: pass the next_page_token value from the previous "
            "response, unmodified.",
        )


def paginate_bars(symbols, tf, start_dt, end_dt, limit, seed, page_token, descending):
    """Alpaca pagination semantics: symbols alphabetically, `limit` counts
    total bars across symbols, next_page_token resumes exactly after the last
    bar served."""
    resume_symbol, resume_t = (None, None)
    if page_token:
        resume_symbol, resume_t = decode_token(page_token)

    bars_by_symbol: dict[str, list] = {}
    budget = limit
    next_token = None
    for symbol in symbols:
        if resume_symbol is not None and symbol < resume_symbol:
            bars_by_symbol[symbol] = []
            continue
        sym_start, sym_end = start_dt, end_dt
        if symbol == resume_symbol and resume_t is not None:
            edge = parse_time(resume_t, "page_token") + (
                timedelta(seconds=-1) if descending else timedelta(seconds=1)
            )
            if descending:
                sym_end = min(sym_end, edge)
            else:
                sym_start = max(sym_start, edge)
        # One-bar lookahead distinguishes "exactly fit" from "more remain".
        bars, truncated = bars_range(
            symbol, tf, sym_start, sym_end, seed=seed,
            max_bars=budget + 1, descending=descending,
        )
        if len(bars) > budget:
            served = bars[:budget]
            bars_by_symbol[symbol] = served
            next_token = encode_token(symbol, served[-1]["t"])
            budget = 0
            break
        bars_by_symbol[symbol] = bars
        budget -= len(bars)
    return bars_by_symbol, next_token


# ---- provider surface: Alpaca ----------------------------------------------
# Paths under this router replicate data.alpaca.markets exactly; nothing
# CuckooTrade-specific may leak into them beyond the seed/generation params.
alpaca = APIRouter(prefix="/api/v1/alpaca", tags=["provider: alpaca"])


@alpaca.get("/v2/stocks/bars")
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
    seed: str | None = None,        # Cuckoo extension: alternate universe
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
    _maybe_cache_forever(response, end, end_dt)
    return response


@alpaca.get("/v2/stocks/bars/latest")
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


@alpaca.get("/v2/stocks/{symbol}/bars")
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
    _maybe_cache_forever(response, end, end_dt)
    return response


def _maybe_cache_forever(response, end_param, end_dt) -> None:
    """History never changes here: a fully-specified window that ended in the
    past is immutable, so say so and let any cache keep it forever."""
    if end_param and end_dt < datetime.now(timezone.utc) - timedelta(days=1):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api")
def index():
    """Machine-readable index: enough for an agent that lands here with no
    other context to make its first successful call."""
    return {
        "name": "CuckooTrade",
        "synthetic": True,
        "api_version": 1,
        "generation": GENERATION,
        "tagline": "Deterministic synthetic market data. No key, no signup.",
        "disclaimer": DISCLAIMER,
        "path_scheme": (
            "/api/v1/{provider}/<provider's own path> mimics that provider's "
            "wire format (point its SDK there via a base-URL override); "
            "/api/v1/... without a provider segment is CuckooTrade-native. "
            "The path version covers this API's surface; the `generation` "
            "parameter versions the data itself."
        ),
        "docs": {
            "site": DOCS_URL,
            "openapi": "/api/openapi.json",
            "swagger": "/api/docs",
            "llms": "https://cuckootrade.com/llms.txt",
        },
        "determinism": (
            "Identical requests return identical bars, forever, within a "
            "generation. Add &seed=<anything> for a different but equally "
            "deterministic dataset."
        ),
        "magic_tickers": {
            "CRASH": "sharp ~25% crash mid-month, slow recovery",
            "MOON": "parabolic monthly pump, sharp correction",
            "FLAT": "zero-range bars, constant price",
            "GAPPY": "large overnight gaps most days",
            "HALTS": "missing minute bars during intraday halt windows",
            "SPIKEY": "single-minute fat-finger wicks",
            "PENNY": "sub-dollar prices, high volatility",
            "CHOPPY": "high volatility, zero net drift",
        },
        "providers": {
            "alpaca": {
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
            },
        },
        "native_endpoints": [
            {
                "method": "GET",
                "path": "/api/v1/stream",
                "purpose": "SSE ticks; clock=demo is an always-open synthetic "
                "session, clock=real follows the NYSE calendar",
                "example": "/api/v1/stream?symbols=CUCKOO,CRASH",
            },
        ],
        "rate_limit": "60 req/min sustained, burst 120, per address, no key.",
    }


app.include_router(alpaca)
app.include_router(stream_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
