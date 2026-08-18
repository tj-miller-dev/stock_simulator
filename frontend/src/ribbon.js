// Live ticker ribbon pinned to the bottom of the sticky masthead on every
// page. Plain DOM -- no React -- so the docs page stays framework-free. The
// prices come from the public SSE stream; the ribbon *is* the demo of that
// endpoint, which is why the badge links to its docs.
import { API_BASE, formatPrice } from './lib/api.js'

const SYMBOLS = ['CUCKOO', 'AAPL', 'SPY', 'NVDA', 'TSLA', 'CRASH', 'MOON', 'PENNY', 'CHOPPY']
// Each half of the marquee track repeats the symbol list so the track always
// outspans the viewport; the CSS loop shifts by exactly one half.
const REPEATS = 3

export function initRibbon() {
  const host = document.getElementById('ticker-ribbon')
  if (!host || !('EventSource' in window)) return

  const badge = document.createElement('a')
  badge.className = 'ribbon-badge'
  badge.href = '/docs#stream'
  badge.title =
    'Streaming synthetic ticks over server-sent events — every viewer sees ' +
    'the same price at the same instant. curl -N the stream endpoint to get ' +
    'this exact feed.'
  badge.innerHTML = '<span class="ribbon-dot" aria-hidden="true"></span>live · synthetic'

  const win = document.createElement('div')
  win.className = 'ribbon-window'
  const track = document.createElement('div')
  track.className = 'ribbon-track'
  for (let half = 0; half < 2; half++) {
    const group = document.createElement('div')
    group.className = 'ribbon-group'
    if (half === 1) group.setAttribute('aria-hidden', 'true')
    for (let r = 0; r < REPEATS; r++) {
      for (const s of SYMBOLS) {
        const item = document.createElement('span')
        item.className = 'ribbon-item'
        item.dataset.sym = s
        item.innerHTML = `<span class="sym">${s}</span> <span class="px">—</span>`
        group.appendChild(item)
      }
    }
    track.appendChild(group)
  }
  win.appendChild(track)
  host.append(badge, win)

  const last = {}
  const source = new EventSource(`${API_BASE}/v1/stream?symbols=${SYMBOLS.join(',')}`)
  source.addEventListener('tick', (e) => {
    const { S, p } = JSON.parse(e.data)
    const dir = last[S] == null ? 0 : Math.sign(p - last[S])
    last[S] = p
    for (const el of host.querySelectorAll(`.ribbon-item[data-sym="${S}"] .px`)) {
      el.textContent = formatPrice(p)
      el.classList.toggle('up', dir > 0)
      el.classList.toggle('down', dir < 0)
    }
  })
  source.onerror = () => {}  // EventSource auto-reconnects
}
