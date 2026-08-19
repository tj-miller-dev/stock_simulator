"""Shared request plumbing for every API surface.

Provider modules (providers/*) and the stream import from here, never from
api.py -- api.py imports them, and this module is what breaks the cycle.
Anything provider-specific (wire shapes, error styles, param grammars) stays
in the provider module; this file holds only the pieces that are genuinely
the same everywhere: symbol/time parsing, the pagination walk, and the
immutability cache header.
"""

import base64
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from engine import (GENERATION, RESTATING_TICKERS, bars_range, parse_adjustment,
                    parse_timeframe)

PUBLIC_HOST = "https://cuckootrade.com"

BARS_DEFAULT_LIMIT = 1000
BARS_MAX_LIMIT = 10_000
BARS_DEFAULT_LOOKBACK = timedelta(days=30)
BARS_MAX_SYMBOLS = 50
SYMBOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,11}$")

# The canonical working example, quoted by generic error messages.
EXAMPLE = "/api/v1/alpaca/v2/stocks/bars?symbols=AAPL,CRASH&timeframe=1Day&start=2026-07-01"


def api_error(status: int, code: int, message: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def parse_symbols(raw: str, cap: int = BARS_MAX_SYMBOLS, example: str = EXAMPLE) -> list[str]:
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not symbols:
        api_error(400, 40010001, f"symbols is required -- working example: {example}")
    if len(symbols) > cap:
        api_error(400, 40010001, f"too many symbols: {len(symbols)} (max {cap})")
    for s in symbols:
        if not SYMBOL_RE.match(s):
            api_error(
                400,
                40010001,
                f"invalid symbol {s!r}: letters, digits, dot and dash, max 12 chars. "
                f"Any well-formed symbol works -- unknown ones get a stable "
                f"hash-derived personality. Working example: {example}",
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


def parse_generation(generation: int) -> None:
    if generation != GENERATION:
        api_error(
            400,
            40010001,
            f"unknown generation {generation}: this deployment serves generation "
            f"{GENERATION}. Omit the parameter for the current generation.",
        )


def parse_common(timeframe, start, end, limit, seed, generation):
    try:
        tf = parse_timeframe(timeframe)
    except ValueError as e:
        api_error(400, 40010001, f"{e} -- working example: {EXAMPLE}")
    parse_generation(generation)
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


def parse_history(as_of: str | None, adjustment: str | None):
    """The two restatement knobs (V1_1_SPEC section 3), shared by every surface.

    `as_of` is CuckooTrade's own and is deliberately spelled with the
    underscore: Alpaca's `asof` is a symbol-mapping date and means something
    else entirely, so conflating them would break the mimicry.
    """
    as_of_dt = parse_time(as_of, "as_of") if as_of else None
    try:
        mode = parse_adjustment(adjustment)
    except ValueError as exc:
        api_error(400, 40010001, f"{exc} -- working example: {EXAMPLE}")
    return as_of_dt, mode


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


def paginate_bars(symbols, tf, start_dt, end_dt, limit, seed, page_token, descending,
                  as_of=None, adjustment="all"):
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
            as_of=as_of, adjustment=adjustment,
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


def maybe_cache_forever(response, end_specified: bool, end_dt, *, symbols=(),
                        as_of=None) -> None:
    """A fully-specified window that ended in the past is immutable *for a
    fixed as_of*, so say so and let any cache keep it forever.

    The restatement tickers are the exception that made this precise. Without
    an explicit as_of, their past is exactly the thing that changes -- pinning
    it in a CDN for a year would serve pre-split prices long after the split
    (V1_1_SPEC section 3.4).
    """
    if not (end_specified and end_dt < datetime.now(timezone.utc) - timedelta(days=1)):
        return
    if as_of is None and any(s in RESTATING_TICKERS for s in symbols):
        return
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
