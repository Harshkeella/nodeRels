/* Renderer self-check. One command, no framework, no browser:
 *
 *     cd frontend && npm test
 *
 * It server-renders a slide holding one of every element type and asserts the output
 * actually contains what the document said, then exercises the reducer -- undo, redo,
 * coalescing, z-order, snapping, alignment.
 *
 * The point is the fidelity contract. ppt.py's --demo proves the same document reaches
 * PowerPoint; this proves it reaches the browser. Both read the same elements, so if one
 * of these two files starts disagreeing with the other, one of them fails.
 */
import assert from 'node:assert/strict'
import { renderToStaticMarkup } from 'react-dom/server'
import Slide, { paint, pt, setGeometry } from './Slide'
import {
  alignPatch, bounds, chartPreset, copyElement, copySlide, initial, makeElement,
  PRESETS, reducer, snap,
} from './doc'

const template = { display_font: 'Georgia', body_font: 'Arial', primary: '16324F',
                   accent: '2E86AB', bg: 'F7F9FB', text: '1C1C1C', muted: '8A94A0' }
const DOC = { w: 13.333, h: 7.5 }
let checks = 0
const check = (name, fn) => { fn(); checks++; process.stdout.write('ok  ' + name + '\n') }

const el = (type, extra) => ({
  id: type + '_1', type, x: 1, y: 1, w: 4, h: 2, rotation: 0, locked: false, hidden: false,
  content: {}, style: {}, ...extra,
})

check('every element type renders', () => {
  const slide = {
    id: 's1', name: 'All', background: { color: 'bg' }, notes: '', hidden: false,
    elements: [
      el('text', { content: { text: 'Line one\nLine two' },
                   style: { size: 40, color: 'accent', bold: true, bullets: true } }),
      el('image', { content: { url: '/uploads/abc.png', alt: 'a photo' } }),
      el('shape', { content: { shape: 'ellipse' }, style: { fill: 'accent', opacity: 0.5 } }),
      el('line', { content: { shape: 'line' }, style: { stroke: 'primary', strokeWidth: 0.03 } }),
      el('table', { content: { rows: [['Metric', 'Now'], ['Latency', '412ms']], header: true } }),
      el('chart', { content: chartPreset('bar').content }),
    ],
  }
  const html = renderToStaticMarkup(<Slide slide={slide} template={template} />)
  assert.match(html, /Line one/, 'text did not render')
  assert.match(html, /•\s\sLine one/, 'the bullet marker must be in the text, as in the .pptx')
  assert.match(html, /\/uploads\/abc\.png/, 'image src missing')
  assert.match(html, /alt="a photo"/, 'alt text missing')
  assert.match(html, /412ms/, 'table cell missing')
  assert.match(html, /border-radius:50%/, 'ellipse did not round')
  assert.match(html, /opacity:0\.5/, 'opacity did not render')
  assert.match(html, /<svg/, 'chart did not draw')
  assert.match(html, /Q1/, 'chart categories missing')
  assert.match(html, /#2E86AB/, 'theme token did not resolve to the template colour')
  assert.match(html, /background:#F7F9FB/, 'slide background missing')
})

check('a hidden element draws nothing, an image with no source says so', () => {
  const one = t => renderToStaticMarkup(
    <Slide slide={{ background: { color: 'bg' }, elements: [t] }} template={template} />)
  assert.doesNotMatch(one(el('text', { hidden: true, content: { text: 'secret' } })), /secret/)
  // Two different faults, two different messages: nothing was ever chosen, versus a
  // picture that was chosen and will not load. The second is onError, in the browser.
  assert.match(one(el('image', { content: { url: null } })), /No image yet/)
  const loading = one(el('image', { content: { url: '/uploads/x.png' } }))
  assert.match(loading, /uploads\/x\.png/, 'the source must still be requested')
  assert.match(loading, /el-loading/, 'a loading image must show a placeholder, not a hole')
})

check('every chart kind draws without throwing', () => {
  for (const kind of ['bar', 'line', 'area', 'pie', 'donut', 'scatter']) {
    const html = renderToStaticMarkup(
      <Slide slide={{ background: { color: 'bg' },
                      elements: [el('chart', { content: chartPreset(kind).content })] }}
             template={template} />)
    assert.match(html, /<svg/, kind + ' drew nothing')
  }
  // A chart with no data at all must not divide by zero into NaN attributes.
  const empty = renderToStaticMarkup(
    <Slide slide={{ background: { color: 'bg' }, elements: [el('chart', {
      content: { chart: 'bar', categories: [], series: [], legend: false } })] }}
      template={template} />)
  assert.doesNotMatch(empty, /NaN/, 'empty chart produced NaN geometry')
})

check('colours resolve like deck.color does', () => {
  assert.equal(paint('accent', template), '#2E86AB')
  assert.equal(paint('#ff0000', template), '#ff0000')
  assert.equal(paint('javascript:alert(1)', template), '#1C1C1C')   // falls back, never passes
  assert.equal(paint(null, template, 'muted'), '#8A94A0')
})

check('points map to the slide the same way in both renderers', () => {
  setGeometry({ w: 13.333, h: 7.5 })
  // 13.333in is 960pt is 100cqw, so 1cqw = 9.6pt.
  assert.equal(Math.round(parseFloat(pt(9.6)) * 1000) / 1000, 1)
  setGeometry({ w: 10, h: 7.5 })                       // 4:3 rescales type with the slide
  assert.equal(Math.round(parseFloat(pt(7.2)) * 1000) / 1000, 1)
  setGeometry({ w: 13.333, h: 7.5 })
})

/* ---------- the document reducer ---------- */

const deck = () => ({
  id: 'd', deck_title: 'T', template: 'corporate', w: 13.333, h: 7.5,
  slides: [{ id: 's1', name: 'One', background: { color: 'bg' }, notes: '', hidden: false,
             elements: [el('text', { id: 'a', content: { text: 'A' } }),
                        el('shape', { id: 'b', x: 6 })] },
           { id: 's2', name: 'Two', background: { color: 'bg' }, notes: '', hidden: false,
             elements: [] }],
})

check('undo and redo walk the history', () => {
  let s = initial(deck())
  s = reducer(s, { type: 'patch', slide: 0, ids: ['a'], patch: { x: 5 } })
  assert.equal(s.deck.slides[0].elements[0].x, 5)
  assert.equal(s.dirty, true)
  s = reducer(s, { type: 'undo' })
  assert.equal(s.deck.slides[0].elements[0].x, 1)
  s = reducer(s, { type: 'redo' })
  assert.equal(s.deck.slides[0].elements[0].x, 5)
  assert.equal(reducer(initial(deck()), { type: 'undo' }).past.length, 0)  // never underflows
})

check('a drag is one undo step, a commit ends the run', () => {
  let s = initial(deck())
  for (let i = 0; i < 40; i++)
    s = reducer(s, { type: 'patch', slide: 0, ids: ['a'], tag: 'move', patch: { x: i } })
  assert.equal(s.past.length, 1, 'a drag must coalesce into one history entry')
  assert.equal(s.deck.slides[0].elements[0].x, 39)
  const at = s.at
  s = reducer(s, { type: 'commit' })
  assert.equal(s.tag, null)
  assert.equal(s.at, at, 'commit must not look like an edit')
  assert.equal(s.deck, s.deck, 'commit must not rebuild the document')
  s = reducer(s, { type: 'patch', slide: 0, ids: ['a'], tag: 'move', patch: { x: 99 } })
  assert.equal(s.past.length, 2, 'after a commit the next drag is its own step')
  s = reducer(s, { type: 'undo' })
  assert.equal(s.deck.slides[0].elements[0].x, 39, 'undo landed in the middle of a drag')
})

check('untouched slides keep their identity so history stays cheap', () => {
  const s0 = initial(deck())
  const s1 = reducer(s0, { type: 'patch', slide: 0, ids: ['a'], patch: { x: 3 } })
  assert.equal(s1.deck.slides[1], s0.deck.slides[1], 'slide 2 was needlessly copied')
  assert.notEqual(s1.deck.slides[0], s0.deck.slides[0])
})

check('locked elements resist everything but force', () => {
  let s = initial(deck())
  s = reducer(s, { type: 'patch', slide: 0, ids: ['a'], force: true, patch: { locked: true } })
  s = reducer(s, { type: 'patch', slide: 0, ids: ['a'], patch: { x: 9 } })
  assert.equal(s.deck.slides[0].elements[0].x, 1, 'a locked element moved')
  s = reducer(s, { type: 'remove', slide: 0, ids: ['a'] })
  assert.equal(s.deck.slides[0].elements.length, 2, 'a locked element was deleted')
  s = reducer(s, { type: 'patch', slide: 0, ids: ['a'], force: true, patch: { x: 9 } })
  assert.equal(s.deck.slides[0].elements[0].x, 9, 'force must still win')
})

check('z-order is array order', () => {
  let s = initial(deck())
  s = reducer(s, { type: 'order', slide: 0, ids: ['a'], to: 'front' })
  assert.deepEqual(s.deck.slides[0].elements.map(e => e.id), ['b', 'a'])
  s = reducer(s, { type: 'order', slide: 0, ids: ['a'], to: 'back' })
  assert.deepEqual(s.deck.slides[0].elements.map(e => e.id), ['a', 'b'])
  s = reducer(s, { type: 'order', slide: 0, ids: ['a'], to: 'forward' })
  assert.deepEqual(s.deck.slides[0].elements.map(e => e.id), ['b', 'a'])
  s = reducer(s, { type: 'order', slide: 0, ids: ['a'], to: 'forward' })
  assert.deepEqual(s.deck.slides[0].elements.map(e => e.id), ['b', 'a'], 'walked off the end')
})

check('slides add, move and refuse to hit zero', () => {
  let s = initial(deck())
  s = reducer(s, { type: 'slideMove', from: 0, to: 1 })
  assert.deepEqual(s.deck.slides.map(x => x.id), ['s2', 's1'])
  s = reducer(s, { type: 'slideRemove', ids: ['s2'] })
  assert.equal(s.deck.slides.length, 1)
  const last = reducer(s, { type: 'slideRemove', ids: ['s1'] })
  assert.equal(last.deck.slides.length, 1, 'the last slide must not be deletable')
})

check('copies get fresh ids', () => {
  const src = deck().slides[0]
  const c = copySlide(src)
  assert.notEqual(c.id, src.id)
  assert.notEqual(c.elements[0].id, src.elements[0].id)
  assert.equal(c.elements[0].content.text, 'A')
  const e = copyElement(src.elements[0])
  assert.notEqual(e.id, src.elements[0].id)
  assert.equal(e.x, src.elements[0].x + 0.25, 'a paste must be visibly offset')
  copyElement(src.elements[0]).content.text = 'changed'
  assert.equal(src.elements[0].content.text, 'A', 'copy shares state with its source')
})

check('snapping pulls to an edge and alt-free drags stay put otherwise', () => {
  const moving = [{ x: 1.03, y: 2, w: 2, h: 1 }]
  const others = [{ x: 1, y: 5, w: 2, h: 1 }]
  const near = snap(moving, others, DOC, 0, 0)
  assert.ok(Math.abs(near.dx + 0.03) < 1e-9, 'did not snap to the neighbour edge')
  assert.equal(near.guides.length, 1)
  const far = snap([{ x: 4, y: 2, w: 2, h: 1 }], others, DOC, 0, 0)
  assert.equal(far.dx, 0, 'snapped to something it was nowhere near')
})

check('align and distribute do what they say', () => {
  const els = [{ id: 'a', x: 0, y: 0, w: 2, h: 1 }, { id: 'b', x: 8, y: 4, w: 2, h: 1 }]
  assert.equal(alignPatch(els, 'left', DOC).b.x, 0)
  assert.equal(alignPatch(els, 'top', DOC).b.y, 0)
  const single = alignPatch([els[0]], 'center', DOC)
  assert.equal(single.a.x, (DOC.w - 2) / 2, 'one element centres on the slide')
  const three = [{ id: 'a', x: 0, y: 0, w: 1, h: 1 }, { id: 'b', x: 3, y: 0, w: 1, h: 1 },
                 { id: 'c', x: 9, y: 0, w: 1, h: 1 }]
  const spread = alignPatch(three, 'distribute-h', DOC)
  assert.equal(spread.a.x, 0)
  assert.equal(spread.c.x, 9, 'the outer elements must not move')
  assert.equal(spread.b.x, 4.5, 'the middle one did not land halfway')
})

check('presets and bounds are sane', () => {
  for (const name of Object.keys(PRESETS)) {
    const e = makeElement(name, DOC)
    assert.ok(e.id && e.type && e.w > 0 && e.h >= 0, name + ' is not a usable element')
    assert.ok(e.x >= 0 && e.y >= 0, name + ' was placed off-slide')
  }
  assert.equal(bounds([]), null)
  assert.deepEqual(bounds([{ x: 1, y: 1, w: 2, h: 2 }, { x: 4, y: 0, w: 1, h: 1 }]),
                   { x: 1, y: 0, w: 4, h: 3 })
})

console.log(`\n${checks} checks passed`)
