"""Server-Sent Events stream of simulated ticks (Cuckoo-native, V1_SPEC 3.2).

Two clocks:
- demo (default): an always-open synthetic session. Prices are a pure
  function of the wall clock (engine.demo_price), so every replica and every
  viewer sees the same tick at the same instant with no shared state.
- real: follows the NYSE calendar; emits each symbol's latest completed
  1-minute bar at minute boundaries, and only heartbeats while closed.

Heartbeat comments flow every HEARTBEAT_SECONDS regardless of data: the ALB
kills idle connections (see the idle-timeout annotation in k8s/ingress.yaml),
and a HALTS session or a closed market is silent by design.

STALE is the opposite and the heartbeats matter more there, not less: its
ticks keep arriving on schedule carrying a timestamp that has stopped
advancing. Everything about the connection looks healthy, which is the whole
point -- most clients check that a response arrived, not that it is current.

Alpaca's wire protocol is WebSocket; this is deliberately not that (V2). SSE
is curl-able, which makes it its own documentation.
"""

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from effects import EffectError, has, parse_scenario, value_of
from engine import (GENERATION, bars_range, demo_clock, demo_price, is_trading_day,
                    parse_timeframe)
from engine.market_calendar import session_close_utc, session_open_utc

HEARTBEAT_SECONDS = 15
MAX_STREAM_SYMBOLS = 10
MAX_STREAMS_PER_IP = 5
MAX_STREAM_SECONDS = 15 * 60  # bound leaks; clients reconnect
STREAM_PATH = "/api/v1/stream"

router = APIRouter()
_active: Counter[str] = Counter()


def _event(name: str, payload) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _market_open(now: datetime) -> bool:
    d = now.date()
    return is_trading_day(d) and session_open_utc(d) <= now < session_close_utc(d)


async def _demo_events(symbols: list[str], seed: str, request: Request):
    started = time.monotonic()
    last_heartbeat = started
    yield _event("hello", {"clock": "demo", "symbols": symbols, "synthetic": True,
                           "generation": GENERATION})
    while time.monotonic() - started < MAX_STREAM_SECONDS:
        if await request.is_disconnected():
            return
        second = int(time.time())
        for symbol in symbols:
            # Per-symbol clock: STALE reports an instant that stops advancing
            # while ticks, heartbeats and the socket itself all stay healthy.
            at = int(demo_clock(symbol, second))
            stamp = datetime.fromtimestamp(at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            yield _event("tick", {"S": symbol, "p": demo_price(symbol, at, seed=seed),
                                  "t": stamp})
        if time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
            yield ": hb\n\n"
            last_heartbeat = time.monotonic()
        await asyncio.sleep(max(0.0, (second + 1) - time.time()))


async def _real_events(symbols: list[str], seed: str, request: Request):
    started = time.monotonic()
    timeframe = parse_timeframe("1Min")
    last_emitted: dict[str, str] = {}
    yield _event("hello", {"clock": "real", "symbols": symbols, "synthetic": True,
                           "generation": GENERATION})
    while time.monotonic() - started < MAX_STREAM_SECONDS:
        if await request.is_disconnected():
            return
        now = datetime.now(timezone.utc)
        if _market_open(now):
            for symbol in symbols:
                day_open = session_open_utc(now.date())
                bars, _ = bars_range(symbol, timeframe, day_open, now, seed=seed,
                                     max_bars=1, descending=True, now=now)
                if bars and bars[0]["t"] != last_emitted.get(symbol):
                    last_emitted[symbol] = bars[0]["t"]
                    yield _event("bar", {"S": symbol, **bars[0]})
        yield ": hb\n\n"
        await asyncio.sleep(HEARTBEAT_SECONDS if not _market_open(now) else 5)


_BAD_FRAME = 'event: tick\ndata: {"S":"CUCKOO","p":\n\n'


async def _with_faults(events, effects):
    """Wrap the event generator with the transport faults from scenario=.

    Nothing here is random: a fault fires at the elapsed second or the frame
    index it was asked for, so a test that passes once passes every time.
    """
    started = time.monotonic()
    drop_at = value_of(effects, "drop")
    silent_for = value_of(effects, "silent")
    slow_ms = value_of(effects, "slow")
    garbage_left = value_of(effects, "garbage") or 0
    truncate_left = 1 if has(effects, "truncate") else 0
    frames = 0

    async for chunk in events:
        elapsed = time.monotonic() - started
        if drop_at is not None and elapsed >= drop_at:
            # Half a frame and then silence, with no close event and no error:
            # what a socket dying under you actually looks like.
            yield chunk[: max(1, len(chunk) // 2)]
            return
        if silent_for is not None and elapsed < silent_for:
            continue  # data *and* heartbeats withheld -- this one finds read timeouts
        if slow_ms:
            await asyncio.sleep(slow_ms / 1000.0)
        frames += 1
        if truncate_left and frames > 1 and chunk.startswith("event:"):
            # Cut mid-JSON but keep the connection: the client has to resync
            # rather than reconnect, which is the harder path to get right.
            truncate_left -= 1
            yield chunk[: len(chunk) // 2]
            continue
        if garbage_left and frames % 3 == 0:
            garbage_left -= 1
            yield _BAD_FRAME
        yield chunk


@router.get(STREAM_PATH)
async def sse_stream(request: Request, symbols: str = "CUCKOO", clock: str = "demo",
                     seed: str = "", scenario: str = ""):
    from common import api_error, parse_symbols

    symbol_list = parse_symbols(
        symbols, MAX_STREAM_SYMBOLS, example="/api/v1/stream?symbols=CUCKOO,CRASH"
    )
    if clock not in ("demo", "real"):
        api_error(400, 40010001, f"invalid clock {clock!r}: use clock=demo "
                                 f"(always-open synthetic session, the default) or "
                                 f"clock=real (follows the NYSE calendar)")
    try:
        effects = parse_scenario(scenario, "stream") if scenario else ()
    except EffectError as exc:
        api_error(400, 40010001, str(exc))
    ip = request.headers.get("x-forwarded-for", "").split(",")[-1].strip() or (
        request.client.host if request.client else "unknown"
    )
    if _active[ip] >= MAX_STREAMS_PER_IP:
        api_error(429, 42910000, f"too many concurrent streams from this address "
                                 f"(max {MAX_STREAMS_PER_IP})")

    events = _demo_events if clock == "demo" else _real_events

    async def guarded():
        _active[ip] += 1
        try:
            source = events(symbol_list, seed, request)
            async for chunk in (_with_faults(source, effects) if effects else source):
                yield chunk
        finally:
            _active[ip] -= 1
            if _active[ip] <= 0:
                del _active[ip]

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    if effects:
        headers["X-Cuckoo-Scenario"] = scenario
    return StreamingResponse(guarded(), media_type="text/event-stream", headers=headers)
