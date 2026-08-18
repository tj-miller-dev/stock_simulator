# CuckooTrade V1 Specification

Status: **agreed, not yet built** (Aug 2026). Context and rationale live in
[OVERVIEW.md](OVERVIEW.md) — read that first. This document is the build contract:
what V1 must do, in what order, and how we'll know it's done.

The job V1 does, in one sentence: *give me a reproducible market data feed that speaks
my broker's API, with no signup.*

---

## 1. The determinism contract (the promise everything rests on)

**Every bar is a pure function of `(symbol, timestamp, generation, seed)`.**

- No database, no stored state. Two users querying SPY for last March get identical
  bytes, regardless of query window, timeframe, other symbols in the request, or which
  replica serves them.
- `seed` (Cuckoo extension, optional) remixes the universe: same structural realism,
  different history. Omitted `seed` = the **canonical universe** everyone shares.
- `generation` (Cuckoo extension, optional) pins the generator version. Defaults to
  latest. Responses always state which generation produced them. When we improve the
  generator we bump the generation; **old generations remain queryable forever** (it's
  just code — cheap to keep). The stability guarantee is: *within a generation,
  identical requests return identical data, permanently.*
- Enforced by golden-file byte-stability tests in CI (see §9).

### 1.1 Generator architecture (hierarchical, random-access)

The naive approach (one RNG walking from request start) breaks global consistency —
the same bar changes depending on the query window. Instead, generate hierarchically
so any bar is reachable in bounded work without generating unrelated history:

```
personality(symbol)            hash-derived (or curated) traits          O(1)
  └─ yearly anchor price       per-(symbol, year) hashed annual return   O(years)
       └─ daily bars           per-(symbol, year) seeded walk over the
                               year's trading days                       O(≤252)
            └─ minute bars     per-(symbol, date) seeded bridge from
                               that day's open→close, constrained to
                               the day's precomputed high/low            O(390)
```

All hashing derives from SHA-256 over `f"gen{N}:{seed}:{symbol}:{scope}"`. Weekly and
monthly bars **aggregate from daily bars**; hour and N-minute bars **aggregate from
minute bars**; daily high/low are drawn first and the minute bridge is rescaled to hit
them exactly.

This buys the property naive mocks never have and quants will absolutely check:

> **Cross-timeframe coherence.** For nested timeframes, aggregating the finer bars
> reproduces the coarser bar exactly — O/H/L/C match, V and n are additive.

Implementation note: generate minute paths with vectorized numpy, not Python loops
(a 1-year 1Hour query touches ~98k minutes per symbol).

### 1.2 Realism requirements (the credibility floor)

- **Trading calendar.** No bars on weekends or US market holidays; intraday bars only
  09:30–16:00 ET; daily bars timestamped midnight ET expressed in UTC (match Alpaca's
  convention — verify against a real response). Use an established calendar library
  (e.g. `exchange-calendars`) or an embedded holiday table — implementation's choice,
  behavior is the requirement. Half-days optional in V1; extended hours explicitly out
  (document as regular-session-only).
- **Per-symbol personality**, stable forever: base price level (log-uniform ~$2–$2000),
  annualized volatility (~12%–90%), slight positive drift bias, base volume — all
  hash-derived. A **curated table for the ~100 most famous tickers** overrides the
  hash so AAPL lands in a plausible AAPL price range at a fixed reference date and
  BRK-A is enormous. Cheap, and it's the first thing a visitor checks.
- **Volatility clustering**: a slow-moving multiplier (low-frequency noise per symbol)
  scales daily volatility, so calm and stormy stretches exist. Overnight gaps small
  and occasional.
- **Volume that behaves**: correlated with the magnitude of the bar's return, U-shaped
  across the trading day (heavy at open/close). Trade count `n` scales with volume.

The bar shape is unchanged from today (Alpaca's): `c, h, l, n, o, t, v, vw`.

## 2. Scenario tickers ("magic tickers")

Reserved symbols with scripted behavior — the headline differentiator (Stripe's magic
card numbers, but for market data). Scripts are overlaid deterministically on the
standard engine and anchored to the **calendar**, not the query window, with this
guarantee: **every scenario ticker exhibits its signature behavior within any 30-day
view** (so demos and casual queries always show the goods).

| Ticker | Behavior |
|---|---|
| `CRASH`  | Sharp ~25% multi-day crash on a fixed monthly schedule, slow grind recovery |
| `MOON`   | Persistent strong uptrend with periodic parabolic blow-off spikes |
| `FLAT`   | Constant price; zero-range bars (`o=h=l=c`), token volume — breaks naive chart scaling |
| `GAPPY`  | Large overnight gaps (±5–15%) most days, quiet intraday |
| `HALTS`  | Recurring intraday halt windows: minute bars *absent* mid-session (Alpaca omits no-trade bars — mimic that) |
| `SPIKEY` | Single-minute fat-finger wicks (~10% instantaneous spikes that immediately revert) |
| `PENNY`  | Sub-dollar price (~$0.30), high relative volatility — flushes float/precision bugs |
| `CHOPPY` | High volatility, zero net drift — mean-reversion torture test |

Rules: exactly this list in V1 (8 is enough; each is a small, fun, self-contained
open-source contribution surface later). Scripted tickers respect the schema — never
return malformed data (that would break the wire-compat promise). A `scenario=` query
param applying these shapes to arbitrary symbols is a **named V1.1 candidate**, not V1.

## 3. API surface

Path scheme (added Aug 2026, supersedes the unversioned paths below where they
appear elsewhere): **`/api/v1/{provider}/<the provider's own path>`** for
provider-mimicry surfaces, `/api/v1/...` for Cuckoo-native endpoints. The path `v1`
versions CuckooTrade's API surface; `generation` versions the data. Alpaca is the
first provider (`/api/v1/alpaca/v2/stocks/bars` — the `v2` is Alpaca's own); future
providers with different wire formats (IBKR, Alpha Vantage, …) mount alongside as
their own routers. `/api` (index) and `/api/health` (probes) stay unversioned.

### 3.1 `GET /api/v1/alpaca/v2/stocks/bars` — finish Alpaca compatibility

The acceptance test (§9) is the definition of done: **`alpaca-py`'s historical data
client, pointed at cuckootrade.com/api/v1/alpaca via `url_override`, works
unmodified.**

Params: `symbols` (required, comma list, cap 50), `timeframe` (Alpaca grammar:
`[N]Min|Hour|Day|Week|Month`, calendar-aligned buckets), `start`, `end` (new),
`limit` (cap 10,000), `page_token` (new — real pagination, opaque cursor encoding
symbol+timestamp), `sort` (`asc`/`desc`), `adjustment` and `feed` (accepted and
ignored — no corporate actions in V1; document this), plus Cuckoo extensions `seed`
and `generation`.

Response: exact Alpaca shape `{"bars": {...}, "next_page_token": ...}`. Errors match
Alpaca's observed shape and status codes (verify against real responses; today's
bare-string 422s don't). **Every error message teaches**: state the valid grammar and
include a working example URL — errors are read at the moment someone (or some agent)
is stuck, and a good error is a retry that succeeds.

**Synthetic marking without breaking SDKs**: real Alpaca clients may reject unknown
body fields, so on wire-compat endpoints the marking rides in **headers**:
`X-Cuckoo-Synthetic: true`, `X-Cuckoo-Generation: <n>`, `X-Cuckoo-Docs: <url>`.
Verify alpaca-py's tolerance before putting anything extra in the body. Cuckoo-native
endpoints carry full metadata in the body.

Should-have if cheap: `GET /api/v1/alpaca/v2/stocks/{symbol}/bars` (single-symbol
variant) and `GET /api/v1/alpaca/v2/stocks/bars/latest` (last completed bar per the
calendar).

### 3.2 `GET /api/v1/stream` — SSE (Cuckoo-native)

Server-Sent Events stream of simulated ticks/bars. Not Alpaca's wire protocol (their
WebSocket protocol is V2); this exists for the live hero chart, demo builders, and
agents — and it's curl-able, which is its own documentation.

- Params: `symbols`, `seed`, and `clock`:
  - `clock=demo` (**default**): always-open synthetic session — the stream is alive at
    11pm on a Sunday. This is what the landing page uses.
  - `clock=real`: follows the NYSE calendar; silent while the market is closed.
- Heartbeat comment every ~15s — **required**, because the ALB idle timeout (default
  60s) will otherwise kill quiet streams (`HALTS` goes silent by design). Also raise
  the ALB idle timeout via the `load-balancer-attributes` ingress annotation.
- Per-IP concurrent connection cap (~5). Event format documented in the docs page.

### 3.3 Hygiene and the agent surface

- **Remove** `/api/hello`, `/world`, `/random`, `/bigrandom`, `/randomlist`,
  `/somethingspecial` from the public surface.
- `GET /api` returns a compact machine-readable index: endpoints, params, one working
  example URL each.
- `GET /api/health` for k8s liveness/readiness probes (currently missing).
- `/llms.txt` (and `/llms-full.txt`) served at the site root: the whole API in one
  markdown fetch — the file a model grabs when someone says "use cuckootrade."
- Standard `RateLimit-Limit` / `RateLimit-Remaining` / `RateLimit-Reset` headers.
- CORS stays wide open, GET-only (it already is) — call it out as a feature.
- **MCP server**: V1 stretch goal / immediate fast-follow. ~200 lines wrapping the
  existing API so Claude/Cursor call it as a native tool; independently a strong
  portfolio line.

## 4. Rate limiting, caching, abuse

- **Keyless, per-IP token bucket**: on the order of 60 req/min sustained with burst
  headroom — generous enough to advertise ("no key, real rate limit, go build").
  In-app middleware (e.g. `slowapi`) with per-pod in-memory buckets is acceptable at
  V1 — with 3 replicas the effective ceiling is ~3×; document it honestly, add shared
  state only if abuse actually materializes. 429 on breach, with the headers above.
- **Determinism makes history immutable — exploit it**: any fully-specified request
  whose `end` is in the past gets `Cache-Control: public, max-age=31536000, immutable`.
  A CDN in front becomes nearly free scaling later; no code changes needed now beyond
  the header.
- Existing size caps stay (50 symbols, 10k bars); add probes and resource
  requests/limits to `k8s/api.yaml` (both currently absent).

## 5. The synthetic disclaimer (non-negotiable)

Synthetic data will make a bad backtest look like a validated strategy. Therefore:

- Marking on every response (headers on wire-compat routes, body + headers on native
  routes) with a docs URL.
- A prominent, plain-language statement in the docs and site footer: *for exercising
  code paths — not for validating trading strategies. A profitable backtest on
  synthetic data means nothing.*
- The brand voice carries this honestly everywhere ("100% real fake market data").

## 6. Frontend: landing page, playground, docs

### 6.1 Structure

The current API-tester page moves to **`/playground`** (polished, keeps the existing
chart). The root becomes a real landing page:

1. **Hero**: full-bleed live chart drawing itself; the exact `curl` that produced it
   overlaid; scenario buttons underneath (`CRASH`, `MOON`, `HALTS`, `GAPPY`) — and
   **clicking `CRASH` crashes the chart on screen**. This is the whole pitch in three
   seconds and the page's shareable moment. Ship it with client-side replay of fetched
   bars first if SSE isn't ready; swap to the real stream within V1.
2. **"Change one line"**: Alpaca base URL → Cuckoo base URL as a diff block.
3. Three value props: **No key · Deterministic · Alpaca-compatible.**
4. **Scenario gallery**: one sparkline card per magic ticker.
5. **Determinism proof**: two charts side by side, same request, visibly identical.
6. Code tabs: `curl` / Python (`alpaca-py` + `requests`) / JS — copy buttons, and
   every snippet must actually run against production (§9).
7. Footer: docs, OpenAPI/Swagger (`/api/docs`), GitHub + star count, `llms.txt`,
   disclaimer. **No pricing, no testimonials, no logo wall.**

### 6.2 Rendering requirement

**Landing content must be present in the served HTML without JavaScript** — the SEO
and LLM-citation goals die quietly otherwise. Recommended shape: static/prerendered
page with the interactive pieces (hero chart, gallery, tabs) mounted as islands.
Mechanism is implementation's choice; the requirement is view-source shows the pitch.

### 6.3 Aesthetic

Dark trading-terminal, executed with restraint; the cuckoo carries the personality.

- Near-black background (not pure `#000`), warm off-white text, **one** phosphor
  accent (amber preferred over green — less "Matrix"), monospace for all data and
  code, a good sans for prose. Generous whitespace despite the density.
- One line-drawn cuckoo mark in the accent color; wry, openly-fake copy. **No CRT
  scanlines, no flicker, no boot sequences** — that's where terminal becomes costume.
- `StockChart.jsx`'s geometry/interaction all survives; its hardcoded light `INK`
  palette gets reworked into theme tokens. Final chart series colors chosen at build
  time with a contrast/CVD-validated dark palette (run the dataviz pass then).

## 7. Distribution & repo hygiene

- **LICENSE: MIT.** Repo description + topics (`market-data`, `mock-api`, `testing`,
  `fastapi`, `kubernetes`, `terraform`, `gitops`, …).
- **README restructure**: product-first — pitch, working `curl`, chart GIF, magic
  tickers table in the first screen. The (excellent) infra runbook moves to
  `docs/infrastructure.md`, linked prominently.
- **Container image on GHCR** (`docker run -p 8000:8000 ghcr.io/...`): serious CI
  users won't depend on a stranger's cluster; self-hosting is a feature. Add a publish
  job to the workflow.
- Repo rename to match the brand: optional, deliberate chore (touches the ArgoCD repo
  URL and workflow self-references; OIDC trust survives — it pins numeric IDs).

## 8. Explicitly out of V1

Billing/keys · order execution & positions (**V2: "fake broker"** — which is why the
price engine must be an internal queryable service, not inline endpoint code) ·
Alpaca-compatible WebSocket streaming · options/crypto/FX · corporate
actions/`adjustment` semantics · extended hours · backtesting features of any kind ·
`scenario=` param on arbitrary symbols (V1.1 candidate).

## 9. Acceptance tests (definition of done)

1. **SDK compat**: `alpaca-py` `StockHistoricalDataClient(url_override=...)` fetches
   and parses bars for real-looking and magic tickers, unmodified, in CI.
2. **Byte stability**: golden-file tests pin exact responses for fixed
   (symbols, window, timeframe, seed, generation); any diff fails CI.
3. **Coherence**: aggregated minute bars reproduce hour/day bars exactly; days
   reproduce weeks/months; V and n additive.
4. **Calendar**: zero bars on weekends/holidays; intraday confined to RTH; daily
   timestamps match Alpaca's convention.
5. **Scenarios**: each magic ticker's signature is detectable in any 30-day window.
6. **Docs honesty**: every published snippet runs verbatim against production.
7. **Marking**: every data response carries the synthetic headers.
8. **Landing HTML**: the pitch is present without JS execution.

## 10. Build order

| Phase | Contents |
|---|---|
| 1. Engine | Calendar, personality, hierarchical generator, scenario tickers — as an internal service module with unit tests (§9.2–9.5). The hard, load-bearing part. |
| 2. API | Full bars compat + pagination + errors, latest-bar, `/api` index, health, rate limiting + headers, synthetic marking, debug-endpoint removal, SDK test (§9.1). |
| 3. Stream | SSE endpoint, heartbeats, ALB idle-timeout annotation, connection caps. |
| 4. Frontend | Landing (static + islands), hero + scenario buttons, gallery, determinism proof, code tabs, `/playground`, dark theme, `llms.txt`. |
| 5. Launch | MIT license, README restructure, repo metadata, GHCR image, k8s probes/resources. MCP server as fast-follow. |

Phases 1–2 are sequential; 3 and 4 can proceed in parallel after 2; 5 is cuttable
into whatever's ready at launch. Each phase merges to `main` only when it's shippable
— merging deploys to production.
