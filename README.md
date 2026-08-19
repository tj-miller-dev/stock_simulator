# CuckooTrade

**Market data for everything before production.** A free, deterministic API
serving openly synthetic data — no key, no signup — over the wire formats of
Alpaca, Alpha Vantage, and Polygon. For development, CI, demos, and teaching.
Live at **[cuckootrade.com](https://cuckootrade.com)**.

```bash
curl 'https://cuckootrade.com/api/v1/alpaca/v2/stocks/bars?symbols=AAPL,CRASH&timeframe=1Day&start=2026-07-01'
```

That works right now, from anywhere, with no account. Using
[alpaca-py](https://github.com/alpacahq/alpaca-py)? Change one line:

```python
client = StockHistoricalDataClient(
    api_key="any", secret_key="any",                        # never checked
    url_override="https://cuckootrade.com/api/v1/alpaca",   # <- the whole integration
)
```

Paths are provider-namespaced and versioned: `/api/v1/{provider}/…` mimics
that provider's wire format, while `/api/v1/stream` and friends are
CuckooTrade-native. The path `v1` versions the API surface; the `generation`
parameter versions the data. Three providers are live, all serving the same
deterministic world:

```bash
# Alpha Vantage format (intraday included — premium on the real API, free here)
curl 'https://cuckootrade.com/api/v1/alphavantage/query?function=TIME_SERIES_DAILY&symbol=IBM'
# Polygon aggregates format
curl 'https://cuckootrade.com/api/v1/polygon/v2/aggs/ticker/MSFT/range/1/day/2026-07-01/2026-08-01'
```

## Why

Developing against real market data means API keys in CI, rate limits while
you iterate, closed markets on weekends, licensing questions in demos, and
tests that can never be reproduced byte-for-byte. CuckooTrade is the Stripe
test mode of market data:

- **No key, no signup** — first request works from curl, CI, or a coding
  agent. 60 req/min per address, burst 120.
- **Deterministic** — every bar is a pure function of (symbol, timestamp,
  generation, seed). Identical requests return identical bytes, forever.
  `&seed=anything` selects an alternate universe.
- **Alpaca wire-compatible** — real SDKs parse it unmodified; the acceptance
  test in CI is literally alpaca-py pointed at this server.
- **Realistic enough** — NYSE calendar (no bars on weekends/holidays,
  09:30–16:00 ET sessions), per-symbol personalities (~130 curated tickers at
  plausible price levels, stable hash-derived traits for any other string),
  volatility regimes, volume that follows the action, and exact cross-timeframe
  coherence (minute bars aggregate to the daily bar, days to weeks).

## Scenario tickers

Reserved symbols with scripted, calendar-anchored behavior — each pattern
visible in any 30-day window:

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

Real feeds *restate*: a split or a late dividend rewrites bars you already
stored. `as_of` models that without giving up determinism — pin it and the
bytes never change, omit it and history moves under you like a real vendor's:

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

Scenario tickers break the data. `scenario=` breaks the transport — dropped
sockets, half-written bodies, requests that fail twice before they work:

```bash
# fails twice, succeeds on the third attempt
curl 'https://cuckootrade.com/api/v1/alpaca/v2/stocks/bars?symbols=AAPL&timeframe=1Day&scenario=flap:2'

# the socket dies twenty seconds in, mid-frame, with no close event
curl -N 'https://cuckootrade.com/api/v1/stream?symbols=CUCKOO&scenario=drop:20s'
```

Nothing fires unless you ask for it, and every fault is deterministic — which
is the point. Random chaos makes flaky tests; these are meant to run in CI.

There's also an SSE stream (`curl -N
'https://cuckootrade.com/api/v1/stream?symbols=CUCKOO'`) with an always-open
demo clock, a [playground](https://cuckootrade.com/playground), human
[docs](https://cuckootrade.com/docs), Swagger at
[/api/docs](https://cuckootrade.com/api/docs), and
[/llms.txt](https://cuckootrade.com/llms.txt) for coding agents.

## Self-hosting

The engine is stateless, so a local instance serves byte-identical data to
cuckootrade.com for the same generation:

```bash
docker run -p 8000:8000 ghcr.io/tj-miller-dev/cuckootrade
# or
pip install -r api/requirements.txt && python api/api.py
```

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
