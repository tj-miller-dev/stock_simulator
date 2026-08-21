import { readdirSync } from 'node:fs'
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

// Multi-page build: the landing page keeps its pitch in static HTML (SEO and
// LLM-citation quotability) with React islands layered on; playground, docs
// and each guide are their own entries. nginx maps the clean URLs to the
// corresponding .html files.
export default defineConfig({
  plugins: [react(), partials()],
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
