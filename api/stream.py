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

from typing import Annotated

import apidocs
from fastapi import APIRouter, Query, Request
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


StreamSymbolsQ = Annotated[
    str,
    Query(
        description=(
            f"Comma-separated symbols, up to {MAX_STREAM_SYMBOLS} -- a tighter cap "
            "than the bar endpoints, because every symbol multiplies the frame rate. "
            "Each one gets its own `tick` (or `bar`) event per interval."
        ),
        openapi_examples={
            "default": {"summary": "The default", "value": "CUCKOO"},
            "several": {"summary": "Several at once", "value": "CUCKOO,CRASH,MOON"},
            "stale": {
                "summary": "The one that looks healthy and is not",
                "description": "STALE keeps ticking on schedule with a timestamp that "
                "has stopped advancing. Watch `t`, not the arrival of bytes.",
                "value": "STALE",
            },
        },
    ),
]

StreamClockQ = Annotated[
    str,
    Query(
        description=(
            "`demo` (the default) is an always-open synthetic session -- alive at "
            "11pm on a Sunday, which is what makes it usable for demos and CI. "
            "`real` follows the NYSE calendar and sends nothing but heartbeats while "
            "the market is closed."
        ),
        json_schema_extra={"enum": ["demo", "real"]},
    ),
]

StreamScenarioQ = Annotated[
    str,
    Query(
        description=(
            "Stream-specific transport faults, comma-separated: `drop:S` closes the "
            "socket at S seconds mid-frame with no close event; `garbage:N` mixes in "
            "N unparseable frames; `silent:S` withholds data *and* heartbeats for S "
            "seconds; `slow:MS` delays each frame; `truncate` cuts one frame mid-JSON "
            "but leaves the connection up. The bar-endpoint effects (`flap`, "
            "`status`) do not apply here and are rejected with a message saying so."
        ),
        openapi_examples={
            "none": {"summary": "No faults (default)", "value": ""},
            "drop": {
                "summary": "Kill the socket at 20 seconds",
                "description": "No close event, no error -- what a dying socket really looks like.",
                "value": "drop:20s",
            },
            "silent": {
                "summary": "Go quiet for 30 seconds",
                "description": "Heartbeats withheld too. Finds missing read timeouts.",
                "value": "silent:30s",
            },
            "garbage": {"summary": "Three unparseable frames", "value": "garbage:3"},
        },
    ),
]

_SSE_SAMPLE = """event: hello
data: {"clock":"demo","symbols":["CUCKOO"],"synthetic":true,"generation":1}

event: tick
data: {"S":"CUCKOO","p":545.41,"t":"2026-08-19T19:06:00Z"}

event: tick
data: {"S":"CUCKOO","p":545.38,"t":"2026-08-19T19:06:01Z"}

: hb

"""


@router.get(
    STREAM_PATH,
    tags=["cuckoo-native"],
    summary="Live tick stream (Server-Sent Events)",
    operation_id="stream_ticks",
    response_description="An endless `text/event-stream` of hello, tick/bar and heartbeat frames.",
    responses={
        200: apidocs.response(
            "An SSE stream. One `hello` event, then `tick` events (demo clock) or "
            "`bar` events (real clock), with `: hb` comment heartbeats throughout.",
            schema={"type": "string", "format": "binary"},
            examples={
                "demo": apidocs.example(
                    "A demo-clock stream",
                    _SSE_SAMPLE,
                    "`hello` states the clock and generation. Each `tick` carries "
                    "`S` (symbol), `p` (price) and `t` (the instant it claims to be). "
                    "`: hb` is an SSE comment, not an event -- most clients drop it "
                    "silently, which is the point: it only exists to keep the "
                    "connection from idling out.",
                )
            },
            headers={
                "Cache-Control": apidocs.header("Always `no-cache`.", "no-cache"),
                "X-Accel-Buffering": apidocs.header(
                    "`no`, so no proxy buffers the stream into uselessness.", "no"
                ),
                "X-Cuckoo-Scenario": apidocs.header(
                    "Echoed back when faults were requested.", "drop:20s"
                ),
            },
        ),
        400: apidocs.response(
            "An invalid `clock`, symbol list, or scenario spec.",
            schema=apidocs.ref("AlpacaError"),
            examples={
                "clock": apidocs.example(
                    "Unknown clock",
                    {"code": 40010001, "message":
                     "invalid clock 'nyse': use clock=demo (always-open synthetic "
                     "session, the default) or clock=real (follows the NYSE calendar)"},
                ),
                "scenario": apidocs.example(
                    "A bar-only effect on the stream",
                    {"code": 40010001, "message":
                     "scenario=flap only applies to the bar endpoints; here you can "
                     "use drop, garbage, silent, slow, truncate"},
                ),
            },
        ),
        429: apidocs.response(
            f"More than {MAX_STREAMS_PER_IP} concurrent streams from this address.",
            schema=apidocs.ref("AlpacaError"),
            examples={
                "concurrent": apidocs.example(
                    "Too many open streams",
                    {"code": 42910000, "message":
                     f"too many concurrent streams from this address "
                     f"(max {MAX_STREAMS_PER_IP})"},
                    "A separate limit from the per-minute request budget: this one "
                    "counts sockets held open, not requests made.",
                )
            },
        ),
    },
    openapi_extra=apidocs.extras(
        samples=(
            (
                "Shell",
                "curl",
                "# SSE is curl-able, which makes it its own documentation\n"
                'curl -N "https://cuckootrade.com/api/v1/stream?symbols=CUCKOO,CRASH"',
            ),
            (
                "JavaScript",
                "EventSource",
                'const es = new EventSource(\n'
                '  "https://cuckootrade.com/api/v1/stream?symbols=CUCKOO,CRASH"\n'
                ');\n'
                'es.addEventListener("hello", (e) => console.log(JSON.parse(e.data)));\n'
                'es.addEventListener("tick", (e) => {\n'
                '  const { S, p, t } = JSON.parse(e.data);\n'
                '  console.log(S, p, t);   // compare t to now: STALE stops advancing\n'
                '});\n'
                '// Reconnect on error: the stream closes itself after 15 minutes.\n'
                'es.onerror = () => console.warn("stream dropped");',
            ),
            (
                "Shell",
                "curl (fault injection)",
                "# The socket dies at twenty seconds, mid-frame, every time\n"
                'curl -N "https://cuckootrade.com/api/v1/stream?symbols=CUCKOO'
                '&scenario=drop:20s"',
            ),
        ),
    ),
)
async def sse_stream(request: Request, symbols: StreamSymbolsQ = "CUCKOO",
                     clock: StreamClockQ = "demo", seed: apidocs.SeedQ = "",
                     scenario: StreamScenarioQ = ""):
    """A Server-Sent Events stream of simulated ticks. Deliberately not a
    WebSocket: SSE is curl-able, which makes it its own documentation.

    **Frame grammar.** One `hello` event on connect, stating the clock, the
    symbols and the generation. Then, per symbol per interval, either a `tick`
    (demo clock) carrying `S`, `p` and `t`, or a `bar` (real clock) carrying `S`
    plus the full OHLCV bar. Interleaved throughout are `: hb` comment
    heartbeats -- SSE comments, not events, which most clients drop silently.

    **The heartbeats are load-bearing.** They flow every ~15 seconds regardless
    of data because the load balancer kills idle connections, and a `HALTS`
    session or a closed market is silent by design. `STALE` is where they matter
    most: its ticks keep arriving on schedule carrying a timestamp that has
    stopped advancing, so the connection looks perfectly healthy while the data
    is dead. Check `t`, not the arrival of bytes.

    **Two clocks.** `demo` (default) is an always-open synthetic session whose
    prices are a pure function of the wall clock -- every replica and every
    viewer sees the same tick at the same instant. `real` follows the NYSE
    calendar and emits each symbol's latest completed minute bar at minute
    boundaries, heartbeating only while the market is closed.

    **Limits.** 10 symbols per stream, 5 concurrent streams per address, and
    every connection closes itself after 15 minutes -- bounded so a leaked
    connection cannot accumulate. Clients are expected to reconnect;
    `EventSource` does it automatically.
    """
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
