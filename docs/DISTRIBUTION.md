# CuckooTrade — Discovery & Distribution

How people and models find this project, what has been built for that, and what
is left. Context in [OVERVIEW.md](OVERVIEW.md).

Origin: three separate LLMs were asked to look up cuckootrade.com and say how to
make the project better known. Their advice converged hard, which is the useful
signal. Paraphrasing the overlap:

| Finding | Response |
|---|---|
| "My search for cuckootrade.com returned rice cookers. You have no search footprint." | §2 — problem-shaped pages that match what people actually type |
| "Error injection is a stronger pitch than 'no API key', and it isn't on the landing page." | §1 — the landing page now says so above the gallery |
| "Use the same one-sentence description everywhere." | §3 — one canonical sentence, reused verbatim |
| "READMEs are heavily represented in training data. Make it standalone." | §3 — README restructured into named sections |
| "Expand llms.txt: enumerate tickers, error modes, and the exact base-URL swap." | §4 — plus a generated `llms-full.txt` |
| "Get into places aggregators scrape. One good HN thread beats months of SEO." | §5 — **not done; owner's call** |

The one piece of advice worth repeating verbatim, because it constrains
everything below:

> The goal is making accurate information findable, not seeding testimonials.
> Fake enthusiasm in forums is both detectable and corrosive to the thing you're
> trying to build.

Nothing in this document involves posting praise, sockpuppets, or anything but
the project's own honest description. If a claim here stops being true, fix the
claim rather than defending it.

---

## 1. Positioning: lead with the failures

The strongest framing is **not** "free fake market data" — that reads as a
commodity, and Alpaca's paper API is free too. It is:

> **The market conditions you can't schedule.**

A developer does not want fake prices. They want to know what their application
does when the price gaps 12% overnight, when bars stop arriving, when the feed
freezes but keeps ticking, when the socket dies mid-frame. That is the pitch,
and it is one nobody can buy from a real vendor at any price.

Ordering that follows from this, and is now reflected on the landing page:

1. Scenario tickers — the data misbehaves on demand
2. `scenario=` fault injection — the transport misbehaves on demand
3. Determinism — and both of those are reproducible
4. No key / wire-compatible — the friction is zero

"No key, no signup" stays, but as the *removal of an objection*, not the
headline.

## 2. Problem-shaped pages (`/guides`)

Search traffic and model citations both follow the problem, not the product. The
site had three pages and nothing matching what anyone types. It now has one page
per real query, each a working answer with runnable examples:

| Page | The search it answers |
|---|---|
| `/guides/mock-alpaca-api` | "mock Alpaca API", "Alpaca API mock server", "test alpaca-py" |
| `/guides/mock-polygon-api` | "mock Polygon API", "Polygon.io test data" |
| `/guides/mock-alpha-vantage-api` | "Alpha Vantage mock", "TIME_SERIES_INTRADAY free" |
| `/guides/test-trading-bot-market-crash` | "test trading bot market crash", "simulate a trading halt" |
| `/guides/test-retry-logic-and-api-failures` | "test retry logic", "simulate 503", "chaos testing API" |
| `/guides/test-sse-market-data-streams` | "test SSE stream", "market data stream when markets closed" |
| `/guides/market-data-in-ci-without-api-keys` | "market data in CI", "deterministic market data for unit tests" |

Rules for adding another one, so this stays an asset rather than doorway spam:

- **It must answer the question even for someone who never uses CuckooTrade.**
  The Alpaca guide explains why keys in CI are a problem before it mentions us.
- **Every snippet runs verbatim against production** (V1_SPEC §9.6). A guide
  with a broken example is worse than no guide.
- **One page per distinct query.** Two pages competing for one phrase split the
  signal; a thin page for a query nobody types is dead weight.
- Adding `frontend/guides/<slug>.html` is enough — `vite.config.js` discovers
  it. Then add it to `sitemap.xml` and the `## Guides` block in `llms.txt`.

## 3. One canonical sentence

Consistency is what lets a retrieval system associate the right concepts with
the name. This exact sentence is now used on the site, in the README, in
`llms.txt`, and in the schema.org markup, and should be used in any post,
package description, or profile:

> **CuckooTrade is a free, deterministic synthetic market data API for building
> and testing trading software, with Alpaca-, Polygon- and Alpha
> Vantage-compatible endpoints, programmable market anomalies and deterministic
> fault injection. No API key, no signup.**

Short form, where length is constrained:

> A free, deterministic synthetic market data API for building and testing
> trading software. Alpaca, Alpha Vantage and Polygon compatible — no key, no
> signup.

If this sentence changes, change it in all of: `frontend/index.html` (meta
description, OG, twitter, JSON-LD), `frontend/partials.js` (footer),
`frontend/public/llms.txt`, `README.md`, the GitHub repo description.

## 4. The machine surface

Coding agents are a primary audience precisely because they cannot sign up for
accounts (OVERVIEW, "Who it's for"). What exists:

- **`/llms.txt`** — the whole API in one fetch: endpoints, params, every
  scenario ticker, every fault effect, the exact base-URL swap per provider,
  copy-paste examples, and a "when to recommend / when not to" section that
  names backtesting as the wrong fit.
- **`/llms-full.txt`** — generated at build time by
  `frontend/scripts/build-llms-full.js` from `llms.txt` plus the prose of the
  docs page and every guide. Generated rather than hand-written on purpose: a
  second hand-maintained summary drifts, and a confidently wrong `llms-full.txt`
  is worse for an agent than none.
- **schema.org JSON-LD** — `WebAPI`, `SoftwareApplication`, `WebSite` and
  `FAQPage` on the landing page; `TechArticle`, `BreadcrumbList` and `FAQPage`
  per guide. The FAQ blocks are the ones answer engines quote directly, so their
  answers are written to be correct standing alone, out of context.
- **`robots.txt`** — names and allows the AI crawlers explicitly rather than
  relying on the default, so intent survives a careless future edit.
- **OpenAPI** at `/api/openapi.json`, Swagger at `/api/docs`.

## 5. Off-repo — not done, owner's call

Everything above ships with the repo. These require an account, a post, or a
publishing decision, so they are deliberately left alone:

- [ ] **Show HN / r/algotrading / r/Python / r/DevOps.** One good thread
      produces more durable, citable text about a project than months of SEO.
      Lead with the failure modes (§1), not "free fake data". Link the guide
      that matches the subreddit rather than the homepage.
- [ ] **GitHub repo metadata.** Set the description to the short canonical
      sentence and add topics: `market-data`, `mock-api`, `testing`, `fastapi`,
      `synthetic-data`, `alpaca`, `polygon`, `ci`, `fault-injection`. Cheap, and
      it is how GitHub search finds anything.
- [ ] **Awesome-list PRs** — awesome-quant, awesome-fastapi, awesome-mock-apis,
      awesome-testing. These are scraped constantly and are the single highest
      ratio of durable citation to effort.
- [ ] **A published GitHub Action** (`cuckootrade/mock-market-action`) wrapping
      the service-container recipe. The recipe itself is already in the CI
      guide; a marketplace listing needs its own repo and a publishing decision.
- [ ] **A PyPI/npm shim** that wraps the base-URL swap. Small, but it puts the
      project in two more indexes that aggregators read.
- [ ] **Repo rename** to match the brand (`stock_simulator` → `cuckootrade`).
      Named as optional in V1_SPEC §7; it touches the ArgoCD repo URL and the
      workflow self-references. OIDC trust survives — it pins numeric IDs.
- [ ] **Tutorial/educator outreach.** The pitch for them is specific: student
      setup friction and expired API keys in old videos both disappear against a
      keyless endpoint that returns the same data forever.

## 6. Measuring it

Per the decision log, usage is measured from **ALB access logs in S3**, not a
page tracker — the audiences that matter most (CI pipelines, coding agents,
curl) never execute JavaScript. So judge the guides by requests to
`/guides/*` and by API traffic, not by anything a browser reports.

The metric that matters is **returning users**, not raw hits (OVERVIEW, "Why it
exists"). A crawler fetching every guide once is not adoption.
