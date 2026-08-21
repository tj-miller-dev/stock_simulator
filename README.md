# CuckooTrade

**CuckooTrade is a free, deterministic synthetic market data API for building
and testing trading software** — with Alpaca-, Polygon- and Alpha
Vantage-compatible endpoints, programmable market anomalies, and deterministic
fault injection. No API key, no signup. Live at
**[cuckootrade.com](https://cuckootrade.com)**.

```bash
curl 'https://cuckootrade.com/api/v1/alpaca/v2/stocks/bars?symbols=AAPL,CRASH&timeframe=1Day&start=2026-07-01'
```

That works right now, from anywhere, with no account.

---

## What is CuckooTrade?

It is the Stripe test mode of market data: a hosted API that speaks the wire
formats real providers speak, serves openly fake data, and never asks for a
credential. Point your app at it during development, CI, demos, and tutorials,
then switch back to your real provider by changing one base URL.

The name is the pitch. A cuckoo lays a convincing mimic egg in another bird's
nest; CuckooTrade is a mimic API sitting in Alpaca's. The data is *proudly*
fake, and every response says so.

## Why it exists

Developing against real market data means API keys in CI, rate limits while you
iterate, closed markets on weekends, licensing questions in demos, and tests
that can never be reproduced byte-for-byte. And it means the code that only
runs during a crash, a halt, or an outage never runs at all before production.

- **No key, no signup** — the first request works from curl, CI, or a coding
  agent. 60 req/min per address, burst 120.
- **Deterministic** — every bar is a pure function of (symbol, timestamp,
  generation, seed, as_of). Identical requests return identical bytes, forever.
  `&seed=anything` selects an alternate universe.
- **Wire-compatible** — real SDKs parse it unmodified; the acceptance test in
  CI is literally alpaca-py pointed at this server.
- **Realistic enough** — NYSE calendar (no bars on weekends/holidays,
  09:30–16:00 ET sessions), per-symbol personalities (~130 curated tickers at
  plausible price levels, stable hash-derived traits for any other string),
  volatility regimes, volume that follows the action, and exact cross-timeframe
  coherence (minute bars aggregate to the daily bar, days to weeks).

## Quickstart

Using [alpaca-py](https://github.com/alpacahq/alpaca-py)? Change one line:

```python
client = StockHistoricalDataClient(
    api_key="any", secret_key="any",                        # never checked
    url_override="https://cuckootrade.com/api/v1/alpaca",   # <- the whole integration
)
```

Prefer a container? The engine is stateless, so a local instance serves
byte-identical data to cuckootrade.com for the same generation:

```bash
docker run -p 8000:8000 ghcr.io/tj-miller-dev/cuckootrade
# or
pip install -r api/requirements.txt && python api/api.py
```

## Supported APIs

Paths are provider-namespaced and versioned: `/api/v1/{provider}/…` mimics that
provider's wire format, while `/api/v1/stream` and friends are CuckooTrade-native.
The path `v1` versions the API surface; the `generation` parameter versions the
data. All three providers serve the same deterministic world, so the same
symbol and day returns identical OHLCV through every surface.

| Provider | Real base URL | Swap in |
|---|---|---|
| Alpaca | `https://data.alpaca.markets` | `https://cuckootrade.com/api/v1/alpaca` |
| Polygon | `https://api.polygon.io` | `https://cuckootrade.com/api/v1/polygon` |
| Alpha Vantage | `https://www.alphavantage.co/query` | `https://cuckootrade.com/api/v1/alphavantage/query` |

```bash
# Alpha Vantage format (intraday included — premium on the real API, free here)
curl 'https://cuckootrade.com/api/v1/alphavantage/query?function=TIME_SERIES_DAILY&symbol=IBM'
# Polygon aggregates format
curl 'https://cuckootrade.com/api/v1/polygon/v2/aggs/ticker/MSFT/range/1/day/2026-07-01/2026-08-01'
```

Keys are accepted and ignored everywhere, so client code that sends them works
unchanged.

## Scenario tickers

Reserved symbols with scripted, calendar-anchored behavior — each pattern
visible in any 30-day window. This is the headline feature: no real data
provider can sell you a crash on a Tuesday at any price.

| ticker | behavior |
|---|---|
| `CRASH` | sharp ~25% crash mid-month, slow grind recovery |
| `MOON` | parabolic pump peaking late in the month, hard correction |
| `FLAT` | zero-range bars pinned at $100.00 — breaks naive chart scaling |
| `GAPPY` | ±5–15% overnight gaps most days |
| `HALTS` | minute bars go missing during intraday halt windows |
| `STALE` | feed freezes mid-session: same price, zero volume, clock keeps moving |
| `SPIKEY` | single-minute fat-finger wicks that instantly revert |
| `PENNY` | ~$0.30 prices with four decimals — flushes float bugs |
| `CHOPPY` | high volatility, zero net drift |
| `SPLITS` | 2:1 split monthly — prior closes halve once it goes ex |
| `DIVVY` | monthly dividend whose ~1.5% adjustment lands five sessions **late** |
| `REVISED` | a bad print that sits in history until the exchange busts the trade |

`STALE` is the one worth dwelling on. Most code checks *did I get a response*,
not *is this response current* — so a frozen feed is worse than a dead one,
because every liveness check you have says green. It is `HALTS` inverted: all
the bars arrive, none of them mean anything.

## The determinism guarantee

**Every bar is a pure function of `(symbol, timestamp, generation, seed, as_of)`.**
No database, no stored state. Two users querying SPY for last March get
identical bytes, regardless of query window, timeframe, other symbols in the
request, or which replica serves them.

The guarantee is versioned: when the generator improves we bump the
`generation`, and **old generations stay queryable forever**. Within a
generation, identical requests return identical data, permanently.

The `as_of` axis is what keeps that honest. Real feeds *restate*: a split or a
late dividend rewrites bars you already stored. Pin `as_of` and the bytes never
change; omit it and history moves under you like a real vendor's:

```bash
# the same window, either side of a corporate action
curl '.../v2/stocks/bars?symbols=SPLITS&timeframe=1Day&start=2026-06-01&end=2026-06-30&as_of=2026-07-09'
curl '.../v2/stocks/bars?symbols=SPLITS&timeframe=1Day&start=2026-06-01&end=2026-06-30&as_of=2026-07-13'

# ...and what changed, with the announce/ex/process dates
curl 'https://cuckootrade.com/api/v1/corporate-actions?symbols=SPLITS,DIVVY'
```

If you keep bars in a database, that's the scenario your reconciliation job is
supposed to catch — and now you can test it without waiting a month.

Use `curl -i`: every bar response reports `X-Cuckoo-Restated: 2 actions applied
(…)`, or `0 actions applied` when nothing rewrote those bars. An action only
rewrites bars dated *before* its ex-date, so query a window that sits behind it.

## Fault injection

Scenario tickers break the data. `scenario=` breaks the transport — dropped
sockets, half-written bodies, requests that fail twice before they work:

```bash
# fails twice, succeeds on the third attempt
curl 'https://cuckootrade.com/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&timeframe=1Day&scenario=flap:2'

# the socket dies twenty seconds in, mid-frame, with no close event
curl -N 'https://cuckootrade.com/api/v1/stream?symbols=CUCKOO&scenario=drop:20s'
```

| effect | surface | behavior |
|---|---|---|
| `flap:N` | HTTP | fail N times, then succeed — the test that *passes* |
| `status:CODE` | HTTP | that provider's error shape, at that status |
| `slow:MS` | both | delay the response / the next frame |
| `truncate` | both | half a body, or one frame cut mid-JSON |
| `drop:S` | stream | close the socket at T+S, mid-frame, no close event |
| `garbage:N` | stream | N invalid `data:` payloads among the good ones |
| `silent:S` | stream | stop data *and* heartbeats for S seconds |

Nothing fires unless you ask for it, and every fault is deterministic — which
is the point. Random chaos makes flaky tests; these are meant to run in CI.

## Live streaming

```bash
curl -N 'https://cuckootrade.com/api/v1/stream?symbols=CUCKOO'
```

Server-Sent Events rather than a WebSocket, deliberately: SSE is curl-able,
which makes it its own documentation. `clock=demo` (the default) is an
always-open synthetic session, so the stream is alive at 11pm on a Sunday;
`clock=real` follows the NYSE calendar and stays quiet when the market is shut.
Comment heartbeats every ~15s keep the connection from idling out.

## Testing examples

```python
# a test that never expires: same window, same bytes, forever
def test_bars_are_byte_stable():
    params = {"symbols": "AAPL", "timeframe": "1Day",
              "start": "2026-07-01", "end": "2026-07-31"}
    assert httpx.get(URL, params=params).text == httpx.get(URL, params=params).text
```

```yaml
# CI with no secrets, no rate limit, and no weekend failures
services:
  market-data:
    image: ghcr.io/tj-miller-dev/cuckootrade
    ports: ["8000:8000"]
env:
  MARKET_DATA_URL: http://localhost:8000/api/v1/alpaca
```

Longer walkthroughs, all of them runnable verbatim:

- [Mock the Alpaca API](https://cuckootrade.com/guides/mock-alpaca-api)
- [Mock the Polygon.io API](https://cuckootrade.com/guides/mock-polygon-api)
- [Mock the Alpha Vantage API](https://cuckootrade.com/guides/mock-alpha-vantage-api)
- [Test a trading bot against a market crash, halt, or gap](https://cuckootrade.com/guides/test-trading-bot-market-crash)
- [Test retry logic and API failures deterministically](https://cuckootrade.com/guides/test-retry-logic-and-api-failures)
- [Test a streaming client when markets are closed](https://cuckootrade.com/guides/test-sse-market-data-streams)
- [Run market data tests in CI without API keys](https://cuckootrade.com/guides/market-data-in-ci-without-api-keys)

## What it is NOT

- **Not real market data**, and it never pretends to be. Every response carries
  `X-Cuckoo-Synthetic: true`.
- **Not a backtesting or strategy-validation tool.** This is the one way the
  project could genuinely hurt someone: synthetic data will make a bad strategy
  look validated. A profitable backtest here means the strategy learned the
  generator, not the market.
- **Not financial advice**, and never an input to a real trade.
- **Not a broker.** No orders, fills, or positions — a "fake broker" against
  this feed is the named V2.
- **Not monetized.** No keys, no billing, no pricing page.

## Other surfaces

There's a [playground](https://cuckootrade.com/playground), human
[docs](https://cuckootrade.com/docs) and [guides](https://cuckootrade.com/guides),
Swagger at [/api/docs](https://cuckootrade.com/api/docs), and
[/llms.txt](https://cuckootrade.com/llms.txt) plus
[/llms-full.txt](https://cuckootrade.com/llms-full.txt) for coding agents.

## Development

```bash
cd api && pip install -r requirements-dev.txt && python -m pytest tests/
cd frontend && npm ci && npm run dev
```

The golden tests in `api/tests/test_golden.py` pin generation-1 output
byte-for-byte — if they fail, you changed history; introduce a new generation
instead. Project orientation for humans and AI assistants:
[docs/OVERVIEW.md](docs/OVERVIEW.md); the build spec:
[docs/V1_SPEC.md](docs/V1_SPEC.md).

## How it runs

EKS + ArgoCD GitOps + Terraform, deployed automatically on every push to
`main` via GitHub Actions with OIDC (no stored cloud keys). The whole
pipeline is in this repo — the rebuild-from-nothing runbook is
[docs/QUICK_START.md](docs/QUICK_START.md).

## Disclaimer

Every bar is fiction, and every response says so
(`X-Cuckoo-Synthetic: true`). CuckooTrade exercises code paths; it is not
market data, not financial advice, and not a backtesting oracle — a
profitable strategy on synthetic data means nothing.

MIT licensed.
