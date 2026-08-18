import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import LineChart from './chart/LineChart.jsx'
import { PUBLIC_BASE, barsUrl, fetchBars, isoDaysAgo, seriesColor } from './lib/api.js'
import './theme.css'

const MAGIC = ['CRASH', 'MOON', 'FLAT', 'GAPPY', 'HALTS', 'SPIKEY', 'PENNY', 'CHOPPY']
const TIMEFRAMES = ['1Min', '5Min', '15Min', '30Min', '1Hour', '1Day', '1Week', '1Month']

function csvEscape(value) {
  const text = String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function barsToCsv(bars) {
  const rows = [['symbol', 't', 'o', 'h', 'l', 'c', 'v', 'n', 'vw'].join(',')]
  for (const [symbol, series] of Object.entries(bars)) {
    for (const b of series) {
      rows.push([symbol, b.t, b.o, b.h, b.l, b.c, b.v, b.n, b.vw].map(csvEscape).join(','))
    }
  }
  return rows.join('\n')
}

function downloadCsv(bars) {
  const blob = new Blob([barsToCsv(bars)], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'cuckootrade-bars.csv'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

function App() {
  const initial = new URLSearchParams(window.location.search)
  const [form, setForm] = useState({
    symbols: initial.get('symbols') ?? 'AAPL, SPY',
    timeframe: initial.get('timeframe') ?? '1Day',
    start: initial.get('start') ?? isoDaysAgo(90),
    end: '',
    seed: '',
    limit: '',
  })
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [drawKey, setDrawKey] = useState(0)

  function params(overrides = {}) {
    const merged = { ...form, ...overrides }
    const p = { symbols: merged.symbols.replaceAll(' ', ''), timeframe: merged.timeframe }
    for (const key of ['start', 'end', 'seed', 'limit']) {
      if (String(merged[key]).trim() !== '') p[key] = String(merged[key]).trim()
    }
    return p
  }

  async function run(overrides = {}) {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchBars(params(overrides))
      setResult(data)
      setDrawKey((k) => k + 1)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { run() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const curl = `curl '${barsUrl(params(), PUBLIC_BASE)}'`
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))
  const series = result
    ? Object.entries(result.bars)
        .filter(([, bars]) => bars.length > 0)
        .slice(0, 8)
        .map(([name, bars], i) => ({ name, bars, color: seriesColor(i) }))
    : []

  return (
    <>
      <section style={{ paddingTop: 24 }}>
        <h2>playground</h2>
        <p>
          Query the bars endpoint live. Every well-formed symbol works — famous ones
          have curated personalities, unknown ones get stable hash-derived traits,
          and the <a href="/docs#magic">magic tickers</a> are scripted drama.
        </p>
        <div className="panel">
          <div className="pg-controls">
            <div style={{ gridColumn: 'span 2' }}>
              <label htmlFor="symbols">symbols (comma-separated, ≤50)</label>
              <input id="symbols" value={form.symbols} onChange={set('symbols')} />
            </div>
            <div>
              <label htmlFor="timeframe">timeframe</label>
              <select id="timeframe" value={form.timeframe} onChange={set('timeframe')}>
                {TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="start">start</label>
              <input id="start" value={form.start} onChange={set('start')} placeholder="2026-07-01" />
            </div>
            <div>
              <label htmlFor="end">end (optional)</label>
              <input id="end" value={form.end} onChange={set('end')} placeholder="now" />
            </div>
            <div>
              <label htmlFor="seed">seed (optional)</label>
              <input id="seed" value={form.seed} onChange={set('seed')} placeholder="canonical" />
            </div>
            <div>
              <label htmlFor="limit">limit (≤10000)</label>
              <input id="limit" value={form.limit} onChange={set('limit')} placeholder="1000" />
            </div>
          </div>
          <div className="scenario-row">
            <button type="button" className="go" onClick={() => run()} disabled={loading}>
              {loading ? 'fetching…' : 'fetch bars'}
            </button>
            {MAGIC.map((t) => (
              <button
                key={t}
                type="button"
                className="chip"
                onClick={() => {
                  setForm((f) => ({ ...f, symbols: t }))
                  run({ symbols: t })
                }}
              >
                {t}
              </button>
            ))}
          </div>
          <pre className="curl-line"><span className="prompt">$</span> {curl}</pre>
        </div>
      </section>

      {error && <div className="pg-error" role="alert">error: {error}</div>}

      {series.length > 0 && (
        <section>
          <div className="panel">
            <div className="panel-title">
              <span className="dot live" />
              <span>{series.map((s) => s.name).join(' · ')} · {form.timeframe} · synthetic</span>
            </div>
            <LineChart series={series} drawKey={drawKey} />
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
            <button type="button" className="ghost" onClick={() => downloadCsv(result.bars)}>
              download csv
            </button>
            {result?.next_page_token && (
              <span className="mono" style={{ fontSize: '0.75rem', color: 'var(--ink-3)', alignSelf: 'center' }}>
                truncated at limit — next_page_token in the raw response below
              </span>
            )}
          </div>
        </section>
      )}

      {result && (
        <section>
          <h2>raw response</h2>
          <div className="panel">
            <pre className="pg-json">{JSON.stringify(result, null, 2)}</pre>
          </div>
        </section>
      )}
    </>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
