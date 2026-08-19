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

import asyncio
import logging
import sys
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse

import apidocs
import providers
from actions import ACTIONS_PATH, router as actions_router
from apidocs import DESCRIPTION, DISCLAIMER, DOCS_URL, SERVERS, SUMMARY, TAGS
from common import EXAMPLE
from effects import AttemptCounter, EffectError, has, parse_scenario, value_of
from engine import GENERATION
from ratelimit import TokenBucketLimiter, client_ip
from stream import STREAM_PATH, router as stream_router

# The docs endpoints hang off the app, not off the /api-prefixed routes, so
# they must be prefixed by hand or the ALB routes them to the frontend.
#
# Everything documentary here comes from apidocs.py: the OpenAPI document is
# where people and SDK generators actually read this API, so it is written
# deliberately rather than left to whatever FastAPI can infer.
app = FastAPI(
    title="CuckooTrade",
    summary=SUMMARY,
    description=DESCRIPTION,
    version=f"1.0 (generation {GENERATION})",
    openapi_tags=TAGS,
    servers=SERVERS,
    contact={"name": "CuckooTrade", "url": DOCS_URL},
    license_info={"name": "MIT", "identifier": "MIT"},
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
attempts = AttemptCounter()

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


def _native_fault(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status * 100000 + 10000,
                                                     "message": message})


async def _truncated(response):
    """Declare the full Content-Length, then send half of it and hang up.

    The client sees a body that ends mid-JSON with the length header insisting
    there was more, which is what a connection dying in flight looks like from
    the parser's side -- and the case naive code never has a branch for.
    """
    body = b"".join([chunk async for chunk in response.body_iterator])
    half = body[: max(1, len(body) // 2)]

    async def cut():
        yield half

    # Headers are copied wholesale, Content-Length included: the mismatch is
    # the fault.
    return StreamingResponse(cut(), status_code=response.status_code,
                             headers=dict(response.headers))


async def _serve(request: Request, call_next, path: str):
    """Normal serving, plus scenario= transport faults where asked for.

    The stream parses scenario= itself: its faults act on the event generator,
    which is reachable only from inside stream.py.
    """
    raw = request.query_params.get("scenario")
    if not raw or path == STREAM_PATH:
        return await call_next(request)

    render = providers.fault_renderer(path) or _native_fault
    try:
        effects = parse_scenario(raw, "http")
    except EffectError as exc:
        return render(400, str(exc))

    delay = value_of(effects, "slow")
    if delay:
        await asyncio.sleep(delay / 1000.0)

    status = value_of(effects, "status")
    flap = value_of(effects, "flap")
    if flap is not None:
        # Keyed on the whole query, not just the path: two endpoints under test
        # at once must not eat each other's attempts, and a retry -- which
        # resends the request byte for byte -- still lands on the same budget.
        if attempts.bump(f"{client_ip(request)}|{path}?{request.url.query}") <= flap:
            status = status or 503
        else:
            status = None

    if status is not None:
        response = render(status, f"injected by scenario={raw} -- this failure was "
                                  f"requested, the data behind it is fine")
    else:
        response = await call_next(request)
        if has(effects, "truncate"):
            response = await _truncated(response)

    # A faulted response is a lie about a moment, never about the history.
    # Nothing may cache it, least of all the immutable rule in common.py.
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Cuckoo-Scenario"] = raw
    return response


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
                response = await _serve(request, call_next, path)
            response.headers["RateLimit-Limit"] = str(int(limiter.capacity))
            response.headers["RateLimit-Remaining"] = str(max(0, remaining))
            response.headers["RateLimit-Reset"] = str(reset)
        else:
            response = await _serve(request, call_next, path)
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


@app.get(
    "/api/health",
    tags=["meta"],
    summary="Liveness and readiness probe",
    operation_id="health",
    response_description="The service is up.",
    responses={
        200: apidocs.response(
            "Always `{\"status\": \"ok\"}` when the process is serving.",
            schema=apidocs.ref("HealthResponse"),
            examples={"ok": apidocs.example("Healthy", {"status": "ok"})},
        )
    },
    openapi_extra=apidocs.extras(
        samples=(("Shell", "curl", 'curl -i "https://cuckootrade.com/api/health"'),),
    ),
)
def health():
    """What kubelet and the ALB call, exempt from rate limiting so a probe can
    never starve behind someone else's scan, and the one path left out of the
    access log -- probing every few seconds per pod, it would otherwise bury
    the lines describing real callers."""
    return {"status": "ok"}


@app.get(
    "/api",
    tags=["meta"],
    summary="Machine-readable service index",
    operation_id="index",
    response_description="Every provider, endpoint, ticker and example URL in one document.",
    responses={
        200: apidocs.response(
            "The whole surface in one fetch: provider base URLs and SDK hints, the "
            "scenario ticker table, the `scenario=` fault grammar, the restatement "
            "contract, and a working example URL for every endpoint.",
            schema=apidocs.ref("IndexResponse"),
            examples={
                "index": apidocs.example(
                    "The index (abridged)",
                    {
                        "name": "CuckooTrade",
                        "synthetic": True,
                        "api_version": 1,
                        "generation": GENERATION,
                        "tagline": "Deterministic synthetic market data. No key, no signup.",
                        "disclaimer": DISCLAIMER,
                        "path_scheme": "/api/v1/{provider}/<provider's own path> ...",
                        "docs": {
                            "site": DOCS_URL,
                            "openapi": "/api/openapi.json",
                            "swagger": "/api/docs",
                            "llms": "https://cuckootrade.com/llms.txt",
                        },
                        "magic_tickers": {
                            "CRASH": "sharp ~25% crash mid-month, slow recovery",
                            "...": "nine more, plus the three restating tickers",
                        },
                        "providers": {
                            "alpaca": {
                                "status": "available",
                                "base_url": "https://cuckootrade.com/api/v1/alpaca",
                                "compatible_with": "https://data.alpaca.markets",
                                "sdk_hint": "StockHistoricalDataClient(url_override=...)",
                                "endpoints": ["..."],
                            },
                            "...": "alphavantage, polygon",
                        },
                        "rate_limit": "60 req/min sustained, burst 120, per address, no key.",
                    },
                    "Abridged -- the live response also carries the full ticker "
                    "tables, the `restatement` and `fault_injection` sections, and "
                    "every provider's endpoint list. Fetch it to see all of it.",
                )
            },
        )
    },
    openapi_extra=apidocs.extras(
        samples=(
            ("Shell", "curl", 'curl "https://cuckootrade.com/api" | jq .'),
            (
                "Python",
                "requests",
                'import requests\n'
                'index = requests.get("https://cuckootrade.com/api").json()\n'
                'print(index["magic_tickers"])              # the scripted symbols\n'
                'print(index["providers"]["alpaca"]["sdk_hint"])',
            ),
        ),
    ),
)
def index():
    """The orientation document, sized for an agent rather than a person.

    Everything needed to make a correct first call, with no other fetch: the
    provider base URLs and how to point each SDK at them, the ticker tables,
    the two determinism axes, and a working example URL for every endpoint.

    This and `/api/openapi.json` say the same things at different resolutions.
    The index is a compact briefing; the OpenAPI document is the reference,
    with per-field descriptions and worked examples.
    """
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
        "restatement": {
            "param": "as_of",
            "purpose": "answer as the feed would have on that date (RFC-3339). "
            "Real feeds restate -- a split or a late dividend rewrites bars you "
            "already stored -- so `as_of` is the second axis of the determinism "
            "guarantee: pin it and the bytes never change, omit it and restating "
            "symbols answer as of today.",
            "tickers": {
                "SPLITS": "2:1 forward split monthly; prior closes halve when it goes ex",
                "DIVVY": "monthly dividend whose ~1.5% adjustment lands five "
                "sessions LATE, after a naive job stopped looking",
                "REVISED": "a bad print that sits in history until the exchange "
                "busts the trade, then quietly disappears",
            },
            "adjustment": "raw | split | dividend | all (default all -- unlike "
            "Alpaca's raw, and observable only on the tickers above)",
            "ledger": ACTIONS_PATH,
            "how_to_tell_it_worked": (
                "Every bar response carries X-Cuckoo-As-Of and X-Cuckoo-Restated "
                "(e.g. '2 actions applied (SPLITS split ex 2026-07-10; ...)'). "
                "'0 actions applied' means nothing rewrote these bars -- usually "
                "the window sits after every ex-date, since an action only "
                "rewrites bars dated before it."
            ),
            "example": "/api/v1/alpaca/v2/stocks/bars?symbols=SPLITS"
            "&timeframe=1Day&start=2026-06-01&end=2026-06-30&as_of=2026-07-09",
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
            "SPLITS": "2:1 forward split monthly; prior closes restate -- see "
            "`restatement` below",
            "DIVVY": "monthly dividend whose ~1.5% adjustment lands five sessions "
            "late -- see `restatement` below",
            "REVISED": "a bad print history carries until the exchange busts it -- "
            "see `restatement` below",
        },
        "fault_injection": {
            "param": "scenario",
            "purpose": "opt-in, deterministic transport faults -- nothing fires "
            "unless you ask for it, and the same spec fails the same way every time",
            "bars": {
                "flap:N": "fail N times, then succeed (tests that retry logic recovers)",
                "status:CODE": "return CODE in the called provider's error shape",
                "slow:MS": "delay the response",
                "truncate": "full Content-Length, half a body",
            },
            "stream": {
                "drop:S": "close the socket at S seconds, mid-frame, no close event",
                "garbage:N": "N unparseable frames among the good ones",
                "silent:S": "no data and no heartbeats for S seconds",
                "slow:MS": "delay between frames",
                "truncate": "one frame cut mid-JSON, connection stays up",
            },
            "example": "/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&scenario=flap:2",
            "caveat": "flap counts attempts per pod; across replicas a flap:N can "
            "burn up to N*replicas failures. Run the container locally for exact counts.",
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
            {
                "method": "GET",
                "path": ACTIONS_PATH,
                "purpose": "splits, dividends and busted trades, with the "
                "announce/ex/process dates a reconciliation job needs",
                "example": f"{ACTIONS_PATH}?symbols=SPLITS,DIVVY",
            },
        ],
        "rate_limit": "60 req/min sustained, burst 120, per address, no key.",
    }


for provider_router in providers.ROUTERS:
    app.include_router(provider_router)
app.include_router(stream_router)
app.include_router(actions_router)


def _openapi():
    """The generated document, plus what generation cannot see.

    `scenario=`, the X-Cuckoo-* headers and the 429 are produced by
    cuckoo_middleware above, so they belong to every operation and are declared
    by no route. apidocs.finalize injects them once, which also means a
    provider added to providers/ later is documented correctly for free.
    """
    if not app.openapi_schema:
        app.openapi_schema = apidocs.finalize(get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=app.servers,
            contact=app.contact,
            license_info=app.license_info,
        ))
    return app.openapi_schema


app.openapi = _openapi


if __name__ == "__main__":
    import uvicorn

    # access_log=False: cuckoo_middleware emits the access line instead, with the
    # real client address and user agent rather than the ALB's ENI.
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
