"""Polygon.io-compatible surface: /api/v1/polygon/<Polygon's own paths>.

Replicates api.polygon.io's aggregates wire format, verified against live
responses (Aug 2026): the envelope key order (ticker, queryCount,
resultsCount, adjusted, results, status, request_id, count, next_url), bar
keys (v, vw, o, c, h, l, t: Unix ms, n), empty windows omitting `results`
and `count`, prev-close bars carrying an extra "T" field, cursor-style
pagination via next_url, and HTTP-4xx errors shaped
{"status": "ERROR", "request_id", "error"}. apiKey is accepted and ignored.

status is always "OK" (the free tier's "DELAYED" would be a lie here:
deterministic synthetic data is never delayed). request_id is an md5 of the
request itself, not a random id -- identical requests must return identical
bytes.
"""

import base64
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import parse_qsl, urlencode

import apidocs
from fastapi import APIRouter, Path, Query
from fastapi.responses import JSONResponse

from common import PUBLIC_HOST, SYMBOL_RE, maybe_cache_forever
from engine import GENERATION, Timeframe, latest_bar, parse_timeframe, bars_range

router = APIRouter(prefix="/api/v1/polygon", tags=["provider: polygon"])

PG_EXAMPLE = "/api/v1/polygon/v2/aggs/ticker/MSFT/range/1/day/2026-07-01/2026-08-01"
DEFAULT_LIMIT = 5000
MAX_LIMIT = 50_000
_MS_RE = re.compile(r"^\d{10,}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

INDEX_ENTRY = {
    "status": "available",
    "base_url": "https://cuckootrade.com/api/v1/polygon",
    "compatible_with": "https://api.polygon.io",
    "sdk_hint": "any Polygon client pointed at the base_url; apiKey accepted "
    "and ignored",
    "endpoints": [
        {
            "method": "GET",
            "path": "/api/v1/polygon/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}",
            "purpose": "Polygon-compatible aggregate bars (custom windows, "
            "cursor pagination via next_url)",
            "example": PG_EXAMPLE,
        },
        {
            "method": "GET",
            "path": "/api/v1/polygon/v2/aggs/ticker/{ticker}/prev",
            "purpose": "previous session's daily bar",
            "example": "/api/v1/polygon/v2/aggs/ticker/MSFT/prev",
        },
    ],
}

POLYGON_DOCS = ("Polygon's own aggregates reference",
                "https://polygon.io/docs/rest/stocks/aggregates/custom-bars")

# Polygon's parameters are documented here rather than in apidocs.py because
# they are Polygon's grammar, not CuckooTrade's -- its `limit` caps at 50,000
# rather than 10,000, and every one of them is typed `str` so a bad value can be
# rejected in Polygon's error shape instead of FastAPI's.
TickerP = Annotated[
    str,
    Path(
        description=(
            "The ticker. Letters, digits, dot and dash, 12 characters max, "
            "case-insensitive. Any well-formed ticker returns data -- unknown ones "
            "get a stable hash-derived personality rather than a 404."
        ),
        openapi_examples={
            "ordinary": {"summary": "An ordinary ticker", "value": "MSFT"},
            "scenario": {
                "summary": "A scenario ticker",
                "description": "Large overnight gaps most days.",
                "value": "GAPPY",
            },
        },
    ),
]

MultiplierP = Annotated[
    str,
    Path(
        description=(
            "Size of the timespan multiplier -- the `5` in \"5-minute bars\". Valid "
            "ranges depend on `timespan`: 1-59 for minute, 1-23 for hour, 1 only for "
            "day, week and year, 1/2/3/4/6/12 for month, and 1/2/4 for quarter."
        ),
        openapi_examples={
            "one": {"summary": "One unit", "value": "1"},
            "five": {"summary": "Five-minute bars, with timespan=minute", "value": "5"},
        },
    ),
]

TimespanP = Annotated[
    str,
    Path(
        description=(
            "The unit `multiplier` counts. Buckets are calendar-aligned rather than "
            "query-aligned, so the same bar comes back whatever window contains it."
        ),
        json_schema_extra={
            "enum": ["minute", "hour", "day", "week", "month", "quarter", "year"]
        },
        openapi_examples={
            "day": {"summary": "Daily bars", "value": "day"},
            "minute": {"summary": "Minute bars", "value": "minute"},
        },
    ),
]

FromP = Annotated[
    str,
    Path(
        description=(
            "Inclusive start of the window: `YYYY-MM-DD`, or a Unix **millisecond** "
            "timestamp. A plain date is read as 00:00:00 UTC."
        ),
        openapi_examples={
            "date": {"summary": "A date", "value": "2026-07-01"},
            "millis": {
                "summary": "Unix milliseconds",
                "description": "Milliseconds, not seconds -- Polygon's convention.",
                "value": "1782878400000",
            },
        },
    ),
]

ToP = Annotated[
    str,
    Path(
        description=(
            "Inclusive end of the window, same grammar as `from`. A plain date "
            "covers the whole day: it is read as 23:59:59.999 UTC, so the day's own "
            "bar is included."
        ),
        openapi_examples={"date": {"summary": "A date", "value": "2026-08-01"}},
    ),
]

AdjustedQ = Annotated[
    str,
    Query(
        description=(
            "Echoed back in the response envelope and otherwise inert: this surface "
            "does not restate. Use the Alpaca paths with `as_of` for corporate "
            "actions."
        ),
        json_schema_extra={"enum": ["true", "false"]},
    ),
]

PgSortQ = Annotated[
    str,
    Query(
        description=(
            "`asc` (oldest first, the default) or `desc`. `next_url` follows the same "
            "direction."
        ),
        json_schema_extra={"enum": ["asc", "desc"]},
    ),
]

PgLimitQ = Annotated[
    str | None,
    Query(
        description=(
            f"Bars per page, 1 to {MAX_LIMIT:,} (default {DEFAULT_LIMIT:,}). Note "
            "this is Polygon's cap, an order of magnitude above the Alpaca surface's. "
            "When more bars remain, the response carries `next_url`."
        ),
        openapi_examples={
            "small": {
                "summary": "Small enough to force pagination",
                "description": "Ask for 5 bars of a long window and next_url appears.",
                "value": "5",
            },
            "default": {"summary": "The default", "value": str(DEFAULT_LIMIT)},
        },
    ),
]

CursorQ = Annotated[
    str | None,
    Query(
        description=(
            "Pagination cursor. Do not construct one: GET the `next_url` from the "
            "previous response unmodified. The cursor carries `limit`, `sort`, `seed` "
            "and `generation` forward, and those carried values win over anything "
            "re-sent alongside it, so a rewritten URL will not do what it looks like "
            "it does."
        ),
    ),
]

ApiKeyQ = Annotated[
    str | None,
    Query(
        description=(
            "Accepted and ignored -- CuckooTrade is keyless by design. Present so "
            "that a Polygon client configured with a real key does not have to be "
            "reconfigured to point here."
        ),
    ),
]

PgSeedQ = Annotated[str | None, Query(description=apidocs.SEED_TEXT)]
PgGenerationQ = Annotated[str | None, Query(description=apidocs.GENERATION_TEXT)]

# A real capture, trimmed to two bars.
_MSFT_RESULTS = [
    {"v": 16323534, "vw": 546.4654, "o": 542.29, "c": 547.47, "h": 550.34,
     "l": 541.81, "t": 1782878400000, "n": 92101},
    {"v": 34854279, "vw": 545.6103, "o": 548.98, "c": 543.53, "h": 549.06,
     "l": 541.73, "t": 1782964800000, "n": 196653},
]

_PG_ERRORS = {
    "timespan": apidocs.example(
        "Unknown timespan",
        {"status": "ERROR", "request_id": "6b237095b634ae86a6a8b24c13b1f71c",
         "error": "Invalid time span. The only supported resolutions are "
                  "minute|hour|day|week|month|quarter|year"},
        "Polygon's own wording, verbatim -- a client that string-matches on it "
        "keeps working.",
    ),
    "multiplier": apidocs.example(
        "Multiplier out of range",
        {"status": "ERROR", "request_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
         "error": "unsupported multiplier 90 for timespan=minute: this synthetic "
                  f"mimic supports 1-59. Working example: {PG_EXAMPLE}"},
        "Where this mimic is narrower than the real API it says so, and says what "
        "it does support.",
    ),
    "window": apidocs.example(
        "Unparseable window",
        {"status": "ERROR", "request_id": "b7c8d9e0f1a2b3c4d5e6f708192a3b4c",
         "error": "invalid time window: use YYYY-MM-DD or a Unix millisecond "
                  f"timestamp for from/to. Working example: {PG_EXAMPLE}"},
    ),
    "cursor": apidocs.example(
        "Edited cursor",
        {"status": "ERROR", "request_id": "c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e",
         "error": "invalid cursor: pass the next_url from the previous response, "
                  "unmodified."},
    ),
}


def _request_id(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()


def _error(status: int, rid: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"status": "ERROR", "request_id": rid, "error": message},
    )


def fault_response(status: int, message: str) -> JSONResponse:
    """An injected fault, wearing Polygon's error shape."""
    return _error(status, _request_id("fault", status, message), message)


def _timeframe(multiplier: int, timespan: str) -> Timeframe | str:
    """Polygon's (multiplier, timespan) grammar onto the engine's. Returns a
    Timeframe or an error string listing what this mimic supports."""
    supported = {
        "minute": ((1, 59), lambda m: Timeframe(m, "Min")),
        "hour": ((1, 23), lambda m: Timeframe(m, "Hour")),
        "day": ((1, 1), lambda m: Timeframe(1, "Day")),
        "week": ((1, 1), lambda m: Timeframe(1, "Week")),
    }
    if timespan in supported:
        (low, high), build = supported[timespan]
        if low <= multiplier <= high:
            return build(multiplier)
        return (
            f"unsupported multiplier {multiplier} for timespan={timespan}: this "
            f"synthetic mimic supports {low}-{high}. Working example: {PG_EXAMPLE}"
        )
    if timespan == "month":
        if multiplier in (1, 2, 3, 4, 6, 12):
            return Timeframe(multiplier, "Month")
        return (
            f"unsupported multiplier {multiplier} for timespan=month: this "
            f"synthetic mimic supports 1, 2, 3, 4, 6, or 12."
        )
    if timespan == "quarter":
        if multiplier in (1, 2, 4):
            return Timeframe(multiplier * 3, "Month")
        return "unsupported multiplier for timespan=quarter: use 1, 2, or 4."
    if timespan == "year":
        if multiplier == 1:
            return Timeframe(12, "Month")
        return "unsupported multiplier for timespan=year: use 1."
    # Polygon's own message for an unknown timespan, verbatim.
    return "Invalid time span. The only supported resolutions are minute|hour|day|week|month|quarter|year"


def _parse_bound(text: str, is_end: bool) -> datetime | None:
    if _MS_RE.match(text):
        return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
    if _DATE_RE.match(text):
        try:
            day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return day + timedelta(days=1) - timedelta(milliseconds=1) if is_end else day
    return None


def _ms(iso_t: str) -> int:
    return int(datetime.fromisoformat(iso_t.replace("Z", "+00:00")).timestamp() * 1000)


def _result(bar: dict) -> dict:
    return {
        "v": bar["v"],
        "vw": round(bar["vw"], 4),
        "o": bar["o"],
        "c": bar["c"],
        "h": bar["h"],
        "l": bar["l"],
        "t": _ms(bar["t"]),
        "n": bar["n"],
    }


def _envelope(ticker: str, results: list, rid: str) -> dict:
    # Field order and omissions match live polygon: no `results`/`count` keys
    # when the window is empty.
    body = {
        "ticker": ticker,
        "queryCount": len(results),
        "resultsCount": len(results),
        "adjusted": True,
    }
    if results:
        body["results"] = results
    body["status"] = "OK"
    body["request_id"] = rid
    if results:
        body["count"] = len(results)
    return body


def _encode_cursor(params: dict) -> str:
    raw = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> dict | None:
    try:
        pad = "=" * (-len(cursor) % 4)
        return dict(parse_qsl(base64.urlsafe_b64decode((cursor + pad).encode()).decode()))
    except Exception:
        return None


@router.get(
    "/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{frm}/{to}",
    summary="Aggregate bars over a custom window",
    operation_id="polygon_aggregates",
    response_description="Polygon's aggregates envelope, with `next_url` when more remain.",
    responses={
        200: apidocs.response(
            "Aggregate bars. `results` and `count` are **omitted entirely** when "
            "nothing matched, rather than sent as `[]` and `0` -- that is what the "
            "real API does.",
            schema=apidocs.ref("PolygonAggsResponse"),
            examples={
                "ordinary": apidocs.example(
                    "A matched window",
                    {"ticker": "MSFT", "queryCount": 2, "resultsCount": 2,
                     "adjusted": True, "results": _MSFT_RESULTS, "status": "OK",
                     "request_id": "234ee1ae9d085ae58a8d694fac44e7ed", "count": 2},
                    "`request_id` is an md5 of the request, so this exact call "
                    "returns this exact id every time -- stable enough to assert on.",
                ),
                "paginated": apidocs.example(
                    "More bars remain",
                    {"ticker": "MSFT", "queryCount": 2, "resultsCount": 2,
                     "adjusted": True, "results": _MSFT_RESULTS, "status": "OK",
                     "request_id": "234ee1ae9d085ae58a8d694fac44e7ed", "count": 2,
                     "next_url": "https://cuckootrade.com/api/v1/polygon/v2/aggs/"
                                 "ticker/MSFT/range/1/day/1782964800001/2026-08-01"
                                 "?cursor=bGltaXQ9MiZzb3J0PWFzYw"},
                    "GET `next_url` unmodified. It already carries the window edge "
                    "and the cursor state.",
                ),
                "empty": apidocs.example(
                    "Nothing matched",
                    {"ticker": "MSFT", "queryCount": 0, "resultsCount": 0,
                     "adjusted": True, "status": "OK",
                     "request_id": "0e1f2a3b4c5d6e7f8091a2b3c4d5e6f7"},
                    "No `results` key and no `count` key at all. Code that does "
                    "`response[\"results\"]` unguarded breaks here, which is the point.",
                ),
            },
        ),
        400: apidocs.response(
            "A malformed parameter, in Polygon's error shape.",
            schema=apidocs.ref("PolygonError"),
            examples=_PG_ERRORS,
        ),
    },
    openapi_extra=apidocs.extras(
        mimics=POLYGON_DOCS,
        samples=(
            (
                "Shell",
                "curl",
                'curl "https://cuckootrade.com/api/v1/polygon/v2/aggs/ticker/MSFT'
                '/range/1/day/2026-07-01/2026-08-01"',
            ),
            (
                "Python",
                "requests (paging)",
                "import requests\n\n"
                'url = ("https://cuckootrade.com/api/v1/polygon/v2/aggs/ticker/MSFT"\n'
                '       "/range/1/day/2026-01-01/2026-08-01?limit=100")\n'
                "bars = []\n"
                "while url:\n"
                "    page = requests.get(url).json()\n"
                '    bars += page.get("results", [])   # absent when empty\n'
                '    url = page.get("next_url")        # absent on the last page',
            ),
        ),
    ),
)
def aggs(
    ticker: TickerP,
    multiplier: MultiplierP,
    timespan: TimespanP,
    frm: FromP,
    to: ToP,
    adjusted: AdjustedQ = "true",   # echoed; this surface does not restate
    sort: PgSortQ = "asc",
    limit: PgLimitQ = None,         # parsed by hand so errors stay polygon-shaped
    cursor: CursorQ = None,
    apiKey: ApiKeyQ = None,         # accepted, ignored: keyless by design
    seed: PgSeedQ = None,           # Cuckoo extension: alternate dataset
    generation: PgGenerationQ = None,  # Cuckoo extension: pin generator version
):
    """Polygon's custom-window aggregates, `GET /v2/aggs/ticker/{ticker}/range/...`.

    **The window** is inclusive at both ends. A plain `YYYY-MM-DD` in `to`
    covers the whole day (it resolves to 23:59:59.999 UTC), so unlike the Alpaca
    surface there is no midnight boundary to trip over here.

    **Pagination is cursor-style, not token-style.** When more bars remain the
    envelope carries `next_url`: an absolute URL to GET unmodified. It encodes
    the next window edge in its path and `limit`, `sort`, `seed` and
    `generation` in its cursor -- and those carried values override anything you
    re-send, so editing the URL does not do what it looks like it does. Stop
    when `next_url` is absent.

    **Two shapes worth coding defensively against**, both faithful to the real
    API: an empty window omits `results` *and* `count` rather than returning
    empty values, and `status` is always the string `"OK"`, never a boolean.

    **Deviations:** `apiKey` is accepted and ignored; `adjusted` is echoed but
    inert, since restatement lives on the Alpaca surface; and `request_id` is an
    md5 of the request rather than a random id, because identical requests must
    return identical bytes.
    """
    if cursor is not None:
        carried = _decode_cursor(cursor)
        if carried is None:
            return _error(400, _request_id("polygon", "badcursor", cursor),
                          "invalid cursor: pass the next_url from the previous "
                          "response, unmodified.")
        # Continuation state wins; the client is expected to GET next_url as-is.
        sort = carried.get("sort", sort)
        limit = carried.get("limit", limit)
        seed = carried.get("seed", seed)
        generation = carried.get("generation", generation)

    rid = _request_id("polygon-aggs", ticker, multiplier, timespan, frm, to,
                      sort, limit, seed, generation, GENERATION)

    symbol = ticker.strip().upper()
    if not SYMBOL_RE.match(symbol):
        return _error(400, rid, f"invalid ticker {ticker!r}: letters, digits, dot "
                                f"and dash, max 12 chars. Any well-formed ticker "
                                f"works. Working example: {PG_EXAMPLE}")
    if generation is not None and generation != str(GENERATION):
        return _error(400, rid, f"unknown generation {generation}: this deployment "
                                f"serves generation {GENERATION}.")
    if not multiplier.isdigit():
        return _error(400, rid, f"invalid multiplier {multiplier!r}: must be an integer.")
    timeframe = _timeframe(int(multiplier), timespan)
    if isinstance(timeframe, str):
        return _error(400, rid, timeframe)
    if sort not in ("asc", "desc"):
        return _error(400, rid, f"invalid sort {sort!r}: use asc or desc.")
    if limit is None:
        capped = DEFAULT_LIMIT
    elif str(limit).isdigit() and 1 <= int(limit) <= MAX_LIMIT:
        capped = int(limit)
    else:
        return _error(400, rid, f"invalid limit {limit!r}: 1 to {MAX_LIMIT}.")
    from_dt = _parse_bound(frm, is_end=False)
    to_dt = _parse_bound(to, is_end=True)
    if from_dt is None or to_dt is None:
        return _error(400, rid, "invalid time window: use YYYY-MM-DD or a Unix "
                                "millisecond timestamp for from/to. Working "
                                f"example: {PG_EXAMPLE}")
    if from_dt > to_dt:
        return _error(400, rid, "the 'from' bound is after the 'to' bound.")

    bars, _ = bars_range(
        symbol, timeframe, from_dt, to_dt, seed=seed or "",
        max_bars=capped + 1, descending=sort == "desc",
    )
    truncated = len(bars) > capped
    bars = bars[:capped]
    body = _envelope(symbol, [_result(b) for b in bars], rid)

    if truncated:
        edge = _ms(bars[-1]["t"])
        next_frm, next_to = (frm, str(edge - 1)) if sort == "desc" else (str(edge + 1), to)
        token = _encode_cursor({"limit": capped, "sort": sort, "seed": seed,
                                "generation": generation})
        body["next_url"] = (
            f"{PUBLIC_HOST}/api/v1/polygon/v2/aggs/ticker/{symbol}/range/"
            f"{multiplier}/{timespan}/{next_frm}/{next_to}?cursor={token}"
        )

    response = JSONResponse(body)
    maybe_cache_forever(response, True, to_dt)
    return response


@router.get(
    "/v2/aggs/ticker/{ticker}/prev",
    summary="Previous session's daily bar",
    operation_id="polygon_previous_close",
    response_description="A one-bar aggregates envelope, whose bar carries an extra `T`.",
    responses={
        200: apidocs.response(
            "The last completed daily bar, in the same envelope as the aggregates "
            "endpoint.",
            schema=apidocs.ref("PolygonAggsResponse"),
            examples={
                "prev": apidocs.example(
                    "The previous close",
                    {"ticker": "MSFT", "queryCount": 1, "resultsCount": 1,
                     "adjusted": True, "results": [
                         {"T": "MSFT", "v": 71016331, "vw": 446.6834, "o": 450.48,
                          "c": 439.85, "h": 452.75, "l": 439.63,
                          "t": 1787025600000, "n": 400683}],
                     "status": "OK",
                     "request_id": "ba3d18d9252d74914f93fde84042063a", "count": 1},
                    "Note the `T` field, present only on this endpoint -- exactly as "
                    "the real API does it.",
                )
            },
        ),
        400: apidocs.response(
            "A malformed ticker or generation, in Polygon's error shape.",
            schema=apidocs.ref("PolygonError"),
            examples={"ticker": apidocs.example(
                "Malformed ticker",
                {"status": "ERROR", "request_id": "d1e2f3a4b5c6d7e8f90a1b2c3d4e5f60",
                 "error": "invalid ticker 'NOT A TICKER': letters, digits, dot and "
                          "dash, max 12 chars."},
            )},
        ),
    },
    openapi_extra=apidocs.extras(
        mimics=("Polygon's own previous-close reference",
                "https://polygon.io/docs/rest/stocks/aggregates/previous-day-bar"),
        samples=(
            (
                "Shell",
                "curl",
                'curl "https://cuckootrade.com/api/v1/polygon/v2/aggs/ticker/MSFT/prev"',
            ),
        ),
    ),
)
def prev_close(
    ticker: TickerP,
    adjusted: AdjustedQ = "true",
    apiKey: ApiKeyQ = None,
    seed: PgSeedQ = None,
    generation: PgGenerationQ = None,
):
    """The most recent completed daily bar, on the NYSE calendar.

    "Previous" is relative to the calendar, not to the wall clock: during a
    session this is yesterday's bar, and over a weekend or holiday it is
    Friday's. No partial in-progress bar is ever served.

    The bar carries an extra `T` field holding the ticker, which the aggregates
    endpoint's bars do not -- an inconsistency in the real API, reproduced here
    on purpose. `results` is an array even though it always holds exactly one
    bar, again matching the real shape.
    """
    rid = _request_id("polygon-prev", ticker, seed, generation, GENERATION)
    symbol = ticker.strip().upper()
    if not SYMBOL_RE.match(symbol):
        return _error(400, rid, f"invalid ticker {ticker!r}: letters, digits, dot "
                                f"and dash, max 12 chars.")
    if generation is not None and generation != str(GENERATION):
        return _error(400, rid, f"unknown generation {generation}: this deployment "
                                f"serves generation {GENERATION}.")
    bar = latest_bar(symbol, parse_timeframe("1Day"), seed=seed or "")
    results = [{"T": symbol, **_result(bar)}] if bar else []
    return _envelope(symbol, results, rid)
