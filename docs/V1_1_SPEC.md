# CuckooTrade V1.1 — Failure Modes

Status: **shipped** (Aug 2026) — built on `add_new_failure_modes`, merged and deployed. Context in
[OVERVIEW.md](OVERVIEW.md); V1 build contract in [V1_SPEC.md](V1_SPEC.md), which this
extends. Everything here is additive — no V1 behavior changes except where §3.4 says so
explicitly.

Origin: launch thread feedback. Three asks, one theme.

| Ask | From | Becomes |
|---|---|---|
| "Stale quotes next?" | u/PriorElephant9 | §1 `STALE` |
| "not yet injecting connection-level failures — no dropped sockets, no malformed frames. I should add that." | us, in-thread | §2 `scenario=` |
| "a split or late dividend rewrites bars you already stored … your reconciliation job has to notice" | u/TelevisionInfamous61 | §3 `as_of` + `SPLITS`/`DIVVY`/`REVISED` |

The theme, and the V1.1 positioning: **the failure modes are the product.** V1 sold
determinism — data that always shows up. The thread wants the opposite half: data that
shows up *wrong*, on demand, reproducibly. Nobody can buy a stale feed or a retroactive
restatement from a real vendor at any price, which is the same argument that made
scenario tickers the headline feature.

Ships as three commits on one branch, in the order below.

---

## 1. `STALE` — the frozen quote

The failure mode named precisely in-thread: *most code paths check "did I get a
response," not "is this response current."* A stale feed is worse than a dead one,
because every liveness check you have says green.

**Behavior.** Recurring intraday windows (hash-derived per session, like
`_halts_windows`) during which the symbol's feed stops updating:

- **Stream**: `t` and `p` freeze and repeat verbatim. **Heartbeats keep flowing** —
  this is the load-bearing detail. The socket is healthy, the ticks are punctual, the
  data is dead. On the demo clock this is twenty seconds out of every minute, on a
  fixed schedule rather than a hashed one, so it is visible on the landing page
  without waiting and testable without hunting for the window.
- **Bars**: bars during the window carry `o=h=l=c=<frozen price>`, `v=0`, `n=0`, with
  timestamps advancing normally. A bar's timestamp *is* its bucket; freezing it would
  be malformed, which V1_SPEC §2 forbids. Zero volume against a flat price is how a
  stuck feed actually lands in stored bars.
- **Window exit**: one catch-up bar absorbs the whole move that happened while the feed
  was frozen. Minute 389 is never stale, so the session always closes on a real print,
  the day's volume is never zero, and the catch-up always lands inside the same
  session.

`STALE` is `HALTS`'s evil twin, and the pairing is worth documenting as such:

| | `HALTS` | `STALE` |
|---|---|---|
| Bars during window | absent | present, `v=0`, unchanged price |
| Stream during window | silent (heartbeats only) | ticking, frozen `t` |
| Naive client sees | "nothing arrived" | "everything's fine" |
| Catches | gap handling, reconnect | freshness checks |

**Quote endpoints are deferred.** A frozen `t` would sit most naturally on
`/v2/stocks/quotes/latest` and `/v2/stocks/snapshots`, but that is a new endpoint
family and a real scope increase. `STALE` expresses itself on the bar surfaces,
`/v2/stocks/bars/latest`, and the stream instead. Revisit if quotes are ever wanted on
their own merits.

**Engine touchpoints.** `Scenario.stale_minutes` alongside the existing
`halted_minutes`, applied in `generator.py` after the minute path is priced. No new RNG
draws, so every existing symbol stays byte-identical (the golden files are the proof).
Two zero-volume guards in `_aggregate`/`_aggregate_days` become reachable for the first
time and are added with it.

## 2. `scenario=` — one parameter, data shapes and transport faults

Bars can be wrong. Sockets can also just die, and V1 has no story for that.

V1_SPEC §2 already reserves a `scenario=` param for applying magic-ticker shapes to
arbitrary symbols. Transport faults fold into **the same parameter** rather than a
separate `chaos=`: one grammar, one parser, one list in the docs, one error message.
Each registered effect declares its kind, `data` or `transport`, and the only place the
distinction surfaces is caching (§2.1).

    GET /api/v1/alpaca/v2/stocks/bars?symbols=AAPL&scenario=crash
    GET /api/v1/alpaca/v2/stocks/bars?symbols=AAPL&scenario=flap:2
    GET /api/v1/stream?symbols=AAPL&scenario=crash,drop:20s

**Faults are a parameter, never a magic ticker.** V1_SPEC §2 states scripted tickers
never return malformed data, because that would break the wire-compat promise — and a
keyless public endpoint that serves garbage to an agent who stumbled onto it is a brand
problem, not a feature. A param appears in the URL that produced the failure, which
makes it self-documenting and impossible to hit by accident.

**Determinism applies to faults too.** `drop:20s` drops at exactly twenty seconds,
every time, for everyone. Reproducible chaos is a genuinely differentiating line — the
chaos-engineering tools people know are random, random failures make flaky tests, and
flaky tests are why nobody runs them in CI. Ours belong in CI.

**Data effects are reserved, not built.** The lowercase magic-ticker names (`crash`,
`moon`, `flat`, `gappy`, `halts`, `stale`, `spikey`, `penny`, `choppy`) are recognised
by the parser and rejected with a message pointing at the ticker itself
(`symbols=CRASH`). Applying a price shape to an arbitrary symbol means threading an
overlay through the engine's cached internals — a separate piece of work, and the one
V1_SPEC §2 actually named as the V1.1 candidate. Holding the names costs nothing and
stops this release burning a namespace we already promised.

**Transport effects** (what shipped)

| Effect | Surface | Behavior | Exercises |
|---|---|---|---|
| `flap:<n>` | HTTP | fail n times, then succeed | **retry/backoff — the one that passes** |
| `status:<code>` | HTTP | that provider's error shape, at that status | error paths |
| `slow:<ms>` | both | delay the response / the next frame | client timeouts |
| `truncate` | both | HTTP: honest `Content-Length`, half a body, then close. Stream: one frame cut mid-JSON with the connection left up, so the client has to resync rather than reconnect — the harder path to get right | partial-body parsing |
| `drop:<s>` | stream | close the socket at T+s, mid-frame, no close event | reconnect logic |
| `garbage:<n>` | stream | n invalid `data:` payloads among the good ones | parse-error handling |
| `silent:<s>` | stream | stop data *and* heartbeats for s | read timeouts |

`flap` is the headline: every other effect asserts your code fails correctly, `flap`
asserts it *recovers*. That's a green test, and green tests get kept.

### 2.1 Rules

- Nothing fires without the parameter. Ever. This is what protects the brand, and it
  gets its own test (§4.6).
- Any request carrying a transport effect responds `Cache-Control: no-store` and never
  `immutable`: a fault lies about a moment, and the immutability rule in `common.py`
  must never cache one. (Data effects, when built, stay as cacheable as their window.)
- `X-Cuckoo-Scenario` echoes the parsed effects, so a confused developer can see in
  their own logs what they asked for.
- Unknown names get the house error treatment: list the valid ones, include a working
  example.

Implementation notes: `flap` is the one thing here that cannot be stateless, since
"fail the first n attempts" means remembering attempts. Its counter is per-pod and
in-memory with the same caveat `ratelimit.py` already carries — across N replicas a
`flap:n` can burn up to n×N failures — disclosed in the docs, the `/api` index and the
error text rather than papered over. It keys on the full query string so two endpoints
under test at once cannot consume each other's budget. HTTP `truncate` was verified end
to end against a real server (`httpx` raises on the short read); watch it if the ALB
ever normalises response bodies.

## 3. `as_of` — restatement, and the second axis of determinism

The sharpest critique in the thread, and it's correct: real feeds restate. A split or a
late dividend rewrites bars you already stored, so "same request, same bytes, forever"
is a *fidelity gap*, not only a feature. Anyone persisting bars to Postgres has a
reconciliation job, and today we give it nothing to catch.

### 3.1 The contract change

Promote the purity contract from

    bar = f(symbol, timestamp, generation, seed)

to

    bar = f(symbol, timestamp, generation, seed, as_of)

`as_of` (Cuckoo extension, RFC-3339, **default = now**) means *answer as the feed would
have answered on this date*.

This strengthens determinism rather than weakening it. For a fixed `as_of`, history is
still immutable forever — golden files pin `as_of` and stay byte-stable, CI stays
reproducible, and the stateless architecture is untouched (an `as_of` is just another
hash input, not a database). But *omit* it, which is the default, and a restating
symbol's past closes genuinely change between today and next month. Both halves are
true at once, which is the honest answer to "the determinism cuts both ways."

**Naming collision — important.** Alpaca already has an `asof` param (symbol-mapping
date), accepted-and-ignored today at [alpaca.py:66](../api/providers/alpaca.py#L66).
Ours is `as_of`, with the underscore, and the two must not be conflated.

### 3.2 Restatement tickers

Announcement schedules are calendar-anchored and deterministic like every other
scenario, so **you can test a restatement without waiting a month** — just move `as_of`
across the announcement date:

    # before the announcement
    curl ".../bars?symbols=SPLITS&start=2026-06-01&end=2026-06-30&as_of=2026-07-10"
    # after — same window, same request, different numbers
    curl ".../bars?symbols=SPLITS&start=2026-06-01&end=2026-06-30&as_of=2026-07-20"

| Ticker | Behavior |
|---|---|
| `SPLITS` | Recurring 2:1 forward split. Prior closes ÷2 and volumes ×2 once the announcement date passes. Loud, obvious, the teaching example. |
| `DIVVY` | A **late** dividend adjustment: the ex-date passes, the adjustment lands ~5 sessions later and retroactively shaves prior closes ~1–2%. Small enough to slip past a naive "did anything move more than 10%" check. This is the one that was actually asked for, and it's the cruel one. |
| `REVISED` | A bad print — one session priced ~8% too high, carried in history until the exchange busts the trade, then quietly corrected. A vendor restatement with no corporate action to explain it. |

Two bounds keep this honest, and both are documented on the site rather than buried:

- **Actions have a six-month horizon.** Anything older counts as already baked into
  history. Without that, monthly actions compound without limit and adjusted prices from
  a decade ago collapse toward zero.
- **This models the restatement, not the ex-date discontinuity.** A split rewrites the
  history in front of it; you will not see the price halve on the ex-date itself. The
  reconciliation case — the thing actually asked for — is fully served either way.

### 3.3 Something to reconcile against

A reconciliation job that detects "the closes changed" but can't say why is just an
alert. Add `GET /api/v1/corporate-actions?symbols=SPLITS&start=…` (Cuckoo-native). Each
action carries `ex_date`, `announce_date`, `process_date`, and `ratio`; the
announce/process split is what makes late adjustments modelable at all. Provider
mirrors (Alpaca `/v1beta1/corporate-actions`, Polygon `/v3/reference/splits` and
`/dividends`) are a follow-up, not part of this branch.

This also makes `adjustment` **real**. It's accepted-and-ignored today
([alpaca.py:62](../api/providers/alpaca.py#L62)) with "no corporate actions in V1" as
the stated reason — that reason expires here. `adjustment=raw` returns as-traded
prices; `adjustment=split|dividend|all` applies whatever was known as of `as_of`.
Closing a documented gap is worth as much as the new feature.

### 3.4 The one V1 behavior that changes

`maybe_cache_forever` ([common.py:161](../api/common.py#L161)) currently marks any
past-ended window `immutable`. That becomes a lie for restating symbols. Rule:

- explicit `as_of` in the past → `immutable`, as before (in fact more defensibly)
- omitted `as_of` on a symbol with restatements → **not** immutable
- everything else → unchanged

## 4. Acceptance tests

Extending V1_SPEC §9. Same standard: these are the definition of done.

1. **Restatement**: one (symbol, window), two `as_of` values straddling an
   announcement ⇒ different bytes; each individually byte-stable in golden files.
2. **Split coherence**: adjusted close × ratio == pre-announcement raw close; volume
   scales inversely; `adjustment=raw` is unchanged across the announcement.
3. **Late-dividend detectability**: `DIVVY`'s restatement is >0 and <3%, and lands
   strictly after its ex-date.
4. **Staleness**: within a stale window `v==0` and price is constant; bars are never
   *missing* (that's `HALTS`); the demo clock freezes while wall-clock time advances;
   the catch-up bar reconciles before the close.
5. **Fault determinism**: `drop:20s` drops within a tight band around 20s, repeatably;
   `flap:2` fails exactly twice; a client with retry passes where one without fails.
6. **Fault containment**: nothing fires without the parameter — assert clean responses
   across the whole surface. This is the one that protects the brand.

## 5. Settled decisions

| Decision | Rationale |
|---|---|
| No quote/snapshot endpoints in V1.1 | Balloons scope for one ticker's benefit; `STALE` reads clearly enough on bars, `bars/latest` and the stream. Revisit only if quotes are wanted for themselves. |
| Transport faults ride `scenario=`, not a separate `chaos=` | One grammar, one parser, one documented list; the `data`/`transport` split is an internal tag that only caching cares about. V1_SPEC §2 already reserved the name. |
| `DIVVY`, not `DIVIDEND` | Matches the existing short, wry ticker names (`GAPPY`, `SPIKEY`). |
| Three commits, one branch (`add_new_failure_modes`), merged together | Each is independently revertible; none is separately shippable to production ahead of the others. |
| `adjustment` defaults to `all`, unlike Alpaca's `raw` | The restatement tickers exist to be seen restating, and no ordinary symbol here has a corporate action, so the deviation is observable only on `SPLITS`/`DIVVY`/`REVISED`. Documented at every surface. |
| Adjustments apply per *day*, before aggregation | Keeps cross-timeframe coherence true by construction — a week straddling an ex-date is built from adjusted dailies rather than patched afterwards. Split ratios stay whole numbers so volume remains exactly additive. Proven by extending the existing coherence test to `SPLITS`/`DIVVY`. |
| Restatement tickers stay out of the landing-page scenario gallery | The gallery is a sparkline showcase and their signature is not visual — it is that the same request answers differently over time. They appear in the docs, `llms.txt`, the README, the playground picker and `/api`. |
