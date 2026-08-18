import { initRibbon } from './ribbon.js'
import './theme.css'

initRibbon()

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
