/* The canvas: everything you can do to a slide with a pointer.
 *
 * Slide.jsx draws; this file only handles input and paints the selection chrome on top.
 * Keeping them apart is what lets a thumbnail, present mode and the editor share one
 * renderer -- and it means a bug in dragging can never change how a slide looks.
 *
 * All geometry is in inches, converted at the edge from the canvas element's measured
 * width. Nothing downstream deals in pixels, so zoom is one number and changes nothing
 * about the document.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import Slide, { DOC, fontOf, paint, pt } from './Slide'
import { bounds, snap } from './doc'

const HANDLES = [
  ['nw', 0, 0], ['n', .5, 0], ['ne', 1, 0],
  ['w', 0, .5], ['e', 1, .5],
  ['sw', 0, 1], ['s', .5, 1], ['se', 1, 1],
]
const MIN = 0.12                      // inches: below this an element cannot be grabbed

export default function Canvas({
  slide, template, zoom, grid, selection, setSelection, dispatch, slideIndex,
  editing, setEditing, onCommit,
}) {
  const ref = useRef(null)
  const [drag, setDrag] = useState(null)      // live gesture, never in the document
  const [marquee, setMarquee] = useState(null)
  const [guides, setGuides] = useState([])
  const els = slide.elements
  const chosen = els.filter(e => selection.includes(e.id))
  const box = bounds(chosen)

  // inches per pixel, measured rather than assumed: the canvas is fluid and zoomable.
  const scale = useCallback(() => {
    const r = ref.current?.getBoundingClientRect()
    return r ? DOC.w / r.width : 1
  }, [])

  const begin = (e, mode, id, handle) => {
    if (e.button !== 0) return
    e.stopPropagation()
    const el = els.find(x => x.id === id)
    if (el?.locked && mode !== 'select') return

    let next = selection
    if (mode === 'move' || mode === 'select') {
      if (e.shiftKey || e.metaKey || e.ctrlKey)
        next = selection.includes(id) ? selection.filter(s => s !== id) : [...selection, id]
      else if (!selection.includes(id)) next = [id]
      setSelection(next)
    }
    if (mode === 'select') return

    const picked = els.filter(x => next.includes(x.id) && !x.locked)
    if (!picked.length) return
    // No setPointerCapture here: capturing on the handle would route pointermove to the
    // handle and away from the listener that does the work. Window listeners instead --
    // they also keep the drag alive when the pointer leaves the canvas entirely, which
    // is exactly what you want when dragging something towards the edge.
    setDrag({ mode, handle, x0: e.clientX, y0: e.clientY, ids: picked.map(p => p.id),
              start: Object.fromEntries(picked.map(p => [p.id, { ...p }])),
              origin: bounds(picked) })
  }

  const onMove = e => {
    if (!drag) return
    const k = scale()
    let dx = (e.clientX - drag.x0) * k, dy = (e.clientY - drag.y0) * k
    const moving = drag.ids.map(id => drag.start[id])

    if (drag.mode === 'move') {
      if (!e.altKey) {          // hold alt to place freely, ignoring every guide
        const fit = snap(moving, els.filter(x => !drag.ids.includes(x.id) && !x.hidden),
                         DOC, dx, dy)
        dx = fit.dx; dy = fit.dy
        setGuides(fit.guides)
      } else setGuides([])
      if (e.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0 }
      dispatch({ type: 'patch', slide: slideIndex, ids: drag.ids, tag: 'move',
                 patch: null, each: id => ({ x: round(drag.start[id].x + dx),
                                             y: round(drag.start[id].y + dy) }) })
    } else if (drag.mode === 'resize') {
      // ponytail: the delta is applied in unrotated space, so resizing a rotated element
      // grows along the slide's axes rather than its own. Rotate-then-resize is rare and
      // the fix is a full transform matrix per element -- worth it only if anyone asks.
      const h = drag.handle
      const sx = h.includes('e') ? 1 : h.includes('w') ? -1 : 0
      const sy = h.includes('s') ? 1 : h.includes('n') ? -1 : 0
      const o = drag.origin
      let w = Math.max(MIN, o.w + dx * sx), hgt = Math.max(MIN, o.h + dy * sy)
      if (e.shiftKey && sx && sy) {           // shift keeps the group's aspect ratio
        const r = Math.min(w / o.w, hgt / o.h); w = o.w * r; hgt = o.h * r
      }
      const kx = w / (o.w || 1), ky = hgt / (o.h || 1)
      const ax = sx < 0 ? o.x + o.w : o.x, ay = sy < 0 ? o.y + o.h : o.y
      dispatch({ type: 'patch', slide: slideIndex, ids: drag.ids, tag: 'resize',
                 patch: null, each: id => {
                   const s = drag.start[id]
                   return {
                     x: round(sx ? ax + (s.x - ax) * kx : s.x),
                     y: round(sy ? ay + (s.y - ay) * ky : s.y),
                     w: round(sx ? Math.max(MIN, s.w * kx) : s.w),
                     h: round(sy ? Math.max(MIN, s.h * ky) : s.h),
                   }
                 } })
    } else if (drag.mode === 'rotate') {
      const r = ref.current.getBoundingClientRect()
      const cx = r.left + ((box.x + box.w / 2) / DOC.w) * r.width
      const cy = r.top + ((box.y + box.h / 2) / DOC.h) * r.height
      let deg = Math.atan2(e.clientY - cy, e.clientX - cx) * 180 / Math.PI + 90
      if (e.shiftKey) deg = Math.round(deg / 15) * 15
      dispatch({ type: 'patch', slide: slideIndex, ids: drag.ids, tag: 'rotate',
                 patch: { rotation: Math.round(deg) } })
    }
  }

  const end = () => {
    if (drag) onCommit?.()
    setDrag(null); setGuides([])
  }

  /* marquee: drag on empty canvas to select everything it touches */
  const startMarquee = e => {
    if (e.button !== 0 || e.target !== e.currentTarget) return
    setEditing(null)
    if (!e.shiftKey) setSelection([])
    const r = ref.current.getBoundingClientRect()
    setMarquee({ x0: e.clientX - r.left, y0: e.clientY - r.top,
                 x: e.clientX - r.left, y: e.clientY - r.top,
                 base: e.shiftKey ? selection : [] })
  }
  const moveMarquee = e => {
    if (!marquee) return
    const r = ref.current.getBoundingClientRect()
    const m = { ...marquee, x: e.clientX - r.left, y: e.clientY - r.top }
    setMarquee(m)
    const a = { x: Math.min(m.x0, m.x) / r.width * DOC.w, y: Math.min(m.y0, m.y) / r.height * DOC.h,
                w: Math.abs(m.x - m.x0) / r.width * DOC.w, h: Math.abs(m.y - m.y0) / r.height * DOC.h }
    const hit = els.filter(el => !el.hidden && !el.locked &&
                                 el.x < a.x + a.w && el.x + el.w > a.x &&
                                 el.y < a.y + a.h && el.y + el.h > a.y).map(el => el.id)
    setSelection([...new Set([...m.base, ...hit])])
  }

  // The whole gesture lives on the window while it is running, so it survives the pointer
  // leaving the canvas, the panel, or the browser chrome, and always ends.
  useEffect(() => {
    if (!drag && !marquee) return
    const move = e => { onMove(e); moveMarquee(e) }
    const up = () => { end(); setMarquee(null) }
    addEventListener('pointermove', move)
    addEventListener('pointerup', up)
    addEventListener('pointercancel', up)
    return () => {
      removeEventListener('pointermove', move)
      removeEventListener('pointerup', up)
      removeEventListener('pointercancel', up)
    }
  })

  const active = editing && els.find(e => e.id === editing)

  return (
    <div className="canvas-scroll">
      {/* Both the width and its cap scale, or zooming past 100% hits the cap and
          does nothing. The scroll container takes the overflow. */}
      <div className="canvas-frame"
           style={{ width: zoom * 100 + '%', maxWidth: zoom * 1200 + 'px' }}>
        <Slide slide={slide} template={template} className="canvas-slide">
          {/* Interaction sits above the drawing and never alters it. */}
          <div className="hit-layer" ref={ref} onPointerDown={startMarquee}>
            {grid && <div className="grid-overlay" aria-hidden="true" />}

            {els.map(el => (
              <div key={el.id} className={'hit' + (el.locked ? ' locked' : '') +
                                          (el.hidden ? ' ghost' : '')}
                   style={{
                     left: (el.x / DOC.w) * 100 + '%', top: (el.y / DOC.h) * 100 + '%',
                     width: (el.w / DOC.w) * 100 + '%', height: (el.h / DOC.h) * 100 + '%',
                     transform: el.rotation ? `rotate(${el.rotation}deg)` : undefined,
                   }}
                   onPointerDown={e => begin(e, el.locked ? 'select' : 'move', el.id)}
                   onDoubleClick={() => el.type === 'text' && !el.locked && setEditing(el.id)} />
            ))}

            {guides.map((g, i) => (
              <div key={i} className={'guide ' + g.axis}
                   style={g.axis === 'x' ? { left: (g.at / DOC.w) * 100 + '%' }
                                         : { top: (g.at / DOC.h) * 100 + '%' }} />
            ))}

            {box && !editing && (
              <div className="frame" style={{
                left: (box.x / DOC.w) * 100 + '%', top: (box.y / DOC.h) * 100 + '%',
                width: (box.w / DOC.w) * 100 + '%', height: (box.h / DOC.h) * 100 + '%',
                transform: chosen.length === 1 && chosen[0].rotation
                  ? `rotate(${chosen[0].rotation}deg)` : undefined,
              }}>
                {chosen.every(c => !c.locked) && <>
                  {HANDLES.map(([h, fx, fy]) => (
                    <span key={h} className={'handle h-' + h}
                          style={{ left: fx * 100 + '%', top: fy * 100 + '%' }}
                          onPointerDown={e => begin(e, 'resize', chosen[0].id, h)} />
                  ))}
                  <span className="handle rot" onPointerDown={e => begin(e, 'rotate', chosen[0].id)} />
                </>}
              </div>
            )}

            {marquee && (
              <div className="marquee" style={{
                left: Math.min(marquee.x0, marquee.x), top: Math.min(marquee.y0, marquee.y),
                width: Math.abs(marquee.x - marquee.x0), height: Math.abs(marquee.y - marquee.y0),
              }} />
            )}

            {active && <TextEditor key={active.id} el={active} template={template} slideIndex={slideIndex}
                                   dispatch={dispatch} done={() => { setEditing(null); onCommit?.() }} />}
          </div>
        </Slide>
      </div>
    </div>
  )
}

const round = n => Math.round(n * 1000) / 1000

/** Editing text in place. A textarea inside the slide container inherits the same cqw
 *  font sizing the rendered element uses, so the words do not jump when you double-click
 *  them and do not reflow when you stop. */
function TextEditor({ el, template, slideIndex, dispatch, done }) {
  const ref = useRef(null)
  const s = el.style
  useLayoutEffect(() => {
    const t = ref.current
    if (!t) return
    t.focus()
    t.setSelectionRange(t.value.length, t.value.length)
  }, [el.id])
  return (
    <textarea ref={ref} className="inline-edit" defaultValue={el.content.text ?? ''}
      aria-label="Edit text"
      onPointerDown={e => e.stopPropagation()}
      onDoubleClick={e => e.stopPropagation()}
      onBlur={done}
      onKeyDown={e => {
        e.stopPropagation()
        if (e.key === 'Escape') { e.preventDefault(); done() }
      }}
      onChange={e => dispatch({ type: 'patch', slide: slideIndex, ids: [el.id], tag: 'text',
                                patch: { content: { ...el.content, text: e.target.value } } })}
      style={{
        left: (el.x / DOC.w) * 100 + '%', top: (el.y / DOC.h) * 100 + '%',
        width: (el.w / DOC.w) * 100 + '%', height: (el.h / DOC.h) * 100 + '%',
        transform: el.rotation ? `rotate(${el.rotation}deg)` : undefined,
        fontFamily: fontOf(s, template), fontSize: pt(s.size ?? 18),
        lineHeight: s.lineHeight ?? 1.2, fontWeight: s.bold ? 700 : 400,
        fontStyle: s.italic ? 'italic' : undefined,
        textAlign: s.align || 'left', color: paint(s.color, template),
        letterSpacing: s.letterSpacing ? pt(s.letterSpacing) : undefined,
      }} />
  )
}
