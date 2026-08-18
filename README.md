# CuckooTrade

**100% real fake market data.** A free, deterministic, Alpaca-compatible
market data API with no key and no signup — for development, CI, demos, and
teaching. Live at **[cuckootrade.com](https://cuckootrade.com)**.

```bash
curl 'https://cuckootrade.com/api/v2/stocks/bars?symbols=AAPL,CRASH&timeframe=1Day&start=2026-07-01'
```

That works right now, from anywhere, with no account. Using
[alpaca-py](https://github.com/alpacahq/alpaca-py)? Change one line:

```python
client = StockHistoricalDataClient(
    api_key="any", secret_key="any",              # never checked
    url_override="https://cuckootrade.com/api",   # <- the whole integration
)
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

## Magic tickers

Scripted, calendar-anchored scenarios no real data provider can sell you —
each visible in any 30-day window:

| ticker | behavior |
|---|---|
| `CRASH` | sharp ~25% crash mid-month, slow grind recovery |
| `MOON` | parabolic pump peaking late in the month, hard correction |
| `FLAT` | zero-range bars pinned at $100.00 — breaks naive chart scaling |
| `GAPPY` | ±5–15% overnight gaps most days |
| `HALTS` | minute bars go missing during intraday halt windows |
| `SPIKEY` | single-minute fat-finger wicks that instantly revert |
| `PENNY` | ~$0.30 prices with four decimals — flushes float bugs |
| `CHOPPY` | high volatility, zero net drift |

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
