"""Everything that makes /api/openapi.json teach as well as the endpoints do.

The OpenAPI document is the source of truth for this API's documentation: the
docs site links to it, SDK generators consume it, and an agent that fetches it
with no other context should be able to make a correct call from it alone.
That bar is higher than "the parameters are listed", so this module holds the
vocabulary that clears it:

- `DESCRIPTION`, `TAGS`, `SERVERS`: the front matter Swagger and ReDoc render
  above any endpoint -- quickstart, path scheme, the determinism contract, the
  headers every response carries.
- `*_Q` annotated query types: each parameter described once and reused by
  every surface that takes it, so `as_of` cannot come to mean one thing on
  Alpaca's bars and another on the ledger.
- `SCHEMAS` plus `response()`: response shapes with per-field descriptions and
  worked examples, including the ones worth seeing -- a restated window, an
  empty window, each provider's own error style.
- `finalize()`: the middleware's contribution. `scenario=`, the `X-Cuckoo-*`
  headers and the 429 are produced in api.py's middleware rather than by any
  route, so no route declares them; they are injected onto every operation
  here instead of copy-pasted onto each one.

Prose lives here rather than inline so the provider modules stay readable as
behavior, and so the description of a shared parameter has exactly one home.
"""

from typing import Annotated

from fastapi import Path, Query

from common import BARS_MAX_LIMIT, BARS_MAX_SYMBOLS, EXAMPLE, PUBLIC_HOST
from engine import GENERATION

DOCS_URL = f"{PUBLIC_HOST}/docs"
DISCLAIMER = (
    "All data is synthetic. CuckooTrade exists for exercising code paths -- "
    "development, CI, demos, tutorials -- never for validating trading "
    "strategies: a profitable backtest on synthetic data means nothing."
)

SUMMARY = (
    "Deterministic synthetic market data, spoken in your broker's wire format. "
    "No key, no signup."
)

# "Try it out" in Swagger UI posts to the first server; localhost second so
# whoever is running the GHCR image can switch with one dropdown.
SERVERS = [
    {"url": PUBLIC_HOST, "description": "Production -- keyless, rate limited per IP"},
    {
        "url": "http://localhost:8000",
        "description": "The container run locally -- exact flap counts, no shared rate limit",
    },
]

EXTERNAL_DOCS = {
    "description": "Guides, recipes and the scenario cookbook",
    "url": DOCS_URL,
}


DESCRIPTION = f"""
{SUMMARY}

Point an SDK's base URL here and it works unmodified. Every bar is generated on
demand from a hash -- there is no database, and no market.

> **{DISCLAIMER}**

---

## Quickstart

```bash
curl "{PUBLIC_HOST}/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&timeframe=1Day&start=2026-07-01"
```

```python
from alpaca.data.historical import StockHistoricalDataClient

# The constructor demands keys; this server ignores them.
client = StockHistoricalDataClient(
    "any", "thing", url_override="{PUBLIC_HOST}/api/v1/alpaca"
)
```

## How the paths are laid out

`/api/v1/{{provider}}/<the provider's own path>`

Everything up to and including the provider segment is CuckooTrade's namespace;
everything after it is that provider's own grammar, faithful enough that their
SDKs, tutorials and copy-pasted snippets work with only a base-URL swap. The
`v1` versions *this API's surface*. The `generation` parameter versions *the
data*. The two move independently.

Paths without a provider segment (`/api/v1/stream`,
`/api/v1/corporate-actions`) are CuckooTrade-native and carry their metadata in
the body. Wire-compatible paths never do: a strict SDK parser must not meet a
field it does not recognise, so their metadata rides in headers instead.

## The determinism contract

Every bar is a pure function of **(symbol, timestamp, timeframe, generation,
seed, as_of)**. Identical requests return identical bytes, forever, from any
replica. Two consequences worth planning around:

- Aggregating finer bars reproduces the coarser bar exactly -- a session's
  one-minute bars sum and bound to that day's `1Day` bar, O/H/L/C included.
- A fully-specified window that ended in the past is immutable, and says so
  with `Cache-Control: public, max-age=31536000, immutable`.

Four parameters steer it, and every data endpoint accepts them:

| Parameter | Default | What it does |
|---|---|---|
| `seed` | unset -- the canonical universe everyone shares | Remixes the whole universe. Same realism, different history. Any string. |
| `generation` | `{GENERATION}` (latest) | Pins the generator version. Old generations stay queryable forever; within one, bytes never change. |
| `as_of` | unset -- answer as of now | Answers as the feed would have on that date. The restatement axis; see below. |
| `scenario` | unset -- nothing fails | Injects transport faults. Opt-in and deterministic; see below. |

## Scenario tickers

Reserved symbols with scripted, calendar-anchored behavior -- Stripe's magic
card numbers, but for market data. Each shows its signature behavior within any
30-day window, so a casual query always finds it. Every other well-formed
symbol works too and gets a stable hash-derived personality; the ~100 most
famous tickers are curated to land in a plausible price range.

| Ticker | What it does | What it tends to break |
|---|---|---|
| `CRASH` | Sharp ~25% multi-day crash monthly, slow recovery | Drawdown maths, stop-loss logic |
| `MOON` | Parabolic pump, then a sharp correction | Y-axis scaling, momentum signals |
| `FLAT` | Constant price, zero-range bars (`o=h=l=c`) | Naive chart scaling, dividing by the range |
| `GAPPY` | Large overnight gaps most days | Assuming close is roughly the next open |
| `HALTS` | Minute bars *absent* during intraday halt windows | Code that assumes a fixed bar count per session |
| `STALE` | Price repeats, `v=0`, the clock keeps advancing | Liveness checks that only test "did bytes arrive" |
| `SPIKEY` | Single-minute fat-finger wicks that instantly revert | Outlier handling, naive high/low |
| `PENNY` | Sub-dollar price, high relative volatility | Float precision, tick-size rounding |
| `CHOPPY` | High volatility, zero net drift | Mean-reversion strategies, overfitting |
| `SPLITS` | 2:1 forward split monthly; prior closes restate | Stored history that is never re-fetched |
| `DIVVY` | Monthly dividend adjusting ~1.5%, landing **five sessions late** | Jobs that stop watching on the ex-date |
| `REVISED` | A bad print history carries until the exchange busts it | Anything trusting a bar because it was there yesterday |

The last three restate. The rest are pure price shapes.

## Restatement: `as_of`

Real feeds rewrite bars you already stored -- a split, a late dividend, a
busted trade. `as_of` is the second axis of the determinism guarantee: pin it
and the bytes never change; omit it and restating symbols answer as of today.

```bash
# The same window, asked before and after the split lands
curl ".../v2/stocks/bars?symbols=SPLITS&timeframe=1Day&start=2026-06-01&end=2026-06-30&as_of=2026-06-09"
curl ".../v2/stocks/bars?symbols=SPLITS&timeframe=1Day&start=2026-06-01&end=2026-06-30&as_of=2026-07-09"
```

`adjustment` selects which action classes get applied: `raw`, `split`,
`dividend`, or `all` -- the default, which differs from Alpaca, whose default
is `raw`. It is observable only on the three restating tickers.

**How to tell it worked.** Every bar response carries `X-Cuckoo-Restated`, e.g.
`1 actions applied (SPLITS split ex 2026-06-10)`. `0 actions applied` is the
informative case: nothing rewrote these bars, usually because the window sits
after every ex-date -- an action only rewrites bars dated *before* it.
`GET /api/v1/corporate-actions` is the ledger behind those headers.

## Fault injection: `scenario`

The other half of a test feed: sockets that die, frames that arrive
half-written, responses that fail twice before they work. Nothing fires unless
you ask for it, and the same spec fails the same way every time -- these are
built to run in CI, where random chaos tools cannot.

| Effect | Surface | What the caller sees | Range |
|---|---|---|---|
| `flap:N` | bars | Fails N times, then succeeds. Proves retry logic recovers. | 1-20 |
| `status:CODE` | bars | That status, in the called provider's own error shape. | 400-599 |
| `slow:MS` | both | Delays the response (bars) or each frame (stream). | 0-10000 |
| `truncate` | both | Full `Content-Length`, half a body. On the stream, one frame cut mid-JSON with the connection left up. | -- |
| `drop:S` | stream | Socket closes at S seconds, mid-frame, with no close event. | 0-900 |
| `garbage:N` | stream | N unparseable frames mixed among the good ones. | 1-50 |
| `silent:S` | stream | No data *and* no heartbeats for S seconds. Finds read timeouts. | 0-900 |

Combine with commas: `scenario=slow:500,flap:2`. Seconds and milliseconds may
carry their unit (`drop:20s`, `slow:500ms`). A faulted response is a lie about
a moment, never about the history, so it always carries
`Cache-Control: no-store` and echoes back in `X-Cuckoo-Scenario`.

**One honest caveat:** `flap` counts attempts per pod, in memory. Across
replicas a `flap:N` can burn up to N x replicas failures before it clears. Run
the container locally when you need an exact count.

## Headers on every response

| Header | Example | Meaning |
|---|---|---|
| `X-Cuckoo-Synthetic` | `true` | Always present, always `true`. If it is missing, you are not talking to CuckooTrade. |
| `X-Cuckoo-Generation` | `{GENERATION}` | Which generator produced this data. |
| `X-Cuckoo-Docs` | `{DOCS_URL}` | Where to read more, for whoever finds this response in a log. |
| `RateLimit-Limit` | `120` | Burst capacity. |
| `RateLimit-Remaining` | `119` | Tokens left in this bucket. |
| `RateLimit-Reset` | `0` | Seconds until the bucket holds enough for one more request. |

Bar endpoints add `X-Cuckoo-As-Of` and `X-Cuckoo-Restated`. Faulted responses
add `X-Cuckoo-Scenario`.

## Limits and errors

Keyless, per-IP: **60 requests/minute sustained, burst 120**. Also
{BARS_MAX_SYMBOLS} symbols per request, {BARS_MAX_LIMIT:,} bars per page, and
on the stream 10 symbols and 5 concurrent connections per address. CORS is wide
open and GET-only on purpose -- browser code can call this directly.

Error *shape* follows the provider being mimicked, because the point is to
exercise your parser: Alpaca's `{{"code", "message"}}`, Polygon's
`{{"status": "ERROR", ...}}`, and Alpha Vantage's HTTP 200 carrying an
`"Error Message"` key. Error *text* always states the valid grammar and
includes a working URL -- errors get read at the moment someone is stuck.
"""


TAGS = [
    {
        "name": "provider: alpaca",
        "description": (
            "Mimics `data.alpaca.markets`: the same paths, params, JSON shapes, "
            "pagination semantics and error style. The acceptance test is the "
            "contract -- `alpaca-py`'s `StockHistoricalDataClient`, pointed here with "
            "`url_override` and any non-empty keys, works unmodified.\n\n"
            f"**Base URL:** `{PUBLIC_HOST}/api/v1/alpaca`\n\n"
            "**Deviations, all deliberate:** `feed`, `currency` and Alpaca's own "
            "`asof` (its symbol-mapping date) are accepted and ignored; `adjustment` "
            "defaults to `all` rather than `raw`; and the restatement knob is spelled "
            "`as_of`, with an underscore, precisely so it cannot be confused with "
            "Alpaca's `asof`."
        ),
        "externalDocs": {
            "description": "Alpaca's own market data reference",
            "url": "https://docs.alpaca.markets/reference/stockbars",
        },
    },
    {
        "name": "provider: polygon",
        "description": (
            "Mimics `api.polygon.io`'s aggregates, verified against live responses: "
            "envelope key order, `next_url` cursor pagination, Unix-millisecond "
            "timestamps, empty windows omitting `results` and `count`, and 4xx errors "
            'shaped `{"status": "ERROR", "request_id", "error"}`.\n\n'
            f"**Base URL:** `{PUBLIC_HOST}/api/v1/polygon`\n\n"
            "**Deviations, all deliberate:** `apiKey` is accepted and ignored; "
            "`status` is always `OK`, never the free tier's `DELAYED`, because "
            "deterministic data is never delayed; and `request_id` is an md5 of the "
            "request rather than a random id, since identical requests must return "
            "identical bytes. `as_of` restatement is not wired into this surface -- "
            "use the Alpaca paths for that."
        ),
        "externalDocs": {
            "description": "Polygon's own aggregates reference",
            "url": "https://polygon.io/docs/rest/stocks/aggregates/custom-bars",
        },
    },
    {
        "name": "provider: alphavantage",
        "description": (
            "Mimics `www.alphavantage.co`'s `query` endpoint, verified against live "
            "responses: stringified OHLCV maps keyed newest-first, numbered "
            "`Meta Data` fields, `GLOBAL_QUOTE`'s zero-padded keys, and -- "
            "faithfully -- errors returned as **HTTP 200** with an `\"Error Message\"` "
            "body, which is how the real API reports them and what client libraries "
            "sniff for.\n\n"
            f"**Base URL:** `{PUBLIC_HOST}/api/v1/alphavantage`\n\n"
            "**Deviations, all deliberate:** `apikey` is accepted and ignored; only "
            "completed bars are served, so there is no partial current "
            "day/week/month row (determinism requires it); sessions are regular hours "
            "only, so `extended_hours` and `adjusted` are accepted and ignored; and "
            "`datatype=csv` is not implemented."
        ),
        "externalDocs": {
            "description": "Alpha Vantage's own documentation",
            "url": "https://www.alphavantage.co/documentation/",
        },
    },
    {
        "name": "cuckoo-native",
        "description": (
            "CuckooTrade's own endpoints, mimicking nobody. These are the ones that "
            "carry full metadata in the body -- there is no SDK parser to keep "
            "happy -- so read the response, not the headers."
        ),
    },
    {
        "name": "meta",
        "description": (
            "Service discovery and probes. Unversioned, and staying that way."
        ),
    },
]


# --------------------------------------------------------------------------
# Query parameters
#
# Described once here, reused by every surface that takes them. `openapi_examples`
# populates Swagger's example dropdown, which is where most people will first
# discover that a parameter has interesting values at all -- so the entries are
# chosen to teach (a restating ticker, a halted session), not to fill the list.
# --------------------------------------------------------------------------

SymbolsQ = Annotated[
    str,
    Query(
        description=(
            f"Comma-separated symbols, up to {BARS_MAX_SYMBOLS}. Case-insensitive; "
            "deduplicated and sorted alphabetically, which is also the order "
            "pagination walks them in. Any well-formed symbol works -- letters, "
            "digits, dot and dash, 12 characters max -- and unknown ones get a "
            "stable hash-derived personality rather than an error. See the scenario "
            "ticker table above for the symbols with scripted behavior."
        ),
        openapi_examples={
            "ordinary": {
                "summary": "An ordinary symbol",
                "description": "Curated to land in a plausible AAPL price range.",
                "value": "AAPL",
            },
            "several": {
                "summary": "Several at once",
                "description": "One request, one bars map keyed by symbol.",
                "value": "AAPL,MSFT,SPY",
            },
            "scenario": {
                "summary": "A scenario ticker",
                "description": "A ~25% crash within any 30-day window.",
                "value": "CRASH",
            },
            "restating": {
                "summary": "A restating ticker",
                "description": "Pair with `as_of` to watch stored history get rewritten.",
                "value": "SPLITS",
            },
        },
    ),
]

SymbolPathQ = Annotated[
    str,
    Path(
        description=(
            "A single symbol, in the path rather than the query string. Same grammar "
            "as `symbols`: letters, digits, dot and dash, 12 characters max, "
            "case-insensitive. Any well-formed symbol works; the scenario tickers "
            "are the ones with scripted behavior."
        ),
        openapi_examples={
            "ordinary": {"summary": "An ordinary symbol", "value": "AAPL"},
            "scenario": {
                "summary": "A scenario ticker",
                "description": "Sub-dollar prices -- the float-precision test.",
                "value": "PENNY",
            },
        },
    ),
]

TimeframeQ = Annotated[
    str,
    Query(
        description=(
            "Alpaca's timeframe grammar: `[N]Min` (1-59), `[N]Hour` (1-23), `1Day`, "
            "`1Week`, or `[N]Month` with N in 1, 2, 3, 4, 6, 12. Buckets are "
            "calendar-aligned, not query-aligned, so the same bar comes back "
            "whatever window you ask for. Intraday bars cover regular hours only "
            "(09:30-16:00 ET); daily bars are stamped midnight ET expressed in UTC, "
            "matching Alpaca."
        ),
        openapi_examples={
            "daily": {"summary": "Daily bars", "value": "1Day"},
            "intraday": {
                "summary": "15-minute bars",
                "description": "Aggregated from minute bars, so they reconcile exactly.",
                "value": "15Min",
            },
            "minute": {
                "summary": "Minute bars",
                "description": "The finest resolution. Where HALTS and SPIKEY show up.",
                "value": "1Min",
            },
            "monthly": {"summary": "Quarterly bars", "value": "3Month"},
        },
    ),
]

StartQ = Annotated[
    str | None,
    Query(
        description=(
            "Inclusive start of the window: RFC-3339, or `YYYY-MM-DD` (read as "
            "midnight UTC). A naive timestamp is treated as UTC. Defaults to 30 days "
            "before `end`.\n\n"
            "Worth knowing: `YYYY-MM-DD` is midnight **UTC** while daily bars are "
            "stamped midnight **ET** (04:00Z or 05:00Z), so `end=2026-07-03` excludes "
            "the July 3rd bar. Pass `end=2026-07-03T23:59:59Z` to include it."
        ),
        openapi_examples={
            "date": {"summary": "A plain date", "value": "2026-07-01"},
            "instant": {
                "summary": "An exact instant",
                "description": "Use this precision for intraday windows.",
                "value": "2026-07-01T13:30:00Z",
            },
        },
    ),
]

EndQ = Annotated[
    str | None,
    Query(
        description=(
            "Inclusive end of the window, same grammar as `start`. Defaults to now.\n\n"
            "Specifying `end` is what makes a response cacheable: a window that "
            "closed more than a day ago is immutable and comes back with "
            "`Cache-Control: public, max-age=31536000, immutable` -- unless a "
            "restating symbol is in the request without an `as_of` to pin it, since "
            "that history is exactly the thing that can still change."
        ),
        openapi_examples={
            "date": {"summary": "A plain date", "value": "2026-07-31"},
            "instant": {"summary": "An exact instant", "value": "2026-07-31T20:00:00Z"},
        },
    ),
]

LimitQ = Annotated[
    int | None,
    Query(
        description=(
            f"Maximum bars in this page, 1 to {BARS_MAX_LIMIT:,} (default 1,000). "
            "Counts **total bars across all requested symbols**, not per symbol -- "
            "so a 3-symbol request with `limit=10` returns 10 bars in total, and the "
            "rest arrive through `next_page_token`."
        ),
        openapi_examples={
            "small": {
                "summary": "A small page",
                "description": "Small enough to force pagination and see next_page_token.",
                "value": 5,
            },
            "max": {"summary": "The maximum", "value": BARS_MAX_LIMIT},
        },
    ),
]

PageTokenQ = Annotated[
    str | None,
    Query(
        description=(
            "Resume from a previous response's `next_page_token`, passed back "
            "unmodified. The token encodes the symbol and timestamp of the last bar "
            "served, so a page boundary never duplicates or skips a bar. "
            "`next_page_token: null` means the window is exhausted."
        ),
    ),
]

SortQ = Annotated[
    str,
    Query(
        description=(
            "`asc` (oldest first, the default) or `desc` (newest first). Pagination "
            "follows the same direction."
        ),
        json_schema_extra={"enum": ["asc", "desc"]},
    ),
]

AdjustmentQ = Annotated[
    str | None,
    Query(
        description=(
            "Which corporate actions to apply: `raw` (none), `split`, `dividend`, or "
            "`all`. **Defaults to `all`**, which differs from Alpaca's `raw` -- and "
            "is observable only on `SPLITS`, `DIVVY` and `REVISED`, the only symbols "
            "with any actions to apply. Pair with `as_of` to choose the vantage point "
            "the adjustment is made from."
        ),
        json_schema_extra={"enum": ["raw", "split", "dividend", "all"]},
    ),
]

AsOfQ = Annotated[
    str | None,
    Query(
        description=(
            "Answer as the feed would have on this date (RFC-3339 or `YYYY-MM-DD`). "
            "The second axis of the determinism guarantee: pin it and these bytes "
            "never change again; omit it and restating symbols answer as of today.\n\n"
            "Only `SPLITS`, `DIVVY` and `REVISED` restate -- for every other symbol "
            "`as_of` is a no-op, and `X-Cuckoo-Restated: 0 actions applied` says so. "
            "Note the underscore: Alpaca's own `asof` is a symbol-mapping date and "
            "means something else entirely."
        ),
        openapi_examples={
            "before": {
                "summary": "Before the action lands",
                "description": "Pre-split prices, as anyone querying that day would have stored.",
                "value": "2026-06-09",
            },
            "after": {
                "summary": "After the action lands",
                "description": "The same window, restated. Compare the two.",
                "value": "2026-07-09",
            },
        },
    ),
]

# Kept as loose text as well as annotated types: the providers that hand-parse
# their parameters (to keep error bodies in their own shape) still need to say
# the same thing about them.
SEED_TEXT = (
    "Remix the entire universe: same structural realism, an entirely different "
    "history. Any string. Omitting it gives the canonical universe everyone "
    "shares -- useful when a bug report needs to be reproducible by someone "
    "else. Two different seeds never share a bar."
)

GENERATION_TEXT = (
    f"Pin the generator version. This deployment serves generation {GENERATION}; "
    "asking for any other is an error rather than a silent fallback. Old "
    "generations stay queryable forever once superseded -- the guarantee is that "
    "within a generation, identical requests return identical data permanently, "
    "so pin this if you are storing golden files."
)

SeedQ = Annotated[
    str | None,
    Query(
        description=SEED_TEXT,
        openapi_examples={
            "canonical": {
                "summary": "The shared universe",
                "description": "Omit the parameter, or pass an empty string.",
                "value": "",
            },
            "alternate": {
                "summary": "A private universe",
                "description": "Any string works. Give each test suite its own.",
                "value": "ci-run-42",
            },
        },
    ),
]

GenerationQ = Annotated[int, Query(description=GENERATION_TEXT)]

ScenarioQ = Annotated[
    str,
    Query(
        description=(
            "Comma-separated transport faults to inject. Nothing fires unless you ask, "
            "and the same spec fails the same way every time. See the fault injection "
            "table above for the full grammar."
        ),
    ),
]

# Accepted-and-ignored parameters exist so that an SDK sending them does not
# break. Saying which are inert -- and why -- prevents the reasonable assumption
# that passing one changed something.
FeedQ = Annotated[
    str | None,
    Query(
        description=(
            "Accepted and ignored: there is exactly one synthetic feed here, so "
            "`sip`, `iex` and `otc` would all return the same bars. Present so that "
            "an SDK sending it does not break."
        ),
    ),
]

AlpacaAsofQ = Annotated[
    str | None,
    Query(
        description=(
            "Accepted and ignored. This is **Alpaca's** `asof` -- the date its "
            "symbol-mapping is resolved at, for tickers that were reused after a "
            "delisting. It is not the restatement knob; that one is `as_of`, with an "
            "underscore."
        ),
    ),
]

CurrencyQ = Annotated[
    str | None,
    Query(
        description="Accepted and ignored: prices are USD. Present so SDKs sending it do not break.",
    ),
]


# --------------------------------------------------------------------------
# Response building blocks
# --------------------------------------------------------------------------


# get_openapi() ends with jsonable_encoder(..., exclude_none=True), which strips
# every null in the document -- examples included. That silently deleted
# `"next_page_token": null` from the bars examples, which is the one field those
# examples most need to show, since it is the only reliable stop condition for
# pagination. Examples write this sentinel instead and finalize() turns it back
# into a real null afterwards. The NUL prefix keeps it from ever colliding with
# a genuine string.
NULL = "\u0000null"


def ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def header(description: str, example, kind: str = "string") -> dict:
    return {"description": description, "schema": {"type": kind}, "example": example}


def example(summary: str, value, description: str | None = None) -> dict:
    entry = {"summary": summary, "value": value}
    if description:
        entry["description"] = description
    return entry


def response(description: str, *, schema=None, examples=None, headers=None,
             links=None) -> dict:
    """One OpenAPI Response Object, with the pieces FastAPI cannot infer.

    Schemas are hand-written rather than derived from a `response_model`
    deliberately: a response model would make FastAPI re-serialize and filter
    the body, and these routes are wire-compatible surfaces whose bytes must
    stay exactly as the provider modules built them.
    """
    body: dict = {"description": description}
    if schema is not None or examples is not None:
        content: dict = {}
        if schema is not None:
            content["schema"] = schema
        if examples is not None:
            content["examples"] = examples
        body["content"] = {"application/json": content}
    if headers:
        body["headers"] = headers
    if links:
        body["links"] = links
    return body


def link(description: str, operation_id: str, parameters: dict) -> dict:
    """One OpenAPI Link Object: which field of this response feeds which
    parameter of which operation.

    Worth the effort here because the two workflows this API is built around
    are both multi-call, and both are the kind of thing prose describes
    ambiguously: paging (`next_page_token` becomes `page_token`) and
    restatement (an action's `process_date` becomes a bars `as_of`). Stated as
    links, tooling can follow them and a reader cannot mistake which field goes
    where.
    """
    return {
        "description": description,
        "operationId": operation_id,
        "parameters": parameters,
    }


def extras(
    *,
    samples: tuple[tuple[str, str, str], ...] = (),
    mimics: tuple[str, str] | None = None,
) -> dict:
    """The parts of an Operation Object FastAPI has no argument for, ready to
    hand to a route's `openapi_extra=`.

    `samples` become `x-codeSamples`, which ReDoc renders as a language-tabbed
    panel beside the endpoint. Swagger UI ignores the key, which is why the
    same calls also appear in the endpoint descriptions.

    `mimics` links the operation to the real provider page it replicates, so
    "what does this field actually mean" has an authoritative answer one click
    away rather than a paraphrase here.
    """
    extra: dict = {}
    if samples:
        extra["x-codeSamples"] = [
            {"lang": lang, "label": label, "source": source}
            for lang, label, source in samples
        ]
    if mimics:
        description, url = mimics
        extra["externalDocs"] = {"description": description, "url": url}
    return extra


# Set by the middleware on every response there is, errors included.
MARKING_HEADERS = {
    "X-Cuckoo-Synthetic": header(
        "Always `true`. Present on every response including errors, so a body that "
        "reached a log or a screenshot can always be traced back to synthetic data.",
        "true",
    ),
    "X-Cuckoo-Generation": header(
        "Which generator version produced this data. Pin it with `generation=` to "
        "keep bytes stable across future generations.",
        str(GENERATION),
    ),
    "X-Cuckoo-Docs": header(
        "Where to read more. Here for whoever finds this response in a log with no "
        "other context.",
        DOCS_URL,
    ),
}

# Set on everything the limiter covers -- which is everything but the probe.
RATE_HEADERS = {
    "RateLimit-Limit": header("Burst capacity of the per-IP bucket.", "120"),
    "RateLimit-Remaining": header("Tokens left in this caller's bucket.", "119"),
    "RateLimit-Reset": header(
        "Seconds until the bucket holds enough for one more request. Back off on this.",
        "0",
    ),
}

# Headers specific to bar endpoints, declared by the routes that set them.
BAR_HEADERS = {
    "X-Cuckoo-As-Of": header(
        "The vantage point this answer was computed from -- the `as_of` you sent, or "
        "now if you sent none.",
        "2026-07-09T00:00:00Z",
    ),
    "X-Cuckoo-Restated": header(
        "How many corporate actions rewrote the bars in this response, and which. "
        "`0 actions applied` is the informative case: nothing changed these bars, "
        "usually because the window sits after every ex-date. Without this header a "
        "working restatement and a broken one look identical -- both are a clean 200 "
        "full of plausible bars.",
        "1 actions applied (SPLITS split ex 2026-06-10)",
    ),
    "Cache-Control": header(
        "`public, max-age=31536000, immutable` when the window is fully specified, "
        "closed more than a day ago, and pinned enough that it can never change. "
        "Absent otherwise; `no-store` on any response faulted by `scenario=`.",
        "public, max-age=31536000, immutable",
    ),
}


SCHEMAS: dict = {
    "AlpacaBar": {
        "type": "object",
        "title": "Bar (Alpaca shape)",
        "description": "One OHLCV bar, in Alpaca's field naming.",
        "properties": {
            "t": {
                "type": "string",
                "format": "date-time",
                "description": (
                    "Bar **open** timestamp, RFC-3339 UTC. Daily bars are stamped "
                    "midnight ET expressed in UTC -- `04:00:00Z` in daylight time, "
                    "`05:00:00Z` in standard -- which is Alpaca's convention, not "
                    "midnight UTC."
                ),
                "examples": ["2026-07-01T04:00:00Z"],
            },
            "o": {"type": "number", "description": "Open price.", "examples": [312.38]},
            "h": {"type": "number", "description": "High price.", "examples": [325.52]},
            "l": {"type": "number", "description": "Low price.", "examples": [311.91]},
            "c": {"type": "number", "description": "Close price.", "examples": [325.41]},
            "v": {
                "type": "integer",
                "description": (
                    "Volume in shares. Correlated with the size of the bar's return "
                    "and U-shaped across the session, heavier at the open and close. "
                    "`0` on STALE, by design."
                ),
                "examples": [112387921],
            },
            "n": {
                "type": "integer",
                "description": "Trade count. Scales with volume.",
                "examples": [755356],
            },
            "vw": {
                "type": "number",
                "description": "Volume-weighted average price. Always within `l`..`h`.",
                "examples": [317.038105],
            },
        },
        "required": ["t", "o", "h", "l", "c", "v", "n", "vw"],
    },
    "AlpacaBarsResponse": {
        "type": "object",
        "title": "Alpaca bars response",
        "properties": {
            "bars": {
                "type": "object",
                "description": (
                    "Bars keyed by symbol, alphabetically. A requested symbol with no "
                    "bars in the window is present with an empty array. Symbols past "
                    "the point where `limit` ran out are absent entirely and arrive "
                    "on the next page."
                ),
                "additionalProperties": {"type": "array", "items": ref("AlpacaBar")},
            },
            "next_page_token": {
                "type": ["string", "null"],
                "description": (
                    "Pass back as `page_token` for the next page. `null` means the "
                    "window is exhausted -- the only reliable stop condition, since a "
                    "full page can still be the last one."
                ),
            },
        },
        "required": ["bars", "next_page_token"],
    },
    "AlpacaSingleBarsResponse": {
        "type": "object",
        "title": "Alpaca single-symbol bars response",
        "properties": {
            "bars": {
                "type": "array",
                "description": "A bare array here, not a map -- Alpaca's single-symbol shape.",
                "items": ref("AlpacaBar"),
            },
            "symbol": {"type": "string", "description": "The symbol, normalized to upper case."},
            "next_page_token": {"type": ["string", "null"], "description": "As above."},
        },
        "required": ["bars", "symbol", "next_page_token"],
    },
    "AlpacaLatestBarsResponse": {
        "type": "object",
        "title": "Alpaca latest bars response",
        "properties": {
            "bars": {
                "type": "object",
                "description": (
                    "One bar per symbol -- a single object, not an array. A symbol "
                    "whose most recent bar cannot be determined is omitted rather "
                    "than returned null."
                ),
                "additionalProperties": ref("AlpacaBar"),
            }
        },
        "required": ["bars"],
    },
    "AlpacaError": {
        "type": "object",
        "title": "Alpaca-shaped error",
        "description": (
            "Alpaca's error shape. `code` is a numeric code, not the HTTP status; the "
            "status is on the response. `message` states the valid grammar and "
            "includes a URL that works."
        ),
        "properties": {
            "code": {
                "type": "integer",
                "description": "Numeric error code, e.g. `40010001` for a malformed parameter.",
                "examples": [40010001],
            },
            "message": {
                "type": "string",
                "description": "Human- and agent-readable explanation, with a working example URL.",
            },
        },
        "required": ["code", "message"],
    },
    "PolygonResult": {
        "type": "object",
        "title": "Aggregate bar (Polygon shape)",
        "description": "One aggregate bar, in Polygon's single-letter field naming.",
        "properties": {
            "v": {"type": "integer", "description": "Volume in shares.", "examples": [16323534]},
            "vw": {"type": "number", "description": "Volume-weighted average price, 4dp.", "examples": [546.4654]},
            "o": {"type": "number", "description": "Open price.", "examples": [542.29]},
            "c": {"type": "number", "description": "Close price.", "examples": [547.47]},
            "h": {"type": "number", "description": "High price.", "examples": [550.34]},
            "l": {"type": "number", "description": "Low price.", "examples": [541.81]},
            "t": {
                "type": "integer",
                "description": (
                    "Bar **open** timestamp as Unix **milliseconds** -- Polygon's "
                    "convention, and the one place their shape differs most visibly "
                    "from Alpaca's RFC-3339 strings."
                ),
                "examples": [1782878400000],
            },
            "n": {"type": "integer", "description": "Trade count.", "examples": [92101]},
            "T": {
                "type": "string",
                "description": (
                    "Ticker. Present **only** on the previous-close endpoint, exactly "
                    "as the real API does it."
                ),
            },
        },
        "required": ["v", "vw", "o", "c", "h", "l", "t", "n"],
    },
    "PolygonAggsResponse": {
        "type": "object",
        "title": "Polygon aggregates response",
        "description": (
            "Key order is preserved to match live Polygon responses. When the window "
            "is empty, `results` and `count` are omitted entirely rather than sent as "
            "`[]` and `0` -- that is what the real API does, and clients depend on it."
        ),
        "properties": {
            "ticker": {"type": "string", "description": "The ticker, normalized to upper case."},
            "queryCount": {"type": "integer", "description": "Bars matched. Equal to `resultsCount` here."},
            "resultsCount": {"type": "integer", "description": "Bars in `results`."},
            "adjusted": {
                "type": "boolean",
                "description": "Echoed from the request. Nothing on this surface restates, so it has no effect.",
            },
            "results": {
                "type": "array",
                "items": ref("PolygonResult"),
                "description": "Omitted when no bars matched.",
            },
            "status": {
                "type": "string",
                "description": (
                    "Always `OK`. The real free tier can return `DELAYED`; "
                    "deterministic synthetic data never is, so claiming otherwise "
                    "would be a lie."
                ),
                "examples": ["OK"],
            },
            "request_id": {
                "type": "string",
                "description": (
                    "md5 of the request parameters, not a random id -- identical "
                    "requests must return identical bytes, which a random id would "
                    "break. Stable enough to assert on in tests."
                ),
            },
            "count": {"type": "integer", "description": "Omitted when no bars matched."},
            "next_url": {
                "type": "string",
                "description": (
                    "Absolute URL of the next page, present only when more bars "
                    "remain. GET it unmodified -- the cursor carries the query state, "
                    "and editing it invalidates the cursor."
                ),
            },
        },
        "required": ["ticker", "queryCount", "resultsCount", "adjusted", "status", "request_id"],
    },
    "PolygonError": {
        "type": "object",
        "title": "Polygon-shaped error",
        "properties": {
            "status": {"type": "string", "description": "Always `ERROR`.", "examples": ["ERROR"]},
            "request_id": {"type": "string", "description": "md5 of the failed request."},
            "error": {"type": "string", "description": "What was wrong, and what would be valid."},
        },
        "required": ["status", "request_id", "error"],
    },
    "AlphaVantageValues": {
        "type": "object",
        "title": "OHLCV row (Alpha Vantage shape)",
        "description": (
            "One bar. Every value is a **string**, and the keys are numbered -- both "
            "are Alpha Vantage's real conventions, so a parser that assumes JSON "
            "numbers fails here exactly as it would against the real API."
        ),
        "properties": {
            "1. open": {"type": "string", "description": "Open price, 4 decimal places.", "examples": ["169.9900"]},
            "2. high": {"type": "string", "description": "High price.", "examples": ["173.5800"]},
            "3. low": {"type": "string", "description": "Low price.", "examples": ["169.9000"]},
            "4. close": {"type": "string", "description": "Close price.", "examples": ["172.5600"]},
            "5. volume": {"type": "string", "description": "Volume in shares, as an integer string.", "examples": ["3416035"]},
        },
        "required": ["1. open", "2. high", "3. low", "4. close", "5. volume"],
    },
    "AlphaVantageSeries": {
        "type": "object",
        "title": "Time series response",
        "description": (
            "What every `TIME_SERIES_*` function returns: a `Meta Data` block, plus "
            "exactly one series key whose *name depends on the function* -- "
            "`Time Series (Daily)`, `Weekly Time Series`, `Monthly Time Series`, or "
            "`Time Series (5min)` and friends. Nothing has a fixed name to bind to, "
            "so clients typically take the one key that is not `Meta Data`."
        ),
        "properties": {
            "Meta Data": {
                "type": "object",
                "description": (
                    "Numbered metadata. The numbering shifts between functions: "
                    "intraday inserts `4. Interval`, pushing Time Zone to `6.`, while "
                    "weekly and monthly have no Output Size at all."
                ),
                "additionalProperties": {"type": "string"},
            }
        },
        "additionalProperties": {
            "type": "object",
            "description": "The series itself: bars keyed by timestamp label, newest first.",
            "additionalProperties": ref("AlphaVantageValues"),
        },
        "required": ["Meta Data"],
    },
    "AlphaVantageQuote": {
        "type": "object",
        "title": "GLOBAL_QUOTE response",
        "properties": {
            "Global Quote": {
                "type": "object",
                "description": "Zero-padded keys, string values. Empty when there is no quote to give.",
                "properties": {
                    "01. symbol": {"type": "string", "examples": ["IBM"]},
                    "02. open": {"type": "string", "description": "Latest session's open."},
                    "03. high": {"type": "string", "description": "Latest session's high."},
                    "04. low": {"type": "string", "description": "Latest session's low."},
                    "05. price": {"type": "string", "description": "Latest close -- the 'current' price.", "examples": ["172.5600"]},
                    "06. volume": {"type": "string", "description": "Latest session's volume."},
                    "07. latest trading day": {"type": "string", "description": "`YYYY-MM-DD` of that session.", "examples": ["2026-08-18"]},
                    "08. previous close": {"type": "string", "description": "The session before it."},
                    "09. change": {"type": "string", "description": "Absolute change against the previous close."},
                    "10. change percent": {
                        "type": "string",
                        "description": "Percentage change, with a literal `%` inside the string. Strip it before parsing.",
                        "examples": ["1.3926%"],
                    },
                },
            }
        },
        "required": ["Global Quote"],
    },
    "AlphaVantageError": {
        "type": "object",
        "title": "Alpha Vantage-shaped error",
        "description": (
            "Returned with **HTTP 200**, faithfully to the real API. Client libraries "
            "sniff for this key rather than checking the status, so this mimic must "
            "too. The one exception is `scenario=status:CODE`, where the caller named "
            "the status they wanted to test against and that wins over the mimicry."
        ),
        "properties": {
            "Error Message": {
                "type": "string",
                "description": "What was wrong, with the valid grammar and a working example URL.",
            }
        },
        "required": ["Error Message"],
    },
    "CorporateAction": {
        "type": "object",
        "title": "Corporate action",
        "description": (
            "One action, carrying the three dates a reconciliation job actually needs. "
            "The gap between `ex_date` and `process_date` is the entire point of "
            "DIVVY: the adjustment lands days after the ex-date, long after a job "
            "that polls on the ex-date has decided the month is settled."
        ),
        "properties": {
            "symbol": {
                "type": "string",
                "description": "The symbol this action belongs to.",
                "examples": ["SPLITS"],
            },
            "type": {
                "type": "string",
                "description": (
                    "`split` (prior closes rescale by `ratio`), `dividend` (prior "
                    "closes adjust down by `cash_amount`), or `correction` (a bad "
                    "print the exchange later busted, which disappears from history)."
                ),
                "enum": ["split", "dividend", "correction"],
            },
            "announce_date": {
                "type": "string",
                "format": "date",
                "description": "When the action became public knowledge. Nothing changes yet.",
            },
            "ex_date": {
                "type": "string",
                "format": "date",
                "description": (
                    "The session from which the action is in effect. Bars dated "
                    "**before** this get rewritten; bars on or after it never do -- "
                    "which is why a window entirely after every ex-date reports "
                    "`0 actions applied`."
                ),
            },
            "process_date": {
                "type": "string",
                "format": "date",
                "description": (
                    "When the restatement actually lands in history. Request the same "
                    "window with `as_of` set either side of this date and the bars "
                    "differ. For DIVVY it is five sessions after `ex_date`."
                ),
            },
            "ratio": {
                "type": "number",
                "description": "Split ratio, e.g. `2.0` for 2:1. `1.0` for non-splits.",
                "examples": [2.0],
            },
            "cash_amount": {
                "type": "number",
                "description": "Dividend per share. `0.0` for non-dividends.",
                "examples": [1.8],
            },
            "synthetic": {"type": "boolean", "description": "Always `true`."},
        },
        "required": [
            "symbol", "type", "ex_date", "announce_date", "process_date",
            "ratio", "cash_amount", "synthetic",
        ],
    },
    "CorporateActionsResponse": {
        "type": "object",
        "title": "Corporate actions ledger",
        "properties": {
            "synthetic": {"type": "boolean", "description": "Always `true`."},
            "generation": {"type": "integer", "description": "Generator version behind these actions."},
            "as_of": {
                "type": "string",
                "format": "date-time",
                "description": (
                    "The vantage point used. Actions whose `process_date` is after "
                    "this are not yet known and are withheld -- the ledger restates "
                    "exactly like the bars do."
                ),
            },
            "start": {"type": "string", "format": "date-time", "description": "Window start, defaulting to 180 days before `end`."},
            "end": {"type": "string", "format": "date-time", "description": "Window end, defaulting to `as_of`."},
            "actions": {
                "type": "array",
                "items": ref("CorporateAction"),
                "description": "Sorted by `ex_date`, then symbol. Empty for every non-restating symbol.",
            },
            "restating_tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Every symbol in this deployment that has actions at all. Everything else is inert here.",
            },
            "how_to_use": {"type": "string", "description": "A one-paragraph reminder of what to do with `process_date`."},
            "docs": {"type": "string", "description": "Link to the prose documentation."},
        },
        "required": ["synthetic", "generation", "as_of", "start", "end", "actions"],
    },
    "IndexResponse": {
        "type": "object",
        "title": "Service index",
        "description": (
            "A machine-readable orientation document: every provider, every endpoint, "
            "the ticker tables, and a working example URL for each. Deliberately "
            "self-contained -- an agent that lands here with no other context has "
            "enough to make a correct first call without fetching anything else."
        ),
        "additionalProperties": True,
    },
    "HealthResponse": {
        "type": "object",
        "title": "Health",
        "properties": {
            "status": {
                "type": "string",
                "description": "Always `ok` -- the probe answers or it does not answer.",
                "examples": ["ok"],
            }
        },
        "required": ["status"],
    },
}


# --------------------------------------------------------------------------
# Cross-cutting injection
# --------------------------------------------------------------------------

_SCENARIO_PARAM = {
    "name": "scenario",
    "in": "query",
    "required": False,
    "description": (
        "Inject deterministic transport faults, comma-separated. Handled by "
        "middleware, so it works on every endpoint here.\n\n"
        "`flap:N` fails N times then succeeds; `status:CODE` returns that status in "
        "this provider's error shape; `slow:MS` delays the response; `truncate` sends "
        "a full `Content-Length` with half a body. The stream takes `drop:S`, "
        "`garbage:N` and `silent:S` instead.\n\n"
        "Faulted responses carry `Cache-Control: no-store` and echo "
        "`X-Cuckoo-Scenario`. `flap` counts attempts per pod, so across replicas a "
        "`flap:N` can burn up to N x replicas failures -- run the container locally "
        "when the count has to be exact."
    ),
    "schema": {"type": "string"},
    "examples": {
        "none": {"summary": "No faults (default)", "value": ""},
        "flap": {
            "summary": "Fail twice, then succeed",
            "description": "The retry test. A client with sane backoff sees a 200 on attempt three.",
            "value": "flap:2",
        },
        "status": {
            "summary": "Force a 503",
            "description": "Rendered in this provider's own error shape, so it exercises your real parser.",
            "value": "status:503",
        },
        "slow": {"summary": "Delay 2 seconds", "description": "Finds missing timeouts.", "value": "slow:2000"},
        "truncate": {
            "summary": "Half a body",
            "description": "Content-Length promises more than arrives -- what a connection dying in flight looks like.",
            "value": "truncate",
        },
        "combined": {"summary": "Slow and flaky at once", "value": "slow:500,flap:2"},
    },
}

_RATE_LIMITED = response(
    "Rate limited. 60 requests/minute sustained, burst 120, per IP address, no key "
    "required. Back off using `RateLimit-Reset`.",
    schema=ref("AlpacaError"),
    examples={
        "limited": example(
            "Rate limit exceeded",
            {
                "code": 42910000,
                "message": (
                    "rate limit exceeded: 60 requests/minute sustained (burst 120) per "
                    "address, no key required. Back off using the RateLimit-Reset header."
                ),
            },
        )
    },
)


# ReDoc renders these as top-level sidebar sections. Two groups, because the
# distinction they draw is the one that decides which docs a reader needs: a
# mimicked surface sends you to that provider's documentation, a native one
# does not.
TAG_GROUPS = [
    {
        "name": "Provider-compatible surfaces",
        "tags": ["provider: alpaca", "provider: polygon", "provider: alphavantage"],
    },
    {"name": "CuckooTrade native", "tags": ["cuckoo-native", "meta"]},
]


def _restore_nulls(node):
    """Turn the NULL sentinel back into real nulls, in place."""
    if isinstance(node, dict):
        for key, value in node.items():
            if value == NULL:
                node[key] = None
            else:
                _restore_nulls(value)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if value == NULL:
                node[index] = None
            else:
                _restore_nulls(value)


def finalize(schema: dict) -> dict:
    """Add what no route can declare for itself.

    `scenario=`, the `X-Cuckoo-*` headers and the 429 all come from the
    middleware in api.py, so they belong to every operation and to no route.
    Injecting them once here beats repeating them on each endpoint, and means a
    provider added later is documented correctly without touching this file.
    """
    schema["externalDocs"] = EXTERNAL_DOCS
    schema["x-tagGroups"] = TAG_GROUPS
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(SCHEMAS)
    # FastAPI generates these for the 422 it would raise; this app never raises
    # one, so they would be two schemas nothing references.
    for unused in ("HTTPValidationError", "ValidationError"):
        schemas.pop(unused, None)

    _restore_nulls(schema)

    for path, item in schema["paths"].items():
        for operation in item.values():
            if not isinstance(operation, dict):
                continue

            # `scenario=` acts in the middleware, which skips the unversioned
            # paths; the probe is also the one path the limiter exempts, so it
            # never carries RateLimit headers or returns a 429.
            versioned = path.startswith("/api/v1")
            limited = path != "/api/health"
            declared = {p.get("name") for p in operation.get("parameters", [])}
            if versioned and "scenario" not in declared:
                operation.setdefault("parameters", []).append(_SCENARIO_PARAM)

            responses = operation.setdefault("responses", {})
            # FastAPI's default 422 never fires: RequestValidationError is
            # remapped to a 400 in the mimicked provider's error shape (api.py).
            responses.pop("422", None)
            if limited:
                responses.setdefault("429", _RATE_LIMITED)
            common = {**MARKING_HEADERS, **(RATE_HEADERS if limited else {})}
            for body in responses.values():
                # Route-declared headers win: a bar endpoint has more to say
                # about Cache-Control than any generic entry could.
                body["headers"] = {**common, **body.get("headers", {})}
    return schema


__all__ = [
    "AdjustmentQ", "AlpacaAsofQ", "AsOfQ", "BAR_HEADERS", "CurrencyQ", "DESCRIPTION",
    "DISCLAIMER", "DOCS_URL", "EndQ", "EXTERNAL_DOCS", "FeedQ", "GenerationQ", "LimitQ",
    "PageTokenQ", "SCHEMAS", "SERVERS", "SUMMARY", "ScenarioQ", "SeedQ", "SortQ",
    "StartQ", "SymbolPathQ", "SymbolsQ", "TAGS", "TimeframeQ", "example", "extras", "finalize",
    "header", "link", "ref", "response", "NULL", "TAG_GROUPS", "SEED_TEXT", "GENERATION_TEXT",
]
