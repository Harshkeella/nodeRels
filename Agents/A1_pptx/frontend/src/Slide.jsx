/* A slide, drawn from the canonical element list -- the same list ppt.py renders into
   PowerPoint. There is no per-kind branching here any more, in either renderer: both are
   a loop over elements, so the editor cannot show you something the .pptx will not.

   A 13.333in slide is 100cqw, so 1cqw = 9.6pt and every inch and point maps across.
   Percentages position against the slide box; only type size needs cqw (container-type
   is inline-size, so cqh would not resolve).

   This component is pure: it draws, it never handles a pointer. Canvas.jsx puts the
   interaction on top so that a thumbnail, the viewer, present mode and the editor are
   all guaranteed to be looking at exactly the same picture. */

import { memo, useEffect, useState } from 'react'

export const DOC = { w: 13.333, h: 7.5 }
export const setGeometry = g => { if (g?.w && g?.h) Object.assign(DOC, { w: g.w, h: g.h }) }

const hex = v => '#' + String(v).replace(/^#/, '')
const TOKENS = ['primary', 'accent', 'text', 'muted', 'bg']

/** A colour is a theme token or a literal hex -- the same rule deck.color() applies, so
 *  retheming moves both renderers together and nothing else can reach the DOM. */
export function paint(value, t, fallback = 'text') {
  const v = value || fallback
  if (TOKENS.includes(v)) return hex(t?.[v] || '000000')
  if (/^#?[0-9a-fA-F]{6}$/.test(v)) return hex(v)
  return hex(t?.[fallback] || '000000')
}

export const fontOf = (style, t) =>
  `'${style?.family || (style?.font === 'display' ? t?.display_font : t?.body_font) || 'Segoe UI'}',system-ui,sans-serif`

// inches -> % of the slide box; points -> cqw (1cqw is one hundredth of the slide width)
const pctX = i => (i / DOC.w) * 100 + '%'
const pctY = i => (i / DOC.h) * 100 + '%'
export const pt = p => (p / (DOC.w * 0.72)).toFixed(4) + 'cqw'
const inch = i => (i / DOC.w) * 100 + 'cqw'

const JUSTIFY = { top: 'flex-start', middle: 'center', bottom: 'flex-end' }

/* ---------- one element per type ---------- */

function TextEl({ el, t }) {
  const s = el.style
  const lines = String(el.content.text ?? '').split('\n')
  return (
    <div className="el-text" style={{
      justifyContent: JUSTIFY[s.valign] || 'flex-start',
      textAlign: s.align === 'justify' ? 'justify' : s.align || 'left',
      fontFamily: fontOf(s, t),
      fontSize: pt(s.size ?? 18),
      lineHeight: s.lineHeight ?? 1.2,
      fontWeight: s.bold ? 700 : 400,
      fontStyle: s.italic ? 'italic' : undefined,
      textDecoration: s.underline ? 'underline' : undefined,
      letterSpacing: s.letterSpacing ? pt(s.letterSpacing) : undefined,
      color: paint(s.color, t),
    }}>
      {lines.map((line, i) => (
        // The bullet marker is part of the run text in the .pptx too, so the two
        // renderers wrap at the same place instead of one of them being a hair wider.
        <div key={i} style={{ marginBottom: s.spaceAfter ? pt(s.spaceAfter) : undefined }}>
          {line.trim() ? (s.numbered ? `${i + 1}.  ` : s.bullets ? '•  ' : '') + line : '​'}
        </div>
      ))}
    </div>
  )
}

/* Three states, and the user is never left to guess which one they are looking at: a
   placeholder while it loads, the picture, or a box that says the picture is unavailable
   and offers to fetch it again. This used to fade a failed image to opacity 0, which
   leaves a hole on the slide that reads as a design decision rather than a fault. */
function ImageEl({ el }) {
  const { url, alt } = el.content
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState('loading')
  useEffect(() => { setState('loading') }, [url, attempt])

  if (!url) return <div className="el-missing">No image yet</div>
  return (
    <>
      <img key={attempt} src={attempt ? `${url}${url.includes('?') ? '&' : '?'}retry=${attempt}` : url}
           alt={alt || ''} loading="lazy" draggable={false} hidden={state !== 'ready'}
           style={{ objectFit: el.style.fit === 'contain' ? 'contain' : 'cover' }}
           onLoad={() => setState('ready')} onError={() => setState('failed')} />
      {state === 'loading' && <div className="el-loading" aria-label="Loading image" />}
      {state === 'failed' && (
        <div className="el-missing">
          <span>Image unavailable</span>
          {/* Local state only -- retrying a picture is not an edit to the document. */}
          <button onPointerDown={e => e.stopPropagation()}
                  onClick={e => { e.stopPropagation(); setAttempt(a => a + 1) }}>Retry</button>
        </div>
      )}
    </>
  )
}

// clip-path is the browser's version of the autoshape geometry python-pptx emits.
const CLIP = {
  triangle: 'polygon(50% 0, 100% 100%, 0 100%)',
  arrow: 'polygon(0 30%, 60% 30%, 60% 0, 100% 50%, 60% 100%, 60% 70%, 0 70%)',
}

function ShapeEl({ el, t }) {
  const s = el.style, kind = el.content.shape || 'rect'
  if (kind === 'line' || el.type === 'line') {
    // A connector from corner to corner of the box, exactly as add_connector places it.
    return (
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ overflow: 'visible' }}>
        <line x1="0" y1="0" x2="100" y2="100" stroke={paint(s.stroke || s.fill, t, 'primary')}
              strokeWidth={Math.max(0.75, (s.strokeWidth || 0.02) * 72)}
              vectorEffect="non-scaling-stroke" strokeLinecap="round" />
      </svg>
    )
  }
  return (
    <div className="el-shape" style={{
      background: s.fill ? paint(s.fill, t, 'accent') : 'transparent',
      border: s.strokeWidth ? `${pt(s.strokeWidth * 72)} solid ${paint(s.stroke, t, 'primary')}` : undefined,
      borderRadius: kind === 'ellipse' ? '50%' : s.radius ? inch(s.radius) : undefined,
      clipPath: CLIP[kind],
    }} />
  )
}

function TableEl({ el, t }) {
  const { rows = [], header = true } = el.content, s = el.style
  return (
    <table className="el-table" style={{
      fontFamily: fontOf(s, t), fontSize: pt(s.size ?? 14),
      color: paint(s.color, t), textAlign: s.align || 'left',
    }}>
      <tbody>
        {rows.map((row, r) => (
          <tr key={r}>
            {row.map((cell, c) => {
              const head = r === 0 && header
              return (
                <td key={c} style={{
                  fontWeight: head || s.bold ? 700 : 400,
                  color: head ? paint('primary', t) : undefined,
                  borderColor: paint('muted', t) + '55',
                }}>{cell}</td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* Charts are drawn, not imported. A chart library is ~150KB to reproduce six shapes,
   and the export is a native PowerPoint chart whose look this has to match rather than
   improve on -- so plain SVG, with the same series palette ppt.py uses. */
const SERIES = ['accent', 'primary', 'muted', 'text', 'accent']

function ChartEl({ el, t }) {
  const c = el.content, s = el.style
  const kind = c.chart || 'bar'
  const series = c.series || [], cats = c.categories || []
  const col = i => paint(SERIES[i % SERIES.length], t, 'accent')
  const size = (s.size ?? 12) / (DOC.w * 0.72) * 100          // pt -> viewBox units
  const legend = c.legend !== false && series.length > 0
  const W = 100, H = 100, pad = { l: 9, r: 3, t: 4, b: legend ? 12 : 8 }
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b
  const flat = series.flatMap(x => x.values || [])
  const max = Math.max(0, ...flat), min = Math.min(0, ...flat)
  const span = max - min || 1
  const yOf = v => pad.t + ih - ((v - min) / span) * ih
  const xOf = i => pad.l + (cats.length > 1 ? (i / (cats.length - 1)) * iw : iw / 2)
  const label = { fontSize: size * 0.72, fill: paint('muted', t), fontFamily: fontOf(s, t) }

  if (kind === 'pie' || kind === 'donut') {
    const vals = (series[0]?.values || []).map(v => Math.abs(v))
    const sum = vals.reduce((a, b) => a + b, 0) || 1
    const R = 34, r0 = kind === 'donut' ? 18 : 0
    let a0 = -Math.PI / 2
    return (
      <svg viewBox="0 0 100 100" className="el-chart">
        {vals.map((v, i) => {
          const a1 = a0 + (v / sum) * Math.PI * 2
          const P = (ang, rad) => `${50 + rad * Math.cos(ang)} ${50 + rad * Math.sin(ang)}`
          const big = a1 - a0 > Math.PI ? 1 : 0
          const d = vals.length === 1
            ? `M ${50 - R} 50 a ${R} ${R} 0 1 0 ${R * 2} 0 a ${R} ${R} 0 1 0 ${-R * 2} 0`
            : `M ${P(a0, R)} A ${R} ${R} 0 ${big} 1 ${P(a1, R)} L ${P(a1, r0)} A ${r0} ${r0} 0 ${big} 0 ${P(a0, r0)} Z`
          const seg = <path key={i} d={d} fill={col(i)} />
          a0 = a1
          return seg
        })}
      </svg>
    )
  }

  return (
    <svg viewBox="0 0 100 100" className="el-chart" preserveAspectRatio="none">
      <g preserveAspectRatio="none">
        {[0, 0.5, 1].map(f => (
          <line key={f} x1={pad.l} x2={W - pad.r} y1={pad.t + ih * f} y2={pad.t + ih * f}
                stroke={paint('muted', t)} strokeOpacity=".22" strokeWidth=".3" />
        ))}
        {kind === 'bar' && series.map((ser, si) => {
          const bw = (iw / Math.max(1, cats.length)) * 0.72 / series.length
          return (ser.values || []).map((v, i) => {
            const x = pad.l + (i + 0.5) * (iw / Math.max(1, cats.length)) - (bw * series.length) / 2 + si * bw
            const y = yOf(Math.max(v, 0)), y0 = yOf(0)
            const h = Math.max(Math.abs(y0 - y), 0.3)
            return <g key={si + '-' + i}>
              <rect x={x} y={Math.min(y, y0)} width={Math.max(bw * 0.86, 0.4)}
                    height={h} fill={col(si)} />
              {c.labels && <text x={x + bw * 0.43} y={Math.max(3, Math.min(y, y0) - 1)}
                                  textAnchor="middle" {...label}>{v}</text>}
            </g>
          })
        })}
        {(kind === 'line' || kind === 'area' || kind === 'scatter') && series.map((ser, si) => {
          const pts = (ser.values || []).map((v, i) => [xOf(i), yOf(v)])
          const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0] + ' ' + p[1]).join(' ')
          return (
            <g key={si}>
              {kind === 'area' && pts.length > 1 &&
                <path d={`${d} L ${pts[pts.length - 1][0]} ${yOf(0)} L ${pts[0][0]} ${yOf(0)} Z`}
                      fill={col(si)} fillOpacity=".22" />}
              {kind !== 'scatter' && <path d={d} fill="none" stroke={col(si)} strokeWidth=".8"
                                           vectorEffect="non-scaling-stroke" />}
              {pts.map((p, i) => <g key={i}>
                <circle cx={p[0]} cy={p[1]} r="1.1" fill={col(si)} />
                {c.labels && <text x={p[0]} y={Math.max(3, p[1] - 1.8)}
                                  textAnchor="middle" {...label}>{ser.values[i]}</text>}
              </g>)}
            </g>
          )
        })}
        <line x1={pad.l} x2={W - pad.r} y1={yOf(0)} y2={yOf(0)}
              stroke={paint('muted', t)} strokeOpacity=".5" strokeWidth=".35" />
      </g>
      {cats.map((c2, i) => (
        <text key={i} x={xOf(i)} y={H - pad.b + size} textAnchor="middle" {...label}>{c2}</text>
      ))}
      {legend && series.map((ser, i) => (
        <g key={i} transform={`translate(${pad.l + i * (iw / series.length)} ${H - 1.5})`}>
          <rect width={size * 0.7} height={size * 0.7} y={-size * 0.6} fill={col(i)} rx=".4" />
          <text x={size} {...label}>{ser.name}</text>
        </g>
      ))}
    </svg>
  )
}

const DRAW = { text: TextEl, image: ImageEl, shape: ShapeEl, line: ShapeEl,
               table: TableEl, chart: ChartEl }

/** One element, positioned. Exported so the canvas can wrap it in a selection frame
 *  without redrawing it a second, subtly different way. */
export function Element({ el, template }) {
  const Draw = DRAW[el.type]
  if (!Draw || el.hidden) return null
  return (
    <div className="el" data-type={el.type} style={{
      left: pctX(el.x), top: pctY(el.y), width: pctX(el.w), height: pctY(el.h),
      transform: el.rotation ? `rotate(${el.rotation}deg)` : undefined,
      opacity: el.style?.opacity ?? 1,
      filter: el.style?.shadow ? 'drop-shadow(0 .35cqw .7cqw rgba(0,0,0,.28))' : undefined,
    }}>
      <Draw el={el} t={template} />
    </div>
  )
}

function Slide({ slide, template, children, className = '' }) {
  const bg = paint(slide?.background?.color, template, 'bg')
  return (
    <div className={'slide ' + className} style={{
      background: bg, aspectRatio: `${DOC.w} / ${DOC.h}`,
    }}>
      {(slide?.elements || []).map(el => <Element key={el.id} el={el} template={template} />)}
      {children}
    </div>
  )
}

/* The rail draws one of these per slide, so a 50-slide deck redraws 50 slides -- charts,
   SVG and all -- every time anything in the document changes. The reducer never mutates,
   so an untouched slide is the same object it was last render and memo actually holds.
   The canvas passes `children`, whose identity changes each render, so the slide being
   edited still redraws: exactly the one that has to. */
export default memo(Slide)
