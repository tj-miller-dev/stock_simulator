import { existsSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

import { FOOTER, MASTHEAD } from './partials.js'

const here = import.meta.dirname

// Every guides/*.html is an entry point, discovered rather than listed, so
// adding a guide is adding one file. Keyed by basename; `guide-` prefixed so a
// guide can never collide with the three original entries.
const guides = Object.fromEntries(
  readdirSync(resolve(here, 'guides'))
    .filter((f) => f.endsWith('.html'))
    .map((f) => [`guide-${f.slice(0, -5)}`, resolve(here, 'guides', f)]),
)

// Shared masthead and footer, substituted at build time. Only pages that opt
// in by writing the placeholder are touched -- the three original pages carry
// their chrome inline and pass through untouched. This runs at build time on
// purpose: the served HTML stays complete without JavaScript, which is what
// the SEO and LLM-citation goals depend on (V1_SPEC section 6.2).
function partials() {
  return {
    name: 'cuckoo-partials',
    transformIndexHtml: {
      order: 'pre',
      handler: (html) =>
        html
          .replace('<!--@masthead-->', MASTHEAD)
          .replace('<!--@footer-->', FOOTER),
    },
  }
}

// Extensionless URLs in dev and preview, mirroring the nginx rules that serve
// them in production (see nginx.conf).
//
// Vite's own fallback appends `.html` to an unknown path, which is why /docs
// and /playground happened to work. /guides does not: there is no guides.html,
// the page lives at guides/index.html, so the dev server 404'd on a URL that
// production serves fine. Divergence between dev and prod routing is its own
// bug -- a link is either correct in both or broken in both.
//
// Resolution is from disk rather than a hardcoded list so it stays true as
// guides are added, and mismatched rules cannot drift apart.
export function cleanUrl(pathname) {
  if (pathname === '/' || pathname.includes('.')) return null   // root, or an asset
  const rel = pathname.replace(/\/+$/, '')                      // tolerate a trailing slash
  if (!rel) return null
  for (const candidate of [`${rel}.html`, `${rel}/index.html`]) {
    if (existsSync(resolve(here, `.${candidate}`))) return candidate
  }
  // An unknown guide lands on the guides index rather than the landing page,
  // which is what nginx does with it too.
  return rel.startsWith('/guides/') ? '/guides/index.html' : null
}

function cleanUrls() {
  const middleware = (req, _res, next) => {
    const [pathname, search] = req.url.split('?')
    const target = cleanUrl(pathname)
    if (target) req.url = search ? `${target}?${search}` : target
    next()
  }
  return {
    name: 'cuckoo-clean-urls',
    // Registered directly (not via the returned-function form) so the rewrite
    // lands before Vite's html and static handlers see the request.
    configureServer: (server) => { server.middlewares.use(middleware) },
    configurePreviewServer: (server) => { server.middlewares.use(middleware) },
  }
}

// Multi-page build: the landing page keeps its pitch in static HTML (SEO and
// LLM-citation quotability) with React islands layered on; playground, docs
// and each guide are their own entries. nginx maps the clean URLs to the
// corresponding .html files in production; cleanUrls() does it in dev.
export default defineConfig({
  plugins: [react(), partials(), cleanUrls()],
  build: {
    rollupOptions: {
      input: {
        index: resolve(here, 'index.html'),
        playground: resolve(here, 'playground.html'),
        docs: resolve(here, 'docs.html'),
        ...guides,
      },
    },
  },
})
