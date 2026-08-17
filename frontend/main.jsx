import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import StockChart from './StockChart.jsx'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

function App() {
  const [result, setResult] = useState('Result will appear here')
  const [seed, setSeed] = useState('')
  const [symbol, setSymbol] = useState('SPY')
  const [timeframe, setTimeframe] = useState('1Day')
  const [start, setStart] = useState('')

  async function callEndpoint(path) {
    setResult('Loading...')
    try {
      const res = await fetch(`${API_BASE}/${path}`)
      if (!res.ok) {
        throw new Error(`Status ${res.status}`)
      }
      const data = await res.json()
      setResult(data)
    } catch (err) {
      setResult(`Error: ${err.message}`)
    }
  }

  function callRandomList() {
    const trimmed = seed.trim()
    const query = trimmed === '' ? '' : `?seed=${encodeURIComponent(trimmed)}`
    callEndpoint(`randomlist${query}`)
  }

  function callStockBars() {
    const params = new URLSearchParams({ symbols: symbol, timeframe })
    if (start.trim() !== '') {
      params.set('start', start.trim())
    }
    if (seed.trim() !== '') {
      params.set('seed', seed.trim())
    }
    callEndpoint(`v2/stocks/bars?${params.toString()}`)
  }

  const chartBars = result && typeof result === 'object' && result.bars ? result.bars : null

  return (
    <div>
      <h1>API Tester</h1>
      <div>
        <button type="button" onClick={() => callEndpoint('hello')}>
          Hello
        </button>
        <button type="button" onClick={() => callEndpoint('world')}>
          World
        </button>
        <button type="button" onClick={() => callEndpoint('random')}>
          Random
        </button>
        <button type="button" onClick={() => callEndpoint('bigrandom')}>
          Big Random
        </button>
        <button type="button" onClick={() => callEndpoint('bigrandom')}>
          Also Big Random
        </button>
        <button type="button" onClick={() => callEndpoint('somethingspecial')}>
          Special Button
        </button>
      </div>
      <div>
        <input
          type="number"
          value={seed}
          onChange={(e) => setSeed(e.target.value)}
          placeholder="Seed (optional)"
        />
        <button type="button" onClick={callRandomList}>
          Random List
        </button>
      </div>
      <div>
        <input
          type="text"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Symbol (e.g. SPY)"
        />
        <input
          type="text"
          value={timeframe}
          onChange={(e) => setTimeframe(e.target.value)}
          placeholder="Timeframe (e.g. 1Day)"
        />
        <input
          type="text"
          value={start}
          onChange={(e) => setStart(e.target.value)}
          placeholder="Start date (optional, e.g. 2026-07-18)"
        />
        <button type="button" onClick={callStockBars}>
          Stock Bars
        </button>
      </div>
      {chartBars && <StockChart bars={chartBars} />}
      <pre id="result">{typeof result === 'string' ? result : JSON.stringify(result, null, 2)}</pre>
    </div>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
