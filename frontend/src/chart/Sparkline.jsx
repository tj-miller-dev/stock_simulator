// Tiny single-series line for the scenario gallery cards. Identity is carried
// by the card title, so every sparkline wears the brand accent.
export default function Sparkline({ bars, width = 200, height = 48 }) {
  if (!bars || bars.length < 2) return <div style={{ height }} />
  const closes = bars.map((b) => b.c)
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min || max * 0.05 || 1
  const x = (i) => (i / (bars.length - 1)) * (width - 4) + 2
  const y = (v) => height - 4 - ((v - min) / span) * (height - 8)
  const d = closes.map((c, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height="auto" aria-hidden="true" style={{ display: 'block' }}>
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}
