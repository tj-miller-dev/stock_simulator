// Generates dist/llms-full.txt: llms.txt, then the full prose of the docs page
// and every guide, in one fetch.
//
// Generated rather than hand-written on purpose. A second hand-maintained
// summary of the API is a file that drifts out of sync with the first one, and
// a confidently wrong llms-full.txt is worse for an agent than no file at all.
// This derives from the pages themselves, so it cannot say anything the site
// does not.
//
// Runs after `vite build` (see package.json). The HTML parsing here is
// deliberately naive -- it only ever reads markup from this repo, written by
// the same people maintaining this script.

import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const dist = resolve(root, 'dist')

const ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', mdash: '—',
  ndash: '–', rsquo: '’', lsquo: '‘', ldquo: '“', rdquo: '”',
  hellip: '…', rarr: '→', times: '×', plusmn: '±', copy: '©', middot: '·',
  eacute: 'é',
}

function decode(text) {
  return text
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&(\w+);/g, (m, name) => ENTITIES[name] ?? m)
}

// Pull the article out of a page and flatten it to readable plain text.
// Headings become markdown so the structure survives; <pre> keeps its line
// breaks, because the code samples are the most useful part of the file.
function pageText(html) {
  // Drop <head> outright -- its <title> and meta tags would otherwise arrive
  // as stray prose, and <link ...> is one character away from matching the
  // <li> rule below.
  let body = html
    .replace(/[\s\S]*?<body[^>]*>/, '')
    .replace(/<script[\s\S]*?<\/script>/g, '')
    .replace(/<style[\s\S]*?<\/style>/g, '')
    .replace(/<button[\s\S]*?<\/button>/g, '')   // "copy" affordances, not prose
    .replace(/<header[\s\S]*?<\/header>/g, '')
    .replace(/<footer[\s\S]*?<\/footer>/g, '')
    .replace(/<nav[\s\S]*?<\/nav>/g, '')

  // Protect <pre> blocks from the whitespace collapsing below.
  const blocks = []
  body = body.replace(/<pre[^>]*>([\s\S]*?)<\/pre>/g, (_, code) => {
    const clean = decode(code.replace(/<[^>]+>/g, '')).replace(/^\n+|\s+$/g, '')
    blocks.push(clean.split('\n').map((l) => `    ${l}`).join('\n'))
    // Self-delimiting token: the whitespace pass below trims line ends, so a
    // placeholder that relied on surrounding spaces would not survive it.
    return `[[PRE${blocks.length - 1}]]`
  })

  body = body
    .replace(/<h1[^>]*>/g, '\n\n# ').replace(/<h2[^>]*>/g, '\n\n## ')
    .replace(/<h3[^>]*>/g, '\n\n### ').replace(/<h4[^>]*>/g, '\n\n#### ')
    .replace(/<li\b[^>]*>/g, '\n- ')
    .replace(/<\/(p|li|div|section|tr|figcaption|h[1-6])>/g, '\n')
    .replace(/<\/t[dh]>/g, ' | ')
    .replace(/<[^>]+>/g, '')

  body = decode(body)
    .split('\n')
    .map((line) => line.replace(/[ \t]+/g, ' ').trim())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

  return body.replace(/\[\[PRE(\d+)\]\]/g, (_, i) => `\n${blocks[Number(i)]}\n`)
}

const parts = [readFileSync(resolve(root, 'public/llms.txt'), 'utf8').trim()]

parts.push(
  '\n\n' + '='.repeat(70) + '\n' +
  'FULL DOCUMENTATION\n' +
  'Everything below is the prose of the site pages, inlined so this file is\n' +
  'the whole API in a single fetch. Source: https://cuckootrade.com/\n' +
  '='.repeat(70),
)

const pages = [
  ['https://cuckootrade.com/docs', resolve(dist, 'docs.html')],
  ...readdirSync(resolve(dist, 'guides'))
    .filter((f) => f.endsWith('.html'))
    .sort()
    .map((f) => [
      `https://cuckootrade.com/guides/${f === 'index.html' ? '' : f.slice(0, -5)}`,
      resolve(dist, 'guides', f),
    ]),
]

for (const [url, file] of pages) {
  parts.push(`\n\n${'-'.repeat(70)}\nSOURCE: ${url}\n${'-'.repeat(70)}\n`)
  parts.push(pageText(readFileSync(file, 'utf8')))
}

const out = parts.join('\n') + '\n'
writeFileSync(resolve(dist, 'llms-full.txt'), out, 'utf8')
console.log(
  `llms-full.txt  ${(Buffer.byteLength(out) / 1024).toFixed(1)} kB  ` +
  `(${pages.length} pages inlined)`,
)
