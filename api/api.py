import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# The docs endpoints hang off the app, not off the router below, so the router's
# /api prefix does not move them. Left at their defaults they sit at /docs and
# /openapi.json, which the ALB routes to the frontend -- so they have to be
# prefixed by hand. openapi_url matters as much as docs_url: the Swagger page
# fetches the schema from it, and pointing it at the frontend renders an empty
# "Failed to load API definition" page.
app = FastAPI(
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    # Unused today (no OAuth2 on this API) but it defaults to /docs/oauth2-redirect,
    # which would land on the frontend the moment auth is ever added.
    swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Mounted under /api because the ALB ingress forwards the path unmodified
# (unlike the old nginx ingress, ALB has no rewrite-target equivalent).
router = APIRouter(prefix="/api")


@router.get("/hello")
def hello():
    return "hello"


@router.get("/world")
def world():
    return "world"


@router.get("/random")
def random():
    import random
    return random.randint(1, 100)


@router.get("/bigrandom")
def bigrandom():
    import random
    return random.randint(1, 1000)


@router.get("/randomlist")
def randomlist(seed: int | None = None):
    import random
    rng = random.Random(seed)
    return [rng.randint(1, 100) for _ in range(10)]


# Mocks Alpaca's GET /v2/stocks/bars (https://data.alpaca.markets/v2/stocks/bars).
# Same path/query shape as the real endpoint, so a consumer can switch between
# this and Alpaca by swapping only the base URL.
_TIMEFRAME_RE = re.compile(r"^(\d+)(Min|Hour|Day|Week|Month)$", re.IGNORECASE)
_TIMEFRAME_UNIT_DELTAS = {
    "min": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
}
BARS_DEFAULT_LIMIT = 1000
BARS_MAX_LIMIT = 10_000
BARS_DEFAULT_LOOKBACK = timedelta(days=30)


def _parse_timeframe(timeframe: str) -> timedelta:
    match = _TIMEFRAME_RE.match(timeframe.strip())
    if not match or int(match.group(1)) <= 0:
        raise HTTPException(status_code=422, detail=f"invalid timeframe: {timeframe!r}")
    amount, unit = match.groups()
    return int(amount) * _TIMEFRAME_UNIT_DELTAS[unit.lower()]


def _parse_start(start: str) -> datetime:
    text = start.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid start: {start!r}")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _generate_bars(rng, start_ts, step, limit, now):
    bars = []
    price = rng.uniform(100, 200)
    prev_close = price
    timestamp = start_ts

    while timestamp <= now and len(bars) < limit:
        if bars:
            change = rng.uniform(-0.03, 0.03)
            price = prev_close * (1 + change)
        open_price = prev_close
        close_price = price
        high_price = max(open_price, close_price) * 1.005
        low_price = min(open_price, close_price) * 0.995

        bars.append({
            "c": round(close_price, 4),
            "h": round(high_price, 4),
            "l": round(low_price, 4),
            "n": rng.randint(1_000, 500_000),
            "o": round(open_price, 4),
            "t": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "v": rng.randint(1_000_000, 50_000_000),
            "vw": round((open_price + high_price + low_price + close_price) / 4, 6),
        })

        prev_close = close_price
        timestamp += step

    return bars


@router.get("/v2/stocks/bars")
def stock_bars(
    symbols: str,
    timeframe: str = "1Day",
    start: str | None = None,
    limit: int | None = None,
    seed: int | None = None,
):
    import random

    step = _parse_timeframe(timeframe)
    now = datetime.now(timezone.utc)
    start_ts = _parse_start(start) if start else now - BARS_DEFAULT_LOOKBACK
    capped_limit = BARS_DEFAULT_LIMIT if limit is None else max(0, min(limit, BARS_MAX_LIMIT))

    bars = {}
    for symbol in [s.strip() for s in symbols.split(",") if s.strip()]:
        # Seeding per-symbol (rather than sharing one Random across the whole
        # request) keeps a symbol's series the same regardless of what other
        # symbols are requested alongside it or in what order.
        symbol_seed = f"{seed}:{symbol}" if seed is not None else None
        rng = random.Random(symbol_seed)
        bars[symbol] = _generate_bars(rng, start_ts, step, capped_limit, now)

    return {"bars": bars, "next_page_token": None}


@router.get("/somethingspecial")
def somethingspecial():
    return "you are special"


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
