import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

function App() {
  const [result, setResult] = useState('Result will appear here')

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
      </div>
      <div id="result">{result}</div>
    </div>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
