import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import LineChart from './chart/LineChart.jsx'
import Sparkline from './chart/Sparkline.jsx'
import { initHeroDemo } from './heroDemo.js'
import { barsUrl, fetchBars, isoDaysAgo } from './lib/api.js'
import { initNav } from './nav.js'
import { initRibbon } from './ribbon.js'
import './theme.css'

const SCENARIOS = [
  ['CRASH', 'A ~25% crash mid-month with a slow recovery. Repeats every month.'],
  ['MOON', 'Parabolic run-up peaking late in the month, then a hard correction.'],
  ['FLAT', 'Zero-range bars pinned at exactly $100.00 — breaks naive chart autoscaling.'],
  ['GAPPY', 'Overnight gaps of 5–15% most days, with quiet sessions between.'],
  ['HALTS', 'Minute bars go missing during intraday halt windows.'],
  ['STALE', 'The feed freezes mid-session — same price, zero volume, while the clock keeps moving.'],
  ['SPIKEY', 'Single-minute wicks that spike and instantly revert.'],
  ['PENNY', 'Sub-dollar prices with four decimal places — surfaces precision bugs.'],
  ['CHOPPY', 'High volatility with zero net drift — stress-tests mean-reversion logic.'],
]

function Gallery() {
  const [bars, setBars] = useState({})

  useEffect(() => {
    fetchBars({
      symbols: SCENARIOS.map(([t]) => t).join(','),
      timeframe: '1Day',
      start: isoDaysAgo(45),
    }).then((data) => setBars(data.bars)).catch(() => {})
  }, [])

  return (
    <div className="gallery">
      {SCENARIOS.map(([t, desc]) => (
        <div
          key={t}
          className="card"
          role="link"
          tabIndex={0}
          aria-label={`${t}: ${desc}`}
          onClick={() => { window.location.href = `/playground?symbols=${t}` }}
          onKeyDown={(e) => e.key === 'Enter' && (window.location.href = `/playground?symbols=${t}`)}
        >
          <div className="name">${t}</div>
          <div className="desc">{desc}</div>
          <Sparkline bars={bars[t]} />
        </div>
      ))}
    </div>
  )
}

const PROOF_PARAMS = {
  symbols: 'SPY',
  timeframe: '1Day',
  start: '2026-01-05',
  end: '2026-03-31',
  seed: '42',
}

function DeterminismProof() {
  const [runs, setRuns] = useState(null)

  useEffect(() => {
    async function fetchOnce() {
      // no-store so both requests genuinely leave the browser.
      const res = await fetch(barsUrl(PROOF_PARAMS), { cache: 'no-store' })
      const text = await res.text()
      let hash = ''
      if (crypto?.subtle) {
        const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
        hash = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
      }
      return { bars: JSON.parse(text).bars.SPY, hash }
    }
    Promise.all([fetchOnce(), fetchOnce()]).then(setRuns).catch(() => {})
  }, [])

  if (!runs) return <div className="chart-empty">fetching the same request twice…</div>
  const identical = runs[0].hash === runs[1].hash
  return (
    <div className="proof-grid">
      {runs.map((run, i) => (
        <div className="panel" key={i}>
          <div className="panel-title">
            <span className="dot" />
            <span>request #{i + 1} · SPY · seed=42</span>
          </div>
          <LineChart series={[{ name: 'SPY', color: 'var(--s1)', bars: run.bars }]} drawKey={i} height={160} />
          <div className="hash">
            sha256 {run.hash.slice(0, 32)}… {identical && <b>· identical</b>}
          </div>
        </div>
      ))}
    </div>
  )
}

function mount(id, node) {
  const el = document.getElementById(id)
  if (el) createRoot(el).render(<StrictMode>{node}</StrictMode>)
}

initNav()
initRibbon()
initHeroDemo()

mount('island-gallery', <Gallery />)
mount('island-proof', <DeterminismProof />)

// Code tabs + static copy buttons: plain DOM, no React needed.
for (const tabs of document.querySelectorAll('.tabs')) {
  tabs.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-pane]')
    if (!btn) return
    const panes = tabs.parentElement.querySelectorAll('[data-pane-id]')
    for (const b of tabs.querySelectorAll('button')) b.classList.toggle('active', b === btn)
    for (const p of panes) p.classList.toggle('active', p.dataset.paneId === btn.dataset.pane)
  })
}
for (const btn of document.querySelectorAll('button.copy[data-copy-target]')) {
  btn.addEventListener('click', () => {
    const target = document.querySelector(btn.dataset.copyTarget)
    navigator.clipboard?.writeText(target.textContent.replace(/^\$ /, ''))
    btn.classList.add('done')
    btn.textContent = 'copied'
    setTimeout(() => {
      btn.classList.remove('done')
      btn.textContent = 'copy'
    }, 1400)
  })
}
