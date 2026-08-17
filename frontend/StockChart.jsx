import { useState } from 'react'

// Validated categorical palette (light mode only -- this app has no dark
// mode). Fixed hue order, never cycled: node validate_palette.js confirms
// CVD/contrast for this exact order. See dataviz skill references/palette.md.
const SERIES_COLORS = [
  '#2a78d6', // blue
  '#eb6834', // orange
  '#1baf7a', // aqua
  '#eda100', // yellow
  '#e87ba4', // magenta
  '#008300', // green
  '#4a3aa7', // violet
  '#e34948', // red
]

const INK = {
  surface: '#fcfcfb',
  primary: '#0b0b0b',
  secondary: '#52514e',
  muted: '#898781',
  gridline: '#e1e0d9',
  axis: '#c3c2b7',
}

const CHART_WIDTH = 760
const CHART_HEIGHT = 360
const MARGIN = { top: 16, right: 72, bottom: 28, left: 72 }
const PLOT_WIDTH = CHART_WIDTH - MARGIN.left - MARGIN.right
const PLOT_HEIGHT = CHART_HEIGHT - MARGIN.top - MARGIN.bottom

function niceTicks(min, max, count) {
  if (min === max) {
    return [min]
  }
  const rawStep = (max - min) / count
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const residual = rawStep / magnitude
  let step = magnitude
  if (residual > 5) step = 10 * magnitude
  else if (residual > 2) step = 5 * magnitude
  else if (residual > 1) step = 2 * magnitude
  const niceMin = Math.floor(min / step) * step
  const niceMax = Math.ceil(max / step) * step
  const ticks = []
  for (let v = niceMin; v <= niceMax + step / 2; v += step) {
    ticks.push(Math.round(v * 1000) / 1000)
  }
  return ticks
}

function formatPrice(value) {
  return `$${value.toFixed(2)}`
}

function formatTickDate(iso, subDay) {
  const text = new Date(iso).toISOString()
  return subDay ? `${text.slice(5, 10)} ${text.slice(11, 16)}` : text.slice(0, 10)
}

function layoutEndLabels(entries, minGap) {
  const sorted = entries.map((entry) => ({ ...entry })).sort((a, b) => a.y - b.y)
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i].y - sorted[i - 1].y < minGap) {
      sorted[i].y = sorted[i - 1].y + minGap
    }
  }
  return sorted
}

function csvEscape(value) {
  const text = String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function barsToCsv(bars) {
  const rows = [['symbol', 't', 'o', 'h', 'l', 'c', 'v', 'n', 'vw'].join(',')]
  for (const [symbol, series] of Object.entries(bars)) {
    for (const bar of series) {
      rows.push(
        [symbol, bar.t, bar.o, bar.h, bar.l, bar.c, bar.v, bar.n, bar.vw]
          .map(csvEscape)
          .join(','),
      )
    }
  }
  return rows.join('\n')
}

function downloadCsv(bars) {
  const blob = new Blob([barsToCsv(bars)], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'stock-bars.csv'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export default function StockChart({ bars }) {
  const [hoveredIndex, setHoveredIndex] = useState(null)

  const requestedSymbols = Object.keys(bars)
  const seriesList = Object.entries(bars)
    .filter(([, series]) => series.length > 0)
    .slice(0, SERIES_COLORS.length)
    .map(([symbol, series], i) => ({ symbol, series, color: SERIES_COLORS[i] }))

  if (seriesList.length === 0) {
    return <p>No bars to chart for the given parameters.</p>
  }

  const pointCount = Math.max(...seriesList.map((s) => s.series.length))
  const xAt = (i) => MARGIN.left + (pointCount <= 1 ? PLOT_WIDTH / 2 : (i / (pointCount - 1)) * PLOT_WIDTH)

  const closes = seriesList.flatMap((s) => s.series.map((bar) => bar.c))
  const rawMin = Math.min(...closes)
  const rawMax = Math.max(...closes)
  const pad = (rawMax - rawMin) * 0.08 || rawMax * 0.05 || 1
  const yMin = rawMin - pad
  const yMax = rawMax + pad
  const yAt = (price) => MARGIN.top + (1 - (price - yMin) / (yMax - yMin)) * PLOT_HEIGHT

  const yTicks = niceTicks(yMin, yMax, 4).filter((t) => t >= yMin && t <= yMax)

  const tickCount = Math.min(5, pointCount)
  const xTickIndices = Array.from({ length: tickCount }, (_, k) =>
    Math.round((k / (tickCount - 1 || 1)) * (pointCount - 1)),
  )
  const longestSeries = seriesList.reduce((a, b) => (a.series.length >= b.series.length ? a : b))
  const subDay =
    longestSeries.series.length > 1 &&
    new Date(longestSeries.series[1].t) - new Date(longestSeries.series[0].t) < 24 * 60 * 60 * 1000

  const showLegend = seriesList.length > 1
  const showEndLabels = seriesList.length <= 4
  const endLabels = showEndLabels
    ? layoutEndLabels(
        seriesList.map((s) => {
          const last = s.series[s.series.length - 1]
          return {
            symbol: s.symbol,
            color: s.color,
            y: yAt(last.c),
            text: formatPrice(last.c),
          }
        }),
        14,
      )
    : []

  function indexFromPointerEvent(e) {
    const svg = e.currentTarget.ownerSVGElement
    const rect = svg.getBoundingClientRect()
    const svgX = ((e.clientX - rect.left) / rect.width) * CHART_WIDTH
    const ratio = Math.min(1, Math.max(0, (svgX - MARGIN.left) / PLOT_WIDTH))
    return Math.round(ratio * (pointCount - 1))
  }

  function handleKeyDown(e) {
    if (e.key === 'ArrowRight') {
      setHoveredIndex((idx) => Math.min(pointCount - 1, (idx ?? -1) + 1))
      e.preventDefault()
    } else if (e.key === 'ArrowLeft') {
      setHoveredIndex((idx) => Math.max(0, (idx ?? 1) - 1))
      e.preventDefault()
    }
  }

  const hoverX = hoveredIndex == null ? null : xAt(hoveredIndex)
  const hoverPercent = hoverX == null ? 0 : Math.min(88, Math.max(12, (hoverX / CHART_WIDTH) * 100))

  return (
    <div>
      {seriesList.length === 1 && (
        <div style={{ fontSize: 13, color: INK.secondary, marginBottom: 4 }}>{seriesList[0].symbol}</div>
      )}
      <div style={{ position: 'relative', width: '100%', maxWidth: `${CHART_WIDTH}px` }}>
        <svg
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          width="100%"
          height="auto"
          role="img"
          aria-label={`Stock price chart for ${seriesList.map((s) => s.symbol).join(', ')}`}
          style={{ background: INK.surface, display: 'block' }}
        >
          {yTicks.map((t) => (
            <g key={t}>
              <line x1={MARGIN.left} x2={CHART_WIDTH - MARGIN.right} y1={yAt(t)} y2={yAt(t)} stroke={INK.gridline} strokeWidth="1" />
              <text x={MARGIN.left - 8} y={yAt(t)} textAnchor="end" dominantBaseline="middle" fontSize="11" fill={INK.muted}>
                {formatPrice(t)}
              </text>
            </g>
          ))}

          <line x1={MARGIN.left} x2={MARGIN.left} y1={MARGIN.top} y2={CHART_HEIGHT - MARGIN.bottom} stroke={INK.axis} strokeWidth="1" />
          <line
            x1={MARGIN.left}
            x2={CHART_WIDTH - MARGIN.right}
            y1={CHART_HEIGHT - MARGIN.bottom}
            y2={CHART_HEIGHT - MARGIN.bottom}
            stroke={INK.axis}
            strokeWidth="1"
          />

          {xTickIndices.map((i) => (
            <text key={i} x={xAt(i)} y={CHART_HEIGHT - MARGIN.bottom + 16} textAnchor="middle" fontSize="11" fill={INK.muted}>
              {longestSeries.series[i] ? formatTickDate(longestSeries.series[i].t, subDay) : ''}
            </text>
          ))}

          {seriesList.length === 1 && (
            <path
              d={`${seriesList[0].series.map((bar, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(bar.c)}`).join(' ')} L${xAt(
                seriesList[0].series.length - 1,
              )},${CHART_HEIGHT - MARGIN.bottom} L${xAt(0)},${CHART_HEIGHT - MARGIN.bottom} Z`}
              fill={seriesList[0].color}
              opacity="0.1"
              stroke="none"
            />
          )}

          {seriesList.map((s) => (
            <path
              key={s.symbol}
              d={s.series.map((bar, i) => `${i === 0 ? 'M' : 'L'}${xAt(i)},${yAt(bar.c)}`).join(' ')}
              fill="none"
              stroke={s.color}
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}

          {seriesList.map((s) => {
            const last = s.series[s.series.length - 1]
            const cx = xAt(s.series.length - 1)
            const cy = yAt(last.c)
            return (
              <g key={s.symbol}>
                <circle cx={cx} cy={cy} r="6" fill={INK.surface} />
                <circle cx={cx} cy={cy} r="4" fill={s.color} />
              </g>
            )
          })}

          {endLabels.map((label) => (
            <text key={label.symbol} x={CHART_WIDTH - MARGIN.right + 8} y={label.y} fontSize="11" dominantBaseline="middle">
              <tspan fill={label.color}>{'— '}</tspan>
              <tspan fill={INK.secondary}>{label.text}</tspan>
            </text>
          ))}

          {hoverX != null && (
            <line x1={hoverX} x2={hoverX} y1={MARGIN.top} y2={CHART_HEIGHT - MARGIN.bottom} stroke={INK.axis} strokeWidth="1" />
          )}

          <rect
            x={MARGIN.left}
            y={MARGIN.top}
            width={PLOT_WIDTH}
            height={PLOT_HEIGHT}
            fill="transparent"
            tabIndex={0}
            onPointerMove={(e) => setHoveredIndex(indexFromPointerEvent(e))}
            onPointerLeave={() => setHoveredIndex(null)}
            onKeyDown={handleKeyDown}
            onFocus={() => setHoveredIndex((idx) => (idx == null ? 0 : idx))}
            onBlur={() => setHoveredIndex(null)}
          />
        </svg>

        {hoveredIndex != null && (
          <div
            style={{
              position: 'absolute',
              top: 8,
              left: `${hoverPercent}%`,
              transform: 'translateX(-50%)',
              background: INK.surface,
              border: `1px solid ${INK.gridline}`,
              borderRadius: 4,
              padding: '6px 8px',
              fontSize: 12,
              pointerEvents: 'none',
              whiteSpace: 'nowrap',
              boxShadow: '0 1px 4px rgba(11,11,11,0.12)',
            }}
          >
            <div style={{ color: INK.primary, fontWeight: 600, marginBottom: 4 }}>
              {longestSeries.series[hoveredIndex] ? formatTickDate(longestSeries.series[hoveredIndex].t, subDay) : ''}
            </div>
            {seriesList.map((s) => {
              const bar = s.series[hoveredIndex]
              if (!bar) return null
              return (
                <div key={s.symbol}>
                  <span style={{ color: s.color }}>{'— '}</span>
                  <strong style={{ color: INK.primary }}>{formatPrice(bar.c)}</strong>{' '}
                  <span style={{ color: INK.secondary }}>{s.symbol}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {showLegend && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8, fontSize: 12, color: INK.secondary }}>
          {seriesList.map((s) => (
            <span key={s.symbol}>
              <span style={{ color: s.color }}>{'— '}</span>
              {s.symbol}
            </span>
          ))}
        </div>
      )}

      {requestedSymbols.length > seriesList.length && (
        <p style={{ fontSize: 12, color: INK.muted }}>
          Showing {seriesList.length} of {requestedSymbols.length} symbols requested (chart caps at {SERIES_COLORS.length}).
        </p>
      )}

      <div style={{ marginTop: 8 }}>
        <button type="button" onClick={() => downloadCsv(bars)}>
          Download CSV
        </button>
      </div>
    </div>
  )
}
