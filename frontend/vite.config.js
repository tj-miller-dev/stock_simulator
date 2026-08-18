import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const here = import.meta.dirname

// Multi-page build: the landing page keeps its pitch in static HTML (SEO and
// LLM-citation quotability) with React islands layered on; playground and
// docs are their own entries. nginx maps /playground and /docs to the
// corresponding .html files.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        index: resolve(here, 'index.html'),
        playground: resolve(here, 'playground.html'),
        docs: resolve(here, 'docs.html'),
      },
    },
  },
})
