# CuckooTrade — Project Overview

> **Read this first.** This document exists so that anyone — human or AI — arriving with
> zero context can understand what this project is, why it looks the way it does, and
> what decisions have already been made. The active build plan is in
> [V1_SPEC.md](V1_SPEC.md); the infrastructure runbook is
> [QUICK_START.md](QUICK_START.md).

## What this is

**CuckooTrade** (cuckootrade.com) is a hosted, **no-signup, deterministic, fake market
data API** that is wire-compatible with real providers (Alpaca first). Developers point
their app at cuckootrade.com instead of `data.alpaca.markets` during development, CI,
demos, and tutorials — then switch back to the real provider by changing one base URL.

The one-line pitch: **Stripe test mode, but for market data.**

The name is the brand: a cuckoo lays a convincing mimic egg in another bird's nest.
CuckooTrade is a mimic API sitting in Alpaca's nest. The product is *openly, proudly
fake* — that honesty is both the legal posture and the brand voice.

## Why it exists (two goals, in priority order)

1. **A DevOps portfolio piece.** The owner (TJ / tj-miller-dev) built the
   infrastructure — Terraform, EKS, ArgoCD GitOps, GitHub Actions with OIDC — as a
   resume showcase. That part is **done and considered banked**; product work must not
   destabilize it.
2. **A product people actually use.** Current priority is *impressive to visitors*
   (recruiters, developers landing on the site) slightly ahead of raw adoption, but
   real usage is the long-term goal. Flashy is good. The metric that matters is
   **returning users**, not revenue. There is deliberately **no billing**.

## Who it's for

In order of sharpness of the wedge:

1. **Developers with market-data code in a CI pipeline** — they can't hit a real API
   from CI (keys, rate limits, flakiness, closed markets). Deterministic fake data is
   a one-line fix.
2. **Coding agents (Claude, Cursor, etc.)** — a deliberate discovery channel. Agents
   can't sign up for accounts, so a keyless endpoint is visible to them in a way keyed
   competitors structurally cannot be. This is why keylessness is non-negotiable.
3. **Educators and tutorial authors** — real market data has redistribution licensing
   problems; a no-key endpoint that never changes is what tutorials link to (which is
   also the SEO/backlink engine).
4. **Frontend/demo builders** — realistic charts that are alive at 11pm on a Sunday,
   with no key and no licensing baggage.

## What differentiates it

The combination — no single leg is unique, the set is:

- **Hosted + no API key + no signup** (IP rate-limited)
- **Deterministic**: same request returns the same bytes, forever (versioned guarantee)
- **Wire-compatible**: real provider clients work unmodified with only a base-URL
  override — Alpaca (`/api/v1/alpaca`), Alpha Vantage (`/api/v1/alphavantage`), and
  Polygon (`/api/v1/polygon`), all serving the same deterministic world
- **Scenario control**: reserved "magic tickers" (`CRASH`, `MOON`, `HALTS`, …) produce
  scripted market behavior on demand — a flash crash in staging, today. No real-data
  provider can offer this at any price. This is the headline feature.
- **Realistic enough**: trading calendar, per-symbol personality, volume that behaves.
  (But see the backtest warning below.)

## What it is explicitly NOT

- **Not a source of real market data**, and never pretends to be. Every response is
  marked synthetic.
- **Not a backtesting/strategy-validation tool.** Synthetic data will make a bad
  strategy look validated; this is the one way the product can genuinely hurt someone.
  The docs and payloads say loudly: *for exercising code paths, not validating
  strategies.*
- **Not a broker/paper-trading engine (yet)** — a "fake broker" (orders, fills,
  positions against the synthetic feed) is the named V2, deliberately out of V1.
- **Not monetized.** No keys, no billing, no pricing page in V1.

## Current state (as of Aug 2026)

- **Infrastructure: complete.** EKS cluster, ArgoCD GitOps, Terraform modules, GitHub
  Actions CI with OIDC (no stored keys), ALB ingress with ACM TLS, hardened (pinned
  action SHAs, CIDR-restricted control plane). See the
  [infrastructure runbook](QUICK_START.md).
- **V1 is built** (branch `first_features`, Aug 2026). `api/engine/` is the
  deterministic hierarchical generator (calendar, personalities, magic tickers,
  golden-file byte-stability tests); `api/providers/` holds the provider-mimicry
  surfaces (`alpaca.py`, `alphavantage.py`, `polygon.py` — one module per provider,
  wire shapes verified against live captures) with pagination, teaching errors in
  each provider's own error style, keyless rate limiting, and synthetic-marking
  headers; `api/common.py` is the shared request plumbing; `api/stream.py` is the
  SSE stream; the frontend is a three-page MPA (landing with islands, /playground,
  /docs) in the terminal-dark theme. The acceptance test suite (`api/tests/`)
  includes alpaca-py pointed at the server via `url_override` and a cross-provider
  consistency test (same symbol+day ⇒ identical OHLCV through every surface).
- **Repo: public** (github.com/tj-miller-dev/stock_simulator), MIT licensed, history
  verified clean of secrets. The API image also publishes to GHCR for self-hosters.

## Decision log (settled — don't relitigate without new information)

| Decision | Rationale |
|---|---|
| Keyless by default; optional keyed tier only later, if ever | Zero friction is the wedge; agents can't sign up; tutorial authors need no-signup. Rejected mandatory-key funnel. |
| Deterministic, stateless engine: every bar is a pure function of (symbol, timestamp, generation, optional seed) | Globally consistent world with no database; infinitely cacheable; reproducible CI fixtures; clean architecture story. |
| Magic tickers over a `scenario=` query param (V1) | More Stripe-like, more memorable/shareable. Param may come later. |
| Provider-namespaced, versioned paths: `/api/v1/{provider}/…` (Aug 2026) | Future providers (IBKR, Alpha Vantage, …) have incompatible wire formats, so each gets its own drop-in surface under its name; the segment before the provider is ours, everything after mimics them. Path `v1` versions the API surface; `generation` versions the data. Native endpoints (e.g. `/api/v1/stream`) sit directly under the version; `/api` and `/api/health` stay unversioned. |
| SSE streaming in V1 (Alpaca-compatible WebSocket in V2) | Serves the live hero chart and the agent/demo audiences at once; SSE is trivial in FastAPI and curl-able. Note the ALB 60s idle timeout → heartbeats required. |
| Dark trading-terminal aesthetic, restrained, with the cuckoo identity in voice + one mark | Domain-native and flashy (owner preference); differentiation carried by brand voice and the interactive hero, not the palette. No CRT/scanline costume. |
| Landing page content must be static/prerendered HTML | SEO + LLM-citation quotability; a client-rendered SPA is invisible to both. Interactive parts mount as islands. |
| Open source, MIT license, one repo | Adoption is the currency; CI users need readable source; the infra being public *is* the portfolio. Self-hosting is a feature, not lost revenue. |
| `"synthetic": true` marking + backtest disclaimer everywhere | Ethical load-bearing wall (see "backtest trap" above). |
| V2 named but frozen: fake broker | Design the V1 price engine as an internal queryable service so V2 can consume it, but build no order/position state now. |

## How this repo works (critical for anyone making changes)

- **Pushing to `main` deploys to production.** CI builds the touched service's image,
  pushes to ECR tagged with the commit SHA, commits the tag bump into `k8s/`, and
  ArgoCD syncs it to the cluster within ~3 minutes. **Work on feature branches; merge
  to `main` only to ship.**
- The ALB forwards paths unmodified (no rewrite), which is why the API mounts
  everything under `/api` and the FastAPI docs URLs are manually prefixed.
- The owner launches/runs things themselves — don't start servers or trigger deploys
  as an unrequested "verification" step.
- Repo layout: `api/` (FastAPI), `frontend/` (React/Vite + nginx), `k8s/` (manifests
  ArgoCD syncs), `terraform/` (all infra), `argocd/` (one-time bootstrap app),
  `.github/workflows/` (build-and-deploy). Full operational detail, including the
  teardown-order trap with ArgoCD self-heal, is in the [infrastructure runbook](QUICK_START.md).

## Where to go next

- Building or reviewing product work → [V1_SPEC.md](V1_SPEC.md)
- Operating, rebuilding, or tearing down infrastructure → [infrastructure runbook](QUICK_START.md)
