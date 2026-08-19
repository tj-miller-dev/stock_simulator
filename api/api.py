"""CuckooTrade HTTP API: provider-wire-compatible synthetic market data.

Path scheme: /api/v1/{provider}/<provider's own path>. Everything through the
provider segment is CuckooTrade's namespace (the v1 versions this API's
surface; the `generation` param versions the data). Everything after it
mimics that provider's wire format, so its SDKs work with only a base-URL
override. Providers live in providers/ -- one module per provider, each
exposing `router` and `INDEX_ENTRY` -- and mount alongside each other
without touching existing paths.

Wire-compat responses carry no extra body fields -- strict SDK parsers must
never choke -- so the synthetic marking rides in X-Cuckoo-* headers instead.
Cuckoo-native endpoints (/api, /api/v1/stream) carry full metadata in the
body.

Error messages teach: they state the valid grammar and include a working
example, because errors are read at the exact moment someone (or some agent)
is stuck. Error *shape* follows the provider being mimicked (Alpaca's
{"code","message"}, Alpha Vantage's 200-with-"Error Message", Polygon's
{"status":"ERROR",...}).
"""

import logging
import sys
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import providers
from common import EXAMPLE
from engine import GENERATION
from ratelimit import TokenBucketLimiter, client_ip
from stream import router as stream_router

DOCS_URL = "https://cuckootrade.com/docs"
DISCLAIMER = (
    "All data is synthetic. CuckooTrade exists for exercising code paths -- "
    "development, CI, demos, tutorials -- never for validating trading "
    "strategies: a profitable backtest on synthetic data means nothing."
)

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

# Own handler rather than uvicorn's access logger, which reports the socket peer
# -- behind the ALB that's a load balancer ENI in 10.0.0.0/16, identical for every
# caller, and carries no user agent. `access_log=False` below turns that line off
# so each request produces exactly one, richer, line. propagate=False keeps this
# independent of however uvicorn reconfigures logging at startup.
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
access_log = logging.getLogger("cuckoo.access")
access_log.addHandler(_handler)
access_log.setLevel(logging.INFO)
access_log.propagate = False


def _log_request(request: Request, status: int, started: float) -> None:
    """One line per real request: who called, what they asked for, how it went.

    Health checks are dropped -- kubelet and the ALB between them probe
    /api/health every few seconds per pod, which buried the few percent of lines
    describing actual users. The query string is kept deliberately: it's the only
    place the requested symbols show up.

    This is a live-debugging aid, not the analytics record -- pod stdout dies with
    the pod. The durable copy is the ALB access log (terraform/modules/loadbalancer).
    """
    if request.url.path == "/api/health":
        return
    # Anything a caller controls gets stripped to printable characters before it
    # reaches a log line, so a crafted header can't forge entries.
    raw_ua = request.headers.get("user-agent", "-")
    ua = "".join(c for c in raw_ua if c.isprintable())[:120].replace('"', "'")
    query = f"?{request.url.query}" if request.url.query else ""
    access_log.info(
        'ip=%s status=%d dur=%dms %s %s%s ua="%s"',
        client_ip(request),
        status,
        int((time.perf_counter() - started) * 1000),
        request.method,
        request.url.path,
        query,
        ua,
    )


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
    started = time.perf_counter()
    limited = path.startswith("/api") and path != "/api/health"
    try:
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
    except Exception:
        # Unhandled errors are turned into a 500 by Starlette's outermost
        # middleware, past this point -- log it here or the request that broke
        # the server is the one request missing from the log.
        _log_request(request, 500, started)
        raise
    response.headers["X-Cuckoo-Synthetic"] = "true"
    response.headers["X-Cuckoo-Generation"] = str(GENERATION)
    response.headers["X-Cuckoo-Docs"] = DOCS_URL
    _log_request(request, response.status_code, started)
    return response


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
            "STALE": "feed freezes: price repeats, volume zero, clock keeps moving",
            "SPIKEY": "single-minute fat-finger wicks",
            "PENNY": "sub-dollar prices, high volatility",
            "CHOPPY": "high volatility, zero net drift",
        },
        "providers": providers.INDEX,
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


for provider_router in providers.ROUTERS:
    app.include_router(provider_router)
app.include_router(stream_router)


if __name__ == "__main__":
    import uvicorn

    # access_log=False: cuckoo_middleware emits the access line instead, with the
    # real client address and user agent rather than the ALB's ENI.
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
