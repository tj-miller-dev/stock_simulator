import { useEffect, useRef, useState } from 'react'
import { formatPrice } from '../lib/api.js'

// Theme-token chart chrome (see theme.css; palette validated per dataviz pass).
const INK = {
  primary: 'var(--ink)',
  secondary: 'var(--ink-2)',
  muted: 'var(--ink-3)',
  gridline: 'var(--grid)',
  axis: 'var(--axis)',
}

// The viewBox is sized to the container's real pixel width rather than a fixed
// 760, so axis labels render at their stated size instead of being scaled down
// with everything else -- a 760-unit box squeezed into a 340px phone shrinks
// 11px type to 5px.
const W_FALLBACK = 760
const H = 340
const NARROW_AT = 560
const MARGIN_WIDE = { top: 16, right: 76, bottom: 28, left: 64 }
// Phones lose the end-of-line price labels, so the right margin collapses.
const MARGIN_NARROW = { top: 12, right: 14, bottom: 24, left: 54 }

/** Tracks the rendered width of a node via a callback ref (the node appears
 *  only after data arrives, so a plain ref + mount effect would miss it). */
function useElementWidth() {
  const [node, setNode] = useState(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    if (!node) return
    const measure = () => setWidth(Math.round(node.getBoundingClientRect().width))
    measure()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    }
    const ro = new ResizeObserver(measure)
    ro.observe(node)
    return () => ro.disconnect()
  }, [node])

  return [setNode, width]
}

function niceTicks(min, max, count) {
  if (min === max) return [min]
  const raw = (max - min) / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const res = raw / mag
  let step = mag
  if (res > 5) step = 10 * mag
  else if (res > 2) step = 5 * mag
  else if (res > 1) step = 2 * mag
  const ticks = []
  for (let v = Math.floor(min / step) * step; v <= max + step / 2; v += step) {
    ticks.push(Math.round(v * 10000) / 10000)
  }
  return ticks
}

function formatTickDate(iso, subDay, terse = false) {
  if (subDay) return terse ? iso.slice(11, 16) : `${iso.slice(5, 10)} ${iso.slice(11, 16)}`
  return terse ? iso.slice(5, 10) : iso.slice(0, 10)
}

function layoutEndLabels(entries, minGap) {
  const sorted = entries.map((e) => ({ ...e })).sort((a, b) => a.y - b.y)
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i].y - sorted[i - 1].y < minGap) sorted[i].y = sorted[i - 1].y + minGap
  }
  return sorted
}

/**
 * series: [{ name, color, bars: [{t, c}] }] -- up to 8 (validated slot order).
 * animate: re-draws the line left-to-right whenever `drawKey` changes.
 */
export default function LineChart({ series, drawKey, height = H }) {
  const [hovered, setHovered] = useState(null)
  const [wrapRef, measured] = useElementWidth()
  const pathRefs = useRef([])

  const drawn = series.filter((s) => s.bars.length > 0).slice(0, 8)

  // Geometry follows the container. Before the first measurement the fallback
  // keeps the markup sane; the observer corrects it on the same frame.
  const W = Math.max(260, measured || W_FALLBACK)
  const narrow = W < NARROW_AT
  const MARGIN = narrow ? MARGIN_NARROW : MARGIN_WIDE
  const PW = W - MARGIN.left - MARGIN.right
  const chartH = narrow ? Math.min(height, Math.max(180, Math.round(height * 0.72))) : height
  const fs = narrow ? 10 : 11

  // Re-draw whenever the data changes -- or the geometry does, since a resize
  // rewrites every path and would otherwise leave a stale dash offset behind.
  useEffect(() => {
    for (const el of pathRefs.current) {
      if (!el) continue
      const len = el.getTotalLength()
      el.style.strokeDasharray = `${len}`
      el.style.strokeDashoffset = `${len}`
      el.style.animation = 'none'
      // Force reflow so the draw animation restarts on data change.
      void el.getBoundingClientRect()
      el.style.animation = 'draw 1.1s ease-out forwards'
    }
    setHovered(null)
  }, [drawKey, W, chartH])

  if (drawn.length === 0) {
    return <div className="chart-empty">no bars for this window</div>
  }

  const pointCount = Math.max(...drawn.map((s) => s.bars.length))
  const xAt = (i) => MARGIN.left + (pointCount <= 1 ? PW / 2 : (i / (pointCount - 1)) * PW)

  const closes = drawn.flatMap((s) => s.bars.map((b) => b.c))
  const rawMin = Math.min(...closes)
  const rawMax = Math.max(...closes)
  const pad = (rawMax - rawMin) * 0.08 || rawMax * 0.05 || 1
  const yMin = rawMin - pad
  const yMax = rawMax + pad
  const ph = chartH - MARGIN.top - MARGIN.bottom
  const yAt = (v) => MARGIN.top + (1 - (v - yMin) / (yMax - yMin)) * ph

  const yTicks = niceTicks(yMin, yMax, narrow ? 3 : 4).filter((t) => t >= yMin && t <= yMax)
  const longest = drawn.reduce((a, b) => (a.bars.length >= b.bars.length ? a : b))
  const subDay =
    longest.bars.length > 1 &&
    new Date(longest.bars[1].t) - new Date(longest.bars[0].t) < 86400e3
  // Fewer, inward-anchored ticks on a phone so no date hangs past the plot.
  const tickCount = Math.min(narrow ? 3 : 5, pointCount)
  const xTickIdx = Array.from({ length: tickCount }, (_, k) =>
    Math.round((k / (tickCount - 1 || 1)) * (pointCount - 1)),
  )

  // No gutter for them on a phone -- the legend and tooltip carry the values.
  const endLabels =
    !narrow && drawn.length <= 4
      ? layoutEndLabels(
          drawn.map((s) => {
            const last = s.bars[s.bars.length - 1]
            return { name: s.name, color: s.color, y: yAt(last.c), text: formatPrice(last.c) }
          }),
          14,
        )
      : []

  function indexFromPointer(e) {
    const svg = e.currentTarget.ownerSVGElement
    const rect = svg.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * W
    const ratio = Math.min(1, Math.max(0, (x - MARGIN.left) / PW))
    return Math.round(ratio * (pointCount - 1))
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowRight') {
      setHovered((i) => Math.min(pointCount - 1, (i ?? -1) + 1))
      e.preventDefault()
    } else if (e.key === 'ArrowLeft') {
      setHovered((i) => Math.max(0, (i ?? 1) - 1))
      e.preventDefault()
    }
  }

  // The tip is centred on the cursor, so it is held clear of both edges by
  // roughly its own half-width -- a bigger share of a narrow chart.
  const tipInset = narrow ? 27 : 14
  const hoverX = hovered == null ? null : xAt(hovered)
  const hoverPct =
    hoverX == null ? 0 : Math.min(100 - tipInset, Math.max(tipInset, (hoverX / W) * 100))

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg
        viewBox={`0 0 ${W} ${chartH}`}
        role="img"
        aria-label={`Price chart: ${drawn.map((s) => s.name).join(', ')}`}
      >
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={MARGIN.left} x2={W - MARGIN.right} y1={yAt(t)} y2={yAt(t)} stroke={INK.gridline} strokeWidth="1" />
            <text x={MARGIN.left - (narrow ? 6 : 8)} y={yAt(t)} textAnchor="end" dominantBaseline="middle" fontSize={fs} fill={INK.muted} fontFamily="var(--mono)">
              {formatPrice(t)}
            </text>
          </g>
        ))}

        <line x1={MARGIN.left} x2={MARGIN.left} y1={MARGIN.top} y2={chartH - MARGIN.bottom} stroke={INK.axis} strokeWidth="1" />
        <line x1={MARGIN.left} x2={W - MARGIN.right} y1={chartH - MARGIN.bottom} y2={chartH - MARGIN.bottom} stroke={INK.axis} strokeWidth="1" />

        {xTickIdx.map((i, k) => (
          <text
            key={i}
            x={xAt(i)}
            y={chartH - MARGIN.bottom + (narrow ? 14 : 16)}
            textAnchor={k === 0 ? 'start' : k === xTickIdx.length - 1 ? 'end' : 'middle'}
            fontSize={fs}
            fill={INK.muted}
            fontFamily="var(--mono)"
          >
            {longest.bars[i] ? formatTickDate(longest.bars[i].t, subDay, narrow) : ''}
          </text>
        ))}

        {drawn.length === 1 && (
          <path
            d={`${drawn[0].bars.map((b, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(b.c)}`).join(' ')} L${xAt(drawn[0].bars.length - 1)},${chartH - MARGIN.bottom} L${xAt(0)},${chartH - MARGIN.bottom} Z`}
            fill={drawn[0].color}
            opacity="0.07"
            stroke="none"
          />
        )}

        {drawn.map((s, si) => (
          <path
            key={s.name}
            ref={(el) => (pathRefs.current[si] = el)}
            className="drawing"
            d={s.bars.map((b, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(b.c)}`).join(' ')}
            fill="none"
            stroke={s.color}
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}

        {endLabels.map((l) => (
          <text key={l.name} x={W - MARGIN.right + 8} y={l.y} fontSize={fs} dominantBaseline="middle" fontFamily="var(--mono)">
            <tspan fill={l.color}>{'— '}</tspan>
            <tspan fill={INK.secondary}>{l.text}</tspan>
          </text>
        ))}

        {hoverX != null && (
          <line x1={hoverX} x2={hoverX} y1={MARGIN.top} y2={chartH - MARGIN.bottom} stroke={INK.axis} strokeWidth="1" />
        )}

        {/* Hit area for pointer and keyboard scrubbing. Touch never fires a
            leave event, so lifting the finger is what clears the tooltip. */}
        <rect
          x={MARGIN.left}
          y={MARGIN.top}
          width={PW}
          height={chartH - MARGIN.top - MARGIN.bottom}
          fill="transparent"
          tabIndex={0}
          onPointerMove={(e) => setHovered(indexFromPointer(e))}
          onPointerLeave={() => setHovered(null)}
          onPointerUp={(e) => e.pointerType !== 'mouse' && setHovered(null)}
          onPointerCancel={() => setHovered(null)}
          onKeyDown={onKeyDown}
          onFocus={() => setHovered((i) => (i == null ? 0 : i))}
          onBlur={() => setHovered(null)}
        />
      </svg>

      {hovered != null && (
        <div className="chart-tip" style={{ left: `${hoverPct}%` }}>
          <div style={{ color: 'var(--ink)', marginBottom: 3 }}>
            {longest.bars[hovered] ? formatTickDate(longest.bars[hovered].t, subDay) : ''}
          </div>
          {drawn.map((s) => {
            const b = s.bars[hovered]
            if (!b) return null
            return (
              <div key={s.name}>
                <span style={{ color: s.color }}>{'— '}</span>
                <b style={{ color: 'var(--ink)', fontWeight: 600 }}>{formatPrice(b.c)}</b>{' '}
                <span style={{ color: 'var(--ink-2)' }}>{s.name}</span>
              </div>
            )
          })}
        </div>
      )}

      {drawn.length > 1 && (
        <div className="chart-legend">
          {drawn.map((s) => (
            <span key={s.name}>
              <span style={{ color: s.color }}>{'— '}</span>
              {s.name}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
