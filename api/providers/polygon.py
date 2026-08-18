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
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter
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


def _request_id(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()


def _error(status: int, rid: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"status": "ERROR", "request_id": rid, "error": message},
    )


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


@router.get("/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{frm}/{to}")
def aggs(
    ticker: str,
    multiplier: str,
    timespan: str,
    frm: str,
    to: str,
    adjusted: str = "true",         # echoed; synthetic data has no corporate actions
    sort: str = "asc",
    limit: str | None = None,       # parsed by hand so errors stay polygon-shaped
    cursor: str | None = None,
    apiKey: str | None = None,      # accepted, ignored: keyless by design
    seed: str | None = None,        # Cuckoo extension: alternate dataset
    generation: str | None = None,  # Cuckoo extension: pin generator version
):
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


@router.get("/v2/aggs/ticker/{ticker}/prev")
def prev_close(
    ticker: str,
    adjusted: str = "true",
    apiKey: str | None = None,
    seed: str | None = None,
    generation: str | None = None,
):
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
