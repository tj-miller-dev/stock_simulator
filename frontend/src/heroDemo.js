// The hero demonstration: "What happens if …" and the endpoint that answers.
//
// A question flies in, the command types itself out from the root of the API,
// it holds long enough to read, then the endpoint backspaces away and the next
// question takes its place. Nothing here calls anything -- these are the real
// endpoints, copy-pasteable as shown, but the panel is a display, not a client.
//
// Plain DOM rather than React: the hero is the page's SEO surface, so the
// markup ships in index.html fully rendered on the first scenario and this
// file takes it over. Scripts blocked, or the instant before this runs, and
// the hero still reads as a complete pitch.
import { PUBLIC_BASE } from './lib/api.js'

const ROOT = `${PUBLIC_BASE}/v1/` // the stable part of every command
const CANCEL = Symbol('cancel') // thrown to unwind an interrupted run

const TYPE_MS = 24 // per character, typing forward
const ERASE_MS = 10 // per character, backspacing to the root
const ASK_MS = 300 // question fly-out / fly-in
const HOLD_MS = 3600 // reading time, once the command is complete

// Each scenario: the question, the endpoint that answers it, and what that
// endpoint does. Kept as short as the API allows -- the single-symbol bars
// path over `?symbols=`, and every parameter that is already the default
// left out -- because a hero command has to be read at a glance.
const SCENARIOS = [
  {
    ask: 'you need live feeds on a Saturday?',
    flags: '-N ',
    path: 'stream?symbols=CUCKOO,CRASH',
    note: 'CuckooTrade gives curl-able SSE streams, without need for keys or WebSocket clients — and the synthetic session never closes.',
  },
  {
    ask: 'the market crashes?',
    path: 'alpaca/v2/stocks/CRASH/bars',
    note: 'Test against predictable, pre-determined long-tail events like crashes, run-ups, volatility spikes, and more.',
  },
  {
    ask: 'there\'s a 2:1 split?',
    path: 'alpaca/v2/stocks/SPLITS/bars?start=2026-06-01&as_of=2026-07-13',
    note: 'Get the same data pre- and post-split with modified prices and volumes. Roll as_of date forward or backward to simulate as many splits as desired.',
  },
  {
    ask: 'their feed freezes?',
    path: 'alpaca/v2/stocks/STALE/bars?timeframe=1Min',
    note: '$STALE interrupts the normal data stream with periodic stale data (advancing timestamps, unchanged prices/volumes).',
  },
  {
    ask: 'your provider 503s?',
    path: 'alpaca/v2/stocks/AAPL/bars?scenario=status:503',
    note: 'CuckooTrade curl-able SSE streams let you inject failure modes on-demand, in the error shape of your provider.',
  },
  {
    ask: 'the socket dies?',
    flags: '-N ',
    path: 'stream?symbols=CUCKOO&scenario=drop:20s',
    note: 'Harden your CI testing by setting a planned socket death, mid-frame, with no close event.',
  },
  {
    ask: 'the margin is slim?',
    path: 'alpaca/v2/stocks/PENNY/bars',
    note: '$PENNY trades sub-dollar at four decimal places — where a two-decimal assumption starts quietly losing money.',
  },
  {
    ask: 'the provider restates history?',
    path: 'corporate-actions?symbols=SPLITS,DIVVY',
    note: 'Pre-determined corporate actions you can test against — test scenarios before and after a split or divident, on demand.',
  },
  {
    ask: 'your client switches providers?',
    path: 'polygon/v2/aggs/ticker/MSFT/prev',
    note: 'No-fuss drop-in replacement for Alpaca, Polygon, and Alpha Vantage endpoints.',
  },
]

export function initHeroDemo() {
  const root = document.querySelector('[data-demo]')
  if (!root) return

  const askEl = document.querySelector('[data-ask]')
  const flagEl = root.querySelector('[data-flags]')
  const typedEl = root.querySelector('[data-typed]')
  const caretEl = root.querySelector('[data-caret]')
  const noteEl = root.querySelector('[data-note]')
  const navEl = root.querySelector('[data-nav]')
  const copyEl = root.querySelector('[data-copy]')
  const cmdEl = root.querySelector('.demo-cmd')
  if (!askEl || !typedEl || !noteEl) return

  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

  let index = 0
  let jumpTo = null
  let generation = 0
  let onscreen = true
  let hovering = false

  // Nothing cycles while nobody is watching: a background tab or a
  // scrolled-past hero holds where it is. Hovering pauses too, so a command
  // can be read -- or copied -- without it vanishing mid-word.
  const asleep = () => document.hidden || !onscreen || hovering

  const check = (token) => {
    if (token !== generation) throw CANCEL
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

  // Holds for `ms` of awake time: a hold that starts while the hero is hidden
  // (or hovered) simply does not end until it is being watched again.
  async function wait(ms, token) {
    let until = performance.now() + ms
    for (;;) {
      if (asleep()) {
        await sleep(150)
        until = performance.now() + Math.min(ms, 900)
      } else {
        const left = until - performance.now()
        if (left <= 0) return
        await sleep(Math.min(120, Math.max(16, left)))
      }
      check(token)
    }
  }

  function setCommand(sc) {
    if (flagEl) flagEl.textContent = sc.flags ?? ''
    copyEl?.setAttribute('data-command', `curl ${sc.flags ?? ''}'${ROOT}${sc.path}'`)
  }

  function setNote(text) {
    noteEl.innerHTML = `<span class="arrow" aria-hidden="true">→</span> ${
      text.replace(/&/g, '&amp;').replace(/</g, '&lt;')
    }`
  }

  // A long endpoint outruns the width of the line; the view follows the caret
  // rather than typing off the edge where nobody can see it.
  const follow = () => {
    if (cmdEl) cmdEl.scrollLeft = cmdEl.scrollWidth
  }

  async function type(text, token) {
    if (reduced) {
      typedEl.textContent = text
      follow()
      return
    }
    caretEl?.classList.add('typing')
    for (let i = 1; i <= text.length; i++) {
      typedEl.textContent = text.slice(0, i)
      follow()
      await sleep(TYPE_MS)
      check(token)
    }
    caretEl?.classList.remove('typing')
  }

  async function erase(token) {
    const text = typedEl.textContent
    if (reduced) {
      typedEl.textContent = ''
      return
    }
    caretEl?.classList.add('typing')
    for (let i = text.length; i >= 0; i--) {
      typedEl.textContent = text.slice(0, i)
      follow()
      await sleep(ERASE_MS)
      check(token)
    }
    caretEl?.classList.remove('typing')
  }

  async function askIn(sc, token) {
    askEl.textContent = sc.ask
    askEl.classList.remove('out')
    if (reduced) return
    askEl.classList.add('in')
    await sleep(ASK_MS)
    check(token)
    askEl.classList.remove('in')
  }

  async function askOut(token) {
    if (reduced) return
    askEl.classList.add('out')
    await sleep(ASK_MS)
    check(token)
  }

  function markNav() {
    for (const btn of navEl?.children ?? []) {
      const active = Number(btn.dataset.i) === index
      btn.classList.toggle('active', active)
      btn.setAttribute('aria-selected', String(active))
    }
  }

  async function runOne(sc, token, first) {
    setCommand(sc)
    setNote(sc.note)
    markNav()
    // The first scenario is already on screen, so it flies in from nowhere --
    // but its text still has to be written, or a stale string in the static
    // markup would survive until the carousel wrapped all the way around.
    if (first) askEl.textContent = sc.ask
    else await askIn(sc, token)
    await type(sc.path, token)
    await wait(reduced ? HOLD_MS * 1.6 : HOLD_MS, token)
    await Promise.all([askOut(token), erase(token)])
  }

  async function loop() {
    let first = true
    for (;;) {
      const token = ++generation
      try {
        await runOne(SCENARIOS[index], token, first)
      } catch (err) {
        if (err !== CANCEL) await sleep(400)
        // An interrupted run leaves the line mid-command; reset it flat.
        typedEl.textContent = ''
        askEl.classList.add('out')
      }
      first = false
      index = jumpTo ?? (index + 1) % SCENARIOS.length
      jumpTo = null
    }
  }

  /* ---- wiring ---- */

  if (navEl) {
    for (const [i, sc] of SCENARIOS.entries()) {
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.className = 'demo-pip'
      btn.dataset.i = String(i)
      btn.setAttribute('role', 'tab')
      btn.setAttribute('aria-selected', String(i === 0))
      btn.setAttribute('aria-label', `What happens if ${sc.ask}`)
      btn.title = `What happens if ${sc.ask}`
      btn.addEventListener('click', () => {
        if (i === index) return
        jumpTo = i
        generation++
      })
      navEl.appendChild(btn)
    }
  }

  copyEl?.addEventListener('click', () => {
    navigator.clipboard?.writeText(copyEl.dataset.command ?? '')
    copyEl.classList.add('done')
    copyEl.textContent = 'copied'
    setTimeout(() => {
      copyEl.classList.remove('done')
      copyEl.textContent = 'copy'
    }, 1400)
  })

  // Hover pauses only where hovering means something: on a touch screen
  // pointerenter fires on a tap and its matching leave may never arrive,
  // which would strand the demo mid-scenario.
  if (window.matchMedia?.('(hover: hover)').matches ?? true) {
    root.addEventListener('pointerenter', () => { hovering = true })
    root.addEventListener('pointerleave', () => { hovering = false })
  }
  root.addEventListener('focusin', () => { hovering = true })
  root.addEventListener('focusout', () => { hovering = false })

  if ('IntersectionObserver' in window) {
    new IntersectionObserver(([entry]) => { onscreen = entry.isIntersecting }, { threshold: 0.15 })
      .observe(root)
  }

  loop()
}
