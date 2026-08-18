export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

// Public host shown in copy-paste snippets, regardless of where the page runs.
export const PUBLIC_BASE = 'https://cuckootrade.com/api'

export function isoDaysAgo(days) {
  const d = new Date(Date.now() - days * 86400e3)
  return d.toISOString().slice(0, 10)
}

export function barsUrl(params, base = API_BASE) {
  const q = new URLSearchParams(params)
  return `${base}/v2/stocks/bars?${q.toString()}`
}

export async function fetchBars(params, opts = {}) {
  const res = await fetch(barsUrl(params), opts)
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      message = (await res.json()).message ?? message
    } catch { /* not json */ }
    throw new Error(message)
  }
  return res.json()
}

export const SERIES_VARS = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8']

export function seriesColor(i) {
  return `var(${SERIES_VARS[i % SERIES_VARS.length]})`
}

export function formatPrice(v) {
  return `$${v.toFixed(v < 1 ? 4 : 2)}`
}
