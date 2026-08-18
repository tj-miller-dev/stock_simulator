// Mobile masthead menu. Plain DOM -- no React -- because the docs page stays
// framework-free, and all three pages share the same masthead markup.
//
// Progressive enhancement on purpose: the collapsed layout is gated on the
// `data-nav-ready` flag this module sets, so a page whose JS never runs keeps
// an ordinary (wrapping) row of links instead of a menu button that does
// nothing.
const COLLAPSE_AT = '(max-width: 800px)'

const BURGER = `
  <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
    <rect class="bar bar-top" x="3" y="6.2" width="18" height="1.6" rx="0.8" fill="currentColor"/>
    <rect class="bar bar-mid" x="3" y="11.2" width="18" height="1.6" rx="0.8" fill="currentColor"/>
    <rect class="bar bar-bot" x="3" y="16.2" width="18" height="1.6" rx="0.8" fill="currentColor"/>
  </svg>`

export function initNav() {
  const masthead = document.querySelector('.masthead')
  const inner = masthead?.querySelector('.masthead-inner')
  const nav = inner?.querySelector('nav')
  if (!nav) return

  if (!nav.id) nav.id = 'site-nav'

  const toggle = document.createElement('button')
  toggle.type = 'button'
  toggle.className = 'nav-toggle'
  toggle.setAttribute('aria-controls', nav.id)
  toggle.setAttribute('aria-expanded', 'false')
  toggle.setAttribute('aria-label', 'Menu')
  toggle.innerHTML = BURGER
  inner.appendChild(toggle)

  // Only now does the collapsed CSS apply.
  masthead.dataset.navReady = 'true'

  function setOpen(open) {
    masthead.classList.toggle('nav-open', open)
    toggle.setAttribute('aria-expanded', String(open))
    toggle.setAttribute('aria-label', open ? 'Close menu' : 'Menu')
  }
  const isOpen = () => masthead.classList.contains('nav-open')

  toggle.addEventListener('click', () => setOpen(!isOpen()))

  // Navigating away, tapping the page, or Escape all dismiss it.
  nav.addEventListener('click', (e) => { if (e.target.closest('a')) setOpen(false) })
  document.addEventListener('click', (e) => {
    if (isOpen() && !masthead.contains(e.target)) setOpen(false)
  })
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen()) {
      setOpen(false)
      toggle.focus()
    }
  })

  // Rotating to landscape can widen past the breakpoint while the menu is
  // open; drop the open state so the row layout comes back clean.
  const collapsed = window.matchMedia(COLLAPSE_AT)
  collapsed.addEventListener('change', (e) => { if (!e.matches) setOpen(false) })
}
