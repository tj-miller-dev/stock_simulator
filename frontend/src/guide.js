// Guide pages. Same masthead behavior as the docs page, plus copy buttons on
// the code blocks -- nothing else. A guide's content is entirely in its HTML;
// if this module never loads, the page still reads correctly.
import { initNav } from './nav.js'
import { initRibbon } from './ribbon.js'
import './theme.css'
import './guide.css'

initNav()
initRibbon()

// Copy the sibling <pre> of whichever caption holds the button, so a guide
// adds a copy button by adding the button -- no ids to keep in sync.
for (const btn of document.querySelectorAll('.snip button.copy')) {
  btn.addEventListener('click', () => {
    const pre = btn.closest('.snip')?.querySelector('pre')
    if (!pre) return
    navigator.clipboard?.writeText(pre.textContent.trim())
    btn.classList.add('done')
    btn.textContent = 'copied'
    setTimeout(() => {
      btn.classList.remove('done')
      btn.textContent = 'copy'
    }, 1400)
  })
}
