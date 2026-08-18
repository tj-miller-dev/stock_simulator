// Tiny single-series line for the scenario gallery cards. Identity is carried
// by the card title, so every sparkline wears the brand accent.
//
// Sizing lives in CSS (.sparkline), not in width/height attributes: `auto` is
// not a valid value for the SVG height presentation attribute, so it gets
// dropped and the element falls back to the 100% default -- which inside a
// grid-stretched card resolved to the card's whole content height and hung the
// line out the bottom.
export default function Sparkline({ bars, width = 200, height = 48 }) {
  // Same box whether or not the data arrived, so the card never reflows.
  const box = { aspectRatio: `${width} / ${height}` }
  if (!bars || bars.length < 2) return <div className="sparkline" style={box} />
  const closes = bars.map((b) => b.c)
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min || max * 0.05 || 1
  const x = (i) => (i / (bars.length - 1)) * (width - 4) + 2
  const y = (v) => height - 4 - ((v - min) / span) * (height - 8)
  const d = closes.map((c, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(' ')
  return (
    <svg className="sparkline" style={box} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}
