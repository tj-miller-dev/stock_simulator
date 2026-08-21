// Shared page chrome, injected at build time by the `partials` plugin in
// vite.config.js wherever a page writes <!--@masthead--> or <!--@footer-->.
//
// The three original pages (index, playground, docs) still carry their chrome
// inline: they predate this, they are three files rather than a growing set,
// and rewriting them to use the plugin would be churn on pages that are
// already correct. The guides opt in because there are many of them and they
// keep arriving. Injection happens at build time, so the served HTML is still
// complete static markup with no JavaScript involved -- which is the whole
// reason the landing page is written the way it is (V1_SPEC section 6.2).

export const MASTHEAD = `
    <header class="masthead">
      <div class="masthead-inner">
        <span class="wordmark"><a href="/" style="color:inherit">CUCKOO<b>TRADE</b></a></span>
        <nav>
          <a href="/guides">Guides</a>
          <a href="/playground">Playground</a>
          <a href="/docs">Docs</a>
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
