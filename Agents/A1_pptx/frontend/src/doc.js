/* The presentation document, and every way to change it.
 *
 * One reducer, immutable updates. Undo is a stack of whole-document references rather
 * than a diff log: because nothing is mutated in place, an untouched slide is the same
 * object in every entry, so a "snapshot" of a 60-slide deck is 60 pointers. That is
 * cheap enough to be boring, which is the point -- a diff log would be a second model of
 * the document to keep correct.
 *
 * Drags coalesce: dragging an element across the canvas fires ~200 updates and leaves
 * exactly one undo step, because commits carry a tag and a commit that repeats the last
 * tag within COALESCE ms replaces it instead of stacking.
 *
 * Nothing here knows about React beyond being a reducer, and nothing here knows about
 * the server. The shapes match backend/deck.py exactly; the server revalidates all of it
 * anyway, so this file is for the user's benefit, not the server's.
 */
export const LIMIT = 120          // undo depth
const COALESCE = 900              // ms within which a repeated tag folds into one step

let seq = 0
export const uid = (p = 'el') => `${p}_${Date.now().toString(36)}${(seq++).toString(36)}`

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))

/* ---------- element factories: the "insert" menu, as data ---------- */

export const PRESETS = {
  heading: { type: 'text', w: 7, h: 1.1, content: { text: 'Heading' },
             style: { size: 40, font: 'display', bold: true, color: 'primary' } },
  subheading: { type: 'text', w: 6.5, h: 0.8, content: { text: 'Subheading' },
                style: { size: 24, font: 'display', color: 'primary' } },
  body: { type: 'text', w: 6, h: 1.4, content: { text: 'Body text' },
          style: { size: 18, color: 'text', lineHeight: 1.35 } },
  bullets: { type: 'text', w: 6, h: 2.4,
             content: { text: 'First point\nSecond point\nThird point' },
             style: { size: 18, color: 'text', bullets: true, spaceAfter: 14 } },
  caption: { type: 'text', w: 4, h: 0.5, content: { text: 'Caption' },
             style: { size: 12, color: 'muted' } },
  rect: { type: 'shape', w: 3, h: 2, content: { shape: 'rect' },
          style: { fill: 'accent', radius: 0.12 } },
  ellipse: { type: 'shape', w: 2.4, h: 2.4, content: { shape: 'ellipse' },
             style: { fill: 'accent' } },
  triangle: { type: 'shape', w: 2.4, h: 2.2, content: { shape: 'triangle' },
              style: { fill: 'accent' } },
  arrow: { type: 'shape', w: 3, h: 1.2, content: { shape: 'arrow' },
           style: { fill: 'accent' } },
  line: { type: 'line', w: 4, h: 0, content: { shape: 'line' },
          style: { stroke: 'primary', strokeWidth: 0.03 } },
  table: { type: 'table', w: 6, h: 2.2,
           content: { header: true, rows: [['Metric', 'Now', 'Target'],
                                           ['Latency', '412ms', '200ms'],
                                           ['Uptime', '99.1%', '99.9%']] },
           style: { size: 14 } },
  image: { type: 'image', w: 4, h: 3, content: { url: null, alt: '' }, style: { fit: 'cover' } },
}

export const chartPreset = (chart = 'bar') => ({
  type: 'chart', w: 6, h: 3.6,
  content: { chart, categories: ['Q1', 'Q2', 'Q3', 'Q4'],
             series: [{ name: 'Revenue', values: [12, 19, 24, 31] }],
             legend: true, labels: true },
  style: { size: 12, color: 'text' },
})

/** A full element from a preset, centred on the slide unless placed. */
export function makeElement(preset, doc, at) {
  const p = typeof preset === 'string' ? PRESETS[preset] : preset
  const w = p.w ?? 3, h = p.h ?? 1
  return {
    id: uid(p.type), type: p.type, rotation: 0, locked: false, hidden: false,
    x: clamp(at?.x ?? (doc.w - w) / 2, 0, doc.w - 0.2),
    y: clamp(at?.y ?? (doc.h - h) / 2, 0, doc.h - 0.2),
    w, h,
    content: structuredClone(p.content || {}),
    style: structuredClone(p.style || {}),
  }
}

export const blankSlide = () => ({
  id: uid('slide'), name: 'Untitled slide', kind: 'bullets',
  background: { color: 'bg' }, elements: [], notes: '', hidden: false,
})

/* ---------- reading ---------- */

export const slideAt = (deck, i) => deck.slides[clamp(i, 0, deck.slides.length - 1)]
export const findSlide = (deck, id) => deck.slides.find(s => s.id === id)

/** The bounding box of a set of elements, in inches. */
export function bounds(els) {
  if (!els.length) return null
  const x = Math.min(...els.map(e => e.x)), y = Math.min(...els.map(e => e.y))
  return { x, y,
           w: Math.max(...els.map(e => e.x + e.w)) - x,
           h: Math.max(...els.map(e => e.y + e.h)) - y }
}

/* ---------- writing ---------- */

const mapSlide = (deck, i, fn) => ({
  ...deck,
  slides: deck.slides.map((s, j) => (j === i ? fn(s) : s)),      // untouched slides keep
})                                                              // their identity

const mapElements = (deck, i, ids, fn) => mapSlide(deck, i, s => ({
  ...s,
  elements: s.elements.map(e => (ids.includes(e.id) ? fn(e) : e)),
}))

/** Deep-merge a patch into an element. Two levels is all the model has. */
export function patchElement(el, patch) {
  const out = { ...el }
  for (const [k, v] of Object.entries(patch)) {
    if ((k === 'style' || k === 'content') && v && typeof v === 'object')
      out[k] = { ...(el[k] || {}), ...v }
    else out[k] = v
  }
  return out
}

/* ---------- history ---------- */

export const initial = deck => ({ deck, past: [], future: [], tag: null, at: 0, dirty: false })

function commit(state, deck, tag) {
  if (deck === state.deck) return state
  const now = Date.now()
  const fold = tag && tag === state.tag && now - state.at < COALESCE && state.past.length
  return {
    deck,
    // A folded commit replaces the head rather than stacking, so one drag is one undo.
    past: fold ? state.past : [...state.past, state.deck].slice(-LIMIT),
    future: [],
    tag: tag ?? null,
    at: now,
    dirty: true,
  }
}

export function reducer(state, action) {
  const { deck } = state
  const i = action.slide ?? 0

  switch (action.type) {
    /* --- history --- */
    case 'undo': {
      if (!state.past.length) return state
      return { ...state, deck: state.past[state.past.length - 1],
               past: state.past.slice(0, -1), future: [deck, ...state.future],
               tag: null, dirty: true }
    }
    case 'redo': {
      if (!state.future.length) return state
      return { ...state, deck: state.future[0], past: [...state.past, deck],
               future: state.future.slice(1), tag: null, dirty: true }
    }
    case 'saved':
      // Only clears the flag if nothing changed while the request was in flight.
      return action.at >= state.at ? { ...state, dirty: false } : state

    case 'commit':
      // "The gesture is over." Clears the coalescing tag so the next change starts a
      // fresh undo step. It must not touch `deck` or `dirty` -- releasing the mouse is
      // not an edit, and a commit that pushed history would make every slider drag
      // cost two undos.
      return state.tag ? { ...state, tag: null } : state
    case 'replace':          // a whole new document (AI edit, restore, reload)
      return commit(state, action.deck, null)

    /* --- elements --- */
    case 'patch':
      // `patch` applies the same change to everything; `each` computes one per element,
      // which is what a multi-element drag or resize needs.
      return commit(state, mapElements(deck, i, action.ids, e => {
        if (e.locked && !action.force) return e
        const p = action.each ? action.each(e.id) : action.patch
        return p ? patchElement(e, p) : e
      }), action.tag)

    case 'add':
      return commit(state, mapSlide(deck, i, s => ({ ...s, elements: [...s.elements, ...action.elements] })), null)

    case 'remove': {
      const ids = action.ids
      return commit(state, mapSlide(deck, i, s => ({
        ...s, elements: s.elements.filter(e => !ids.includes(e.id) || e.locked),
      })), null)
    }

    case 'order': {
      // Array order is z-order, in the document and in both renderers. Moving a layer is
      // moving it in this array -- there is no separate zIndex to fall out of step.
      const ids = action.ids
      return commit(state, mapSlide(deck, i, s => {
        const keep = s.elements.filter(e => !ids.includes(e.id))
        const move = s.elements.filter(e => ids.includes(e.id))
        if (action.to === 'front') return { ...s, elements: [...keep, ...move] }
        if (action.to === 'back') return { ...s, elements: [...move, ...keep] }
        const out = s.elements.slice()
        const step = action.to === 'forward' ? 1 : -1
        const order = step > 0 ? [...move].reverse() : move
        for (const el of order) {
          const at = out.indexOf(el), next = at + step
          if (next < 0 || next >= out.length) continue
          out.splice(at, 1)
          out.splice(next, 0, el)
        }
        return { ...s, elements: out }
      }), null)
    }

    /* --- slides --- */
    case 'slidePatch':
      return commit(state, mapSlide(deck, i, s => ({ ...s, ...action.patch })), action.tag)

    case 'slideAdd': {
      const at = action.at ?? deck.slides.length
      const slides = deck.slides.slice()
      slides.splice(at, 0, ...action.slides)
      return commit(state, { ...deck, slides }, null)
    }

    case 'slideRemove': {
      if (deck.slides.length <= action.ids.length) return state   // never zero slides
      return commit(state, { ...deck, slides: deck.slides.filter(s => !action.ids.includes(s.id)) }, null)
    }

    case 'slideMove': {
      const slides = deck.slides.slice()
      const [moved] = slides.splice(action.from, 1)
      if (!moved) return state
      slides.splice(clamp(action.to, 0, slides.length), 0, moved)
      return commit(state, { ...deck, slides }, null)
    }

    case 'deck':
      return commit(state, { ...deck, ...action.patch }, action.tag)

    default:
      return state
  }
}

/* ---------- copies ---------- */

/** A deep copy with fresh ids, so pasting twice does not make two elements share one id
 *  and every subsequent edit hit both of them. */
export const copyElement = (el, dx = 0.25, dy = 0.25) => ({
  ...structuredClone(el), id: uid(el.type), x: el.x + dx, y: el.y + dy, locked: false,
})

export const copySlide = s => ({
  ...structuredClone(s), id: uid('slide'),
  name: /copy( \d+)?$/.test(s.name) ? s.name : s.name + ' copy',
  elements: s.elements.map(e => ({ ...structuredClone(e), id: uid(e.type) })),
})

/* ---------- snapping ---------- */

const SNAP = 0.06        // inches: about 5px on a 1200px-wide canvas

/** Candidate guides: the slide's own thirds and centre, plus every edge and centre of
 *  every element that is not being dragged. Returns the adjusted delta and the guides to
 *  draw, so the canvas shows the user exactly what it snapped to. */
export function snap(moving, others, doc, dx, dy) {
  const b = bounds(moving)
  if (!b) return { dx, dy, guides: [] }
  const vx = [0, doc.w / 2, doc.w], hy = [0, doc.h / 2, doc.h]
  for (const o of others) {
    vx.push(o.x, o.x + o.w / 2, o.x + o.w)
    hy.push(o.y, o.y + o.h / 2, o.y + o.h)
  }
  const guides = []
  const fit = (edges, lines) => {
    let best = null
    for (const edge of edges) for (const line of lines) {
      const d = line - edge.at
      if (Math.abs(d) < SNAP && (!best || Math.abs(d) < Math.abs(best.d))) best = { d, line }
    }
    return best
  }
  const mx = fit([{ at: b.x + dx }, { at: b.x + b.w / 2 + dx }, { at: b.x + b.w + dx }], vx)
  const my = fit([{ at: b.y + dy }, { at: b.y + b.h / 2 + dy }, { at: b.y + b.h + dy }], hy)
  if (mx) { dx += mx.d; guides.push({ axis: 'x', at: mx.line }) }
  if (my) { dy += my.d; guides.push({ axis: 'y', at: my.line }) }
  return { dx, dy, guides }
}

/** Align / distribute a selection inside its own bounding box (2+) or the slide (1). */
export function alignPatch(els, how, doc) {
  const b = els.length > 1 ? bounds(els) : { x: 0, y: 0, w: doc.w, h: doc.h }
  const out = {}
  if (how === 'distribute-h' || how === 'distribute-v') {
    const horiz = how === 'distribute-h'
    const key = horiz ? 'x' : 'y', len = horiz ? 'w' : 'h'
    const sorted = [...els].sort((a, z) => a[key] - z[key])
    const total = sorted.reduce((n, e) => n + e[len], 0)
    const gap = (b[len] - total) / Math.max(1, sorted.length - 1)
    let at = b[key]
    for (const e of sorted) { out[e.id] = { [key]: +at.toFixed(4) }; at += e[len] + gap }
    return out
  }
  for (const e of els) {
    if (how === 'left') out[e.id] = { x: b.x }
    else if (how === 'center') out[e.id] = { x: b.x + (b.w - e.w) / 2 }
    else if (how === 'right') out[e.id] = { x: b.x + b.w - e.w }
    else if (how === 'top') out[e.id] = { y: b.y }
    else if (how === 'middle') out[e.id] = { y: b.y + (b.h - e.h) / 2 }
    else if (how === 'bottom') out[e.id] = { y: b.y + b.h - e.h }
  }
  return out
}
