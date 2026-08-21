// Shared page chrome, injected at build time by the `partials` plugin in
// vite.config.js wherever a page writes <!--@masthead--> or <!--@footer-->.
//
// Every page uses this -- it is the only copy. It briefly was not: the guides
// got the partial while index, playground and docs kept their chrome inline,
// on the reasoning that rewriting correct pages would be churn. Two copies
// then drifted within the hour (the guides silently lost the "API Docs" nav
// link and the "API index" footer link), which is the entire argument for one
// source of truth, made faster than expected.
//
// Injection happens at build time, so the served HTML is still complete static
// markup with no JavaScript involved -- which is the whole reason the landing
// page is written the way it is (V1_SPEC section 6.2).
//
// The cuckoo mark now rides along on every page rather than the landing page
// alone; V1_SPEC section 6.3 wants one line-drawn mark carrying the identity,
// and having it on exactly one page was the odd case. On the landing page the
// wordmark becomes a self-link, which is ordinary and harmless.

export const MASTHEAD = `
    <header class="masthead">
      <div class="masthead-inner">
        <span class="wordmark" aria-label="CuckooTrade">
          <a href="/">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <!-- minimal line-drawn cuckoo -->
              <path d="M4 16c2-6 7-9 12-8l4-2-2 4c1 5-2 9-8 9l-4 3 1-4c-2-1-3-1-3-2z"
                stroke="var(--accent)" stroke-width="1.5" stroke-linejoin="round"/>
              <circle cx="14.5" cy="9.5" r="0.9" fill="var(--accent)"/>
            </svg>
            CUCKOO<b>TRADE</b>
          </a>
        </span>
        <nav>
          <a href="/guides">Guides</a>
          <a href="/playground">Playground</a>
          <a href="/docs">Docs</a>
          <a href="/api/docs">API Docs</a>
          <a href="https://github.com/tj-miller-dev/stock_simulator">GitHub</a>
          <a class="btn btn-primary btn-sm" href="/docs#quickstart">Get started</a>
        </nav>
      </div>
      <div id="ticker-ribbon" class="ribbon" aria-label="Live synthetic prices"></div>
    </header>`

export const FOOTER = `
    <footer>
      <div class="foot-inner">
        <div class="foot-grid">
          <div class="foot-brand">
            <span class="wordmark">CUCKOO<b>TRADE</b></span>
            <p>A free, deterministic synthetic market data API for building and
            testing trading software. Alpaca, Alpha Vantage, and Polygon
            compatible &mdash; no key, no signup.</p>
          </div>
          <div class="foot-col">
            <h4>Product</h4>
            <a href="/guides">Guides</a>
            <a href="/playground">Playground</a>
            <a href="/docs">Documentation</a>
            <a href="/api">API index</a>
            <a href="/api/docs">OpenAPI docs</a>
          </div>
          <div class="foot-col">
            <h4>Developers</h4>
            <a href="https://github.com/tj-miller-dev/stock_simulator">GitHub</a>
            <a href="/api/openapi.json">openapi.json</a>
            <a href="/llms.txt">llms.txt</a>
            <a href="/llms-full.txt">llms-full.txt</a>
            <a href="/docs#self-host">Self-hosting</a>
          </div>
          <div class="foot-col">
            <h4>Trust</h4>
            <a href="/docs#disclaimer">Synthetic-data disclosure</a>
            <a href="/docs#limits">Rate limits</a>
            <a href="https://github.com/tj-miller-dev/stock_simulator/blob/main/LICENSE">MIT license</a>
          </div>
        </div>
        <div class="fine">
          <p>&copy; 2026 CuckooTrade. Open source &mdash; the API, the frontend, and the infrastructure that runs them.</p>
          <p>All data on this site is synthetic. Not market data, not investment advice.</p>
        </div>
      </div>
    </footer>`
