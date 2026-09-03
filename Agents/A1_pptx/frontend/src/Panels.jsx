/* The left rail: everything you can put on a slide, plus the AI assistant.
 *
 * Every panel here produces canonical elements and hands them to the same `insert`
 * callback. Nothing in this file knows how an element is drawn or exported -- add a type
 * to deck.py and it can be inserted from here without a renderer change.
 */
import { useEffect, useRef, useState } from 'react'
import Icon, { IconButton } from './Icon'
import Slide, { paint } from './Slide'
import { chartPreset, makeElement, PRESETS, uid } from './doc'
import { askAI, imageFromUrl, searchImages, uploadImage } from './api'

/* Slide layouts, as schema. A template is a function of the document size, so the same
   layout is correct on 16:9 and 4:3 rather than being a screenshot of one of them. */
export const LAYOUTS = {
  'Title': d => [
    t(1.3, d.h / 2 - 1.2, d.w - 2.6, 1.4, 'Presentation title', 44, 'primary', {
      font: 'display', bold: true, align: 'center', valign: 'bottom' }),
    t(1.3, d.h / 2 + 0.32, d.w - 2.6, 1, 'A one-line subtitle', 18, 'accent',
      { align: 'center' }),
  ],
  'Section divider': d => [
    box(0.9, d.h / 2 - 0.62, 1.2, 0.09, 'accent'),
    t(0.9, d.h / 2 - 0.3, d.w - 1.8, 1.2, 'Section', 36, 'primary',
      { font: 'display', bold: true }),
  ],
  'Agenda': d => [
    t(0.9, 0.7, d.w - 1.8, 1, 'Agenda', 30, 'primary', { font: 'display', bold: true }),
    t(0.9, 2.1, d.w - 1.8, d.h - 3, 'First item\nSecond item\nThird item\nFourth item',
      20, 'text', { numbered: true, spaceAfter: 14 }),
  ],
  'Bullets': d => [
    t(0.9, 0.7, d.w - 1.8, 1.3, 'A specific claim', 30, 'primary',
      { font: 'display', bold: true }),
    t(0.9, 2.2, d.w - 1.8, d.h - 3, 'First point\nSecond point\nThird point', 18, 'text',
      { bullets: true, spaceAfter: 16 }),
  ],
  'Problem / solution': d => [
    t(0.9, 0.7, d.w - 1.8, 1.3, 'Problem and solution', 30, 'primary',
      { font: 'display', bold: true }),
    t(0.9, 2.2, (d.w - 2.4) / 2, 0.5, 'Problem', 16, 'muted', { bold: true }),
    t(0.9, 2.8, (d.w - 2.4) / 2, d.h - 3.7, 'What is going wrong today.', 17, 'text'),
    t(d.w / 2 + 0.3, 2.2, (d.w - 2.4) / 2, 0.5, 'Solution', 16, 'accent', { bold: true }),
    t(d.w / 2 + 0.3, 2.8, (d.w - 2.4) / 2, d.h - 3.7, 'What we do about it.', 17, 'text'),
  ],
  'Statistic': d => [
    t(0.9, 0.7, d.w - 1.8, 1, 'What the number is about', 24, 'primary',
      { font: 'display', bold: true }),
    t(0.9, 2.1, d.w - 1.8, 2.4, '412ms', 90, 'accent',
      { font: 'display', bold: true, align: 'center', valign: 'bottom' }),
    t(0.9, 4.6, d.w - 1.8, 0.9, 'median, end to end', 20, 'text', { align: 'center' }),
  ],
  'Three stats': d => {
    const w = (d.w - 2.4) / 3
    return [
      t(0.9, 0.7, d.w - 1.8, 1, 'The numbers that matter', 30, 'primary',
        { font: 'display', bold: true }),
      ...[['3.2x', 'growth'], ['412ms', 'latency'], ['99.9%', 'uptime']].flatMap(([n, l], i) => [
        t(0.9 + i * (w + 0.6), 2.5, w, 1.5, n, 54, 'accent',
          { font: 'display', bold: true, align: 'center', valign: 'bottom' }),
        t(0.9 + i * (w + 0.6), 4.1, w, 0.7, l, 15, 'muted', { align: 'center' }),
      ]),
    ]
  },
  'Comparison': d => [
    t(0.9, 0.7, d.w - 1.8, 1.3, 'Us and them', 30, 'primary', { font: 'display', bold: true }),
    { ...makeElement(PRESETS.table, d, { x: 0.9, y: 2.2 }), w: d.w - 1.8, h: d.h - 3 },
  ],
  'Timeline': d => {
    const w = (d.w - 1.8) / 4
    return [
      t(0.9, 0.7, d.w - 1.8, 1.2, 'How this plays out', 30, 'primary',
        { font: 'display', bold: true }),
      box(0.9, 3.4, d.w - 1.8, 0.035, 'muted'),
      ...['Now', 'Q1', 'Q2', 'Q3'].flatMap((label, i) => [
        { ...makeElement(PRESETS.ellipse, d, { x: 0.9 + i * w - 0.1, y: 3.29 }), w: 0.26, h: 0.26 },
        t(0.9 + i * w - 0.3, 2.5, w, 0.6, label, 17, 'primary', { bold: true }),
        t(0.9 + i * w - 0.3, 3.85, w, 1.4, 'What happens', 14, 'muted'),
      ]),
    ]
  },
  'Chart': d => [
    t(0.9, 0.7, d.w - 1.8, 1.2, 'What the data says', 30, 'primary',
      { font: 'display', bold: true }),
    { ...makeElement(chartPreset('bar'), d, { x: 0.9, y: 2.1 }), w: d.w - 1.8, h: d.h - 2.9 },
  ],
  'Quote': d => [
    t(1.6, d.h / 2 - 1.5, d.w - 3.2, 2, '“The sentence someone actually said.”', 32,
      'primary', { font: 'display', align: 'center', valign: 'bottom' }),
    t(1.6, d.h / 2 + 0.7, d.w - 3.2, 0.6, '— Who said it', 16, 'muted', { align: 'center' }),
  ],
  'Closing': d => [
    t(1.3, d.h / 2 - 1.2, d.w - 2.6, 1.4, 'The ask', 44, 'primary',
      { font: 'display', bold: true, align: 'center', valign: 'bottom' }),
    t(1.3, d.h / 2 + 0.32, d.w - 2.6, 1, 'questions?', 18, 'accent', { align: 'center' }),
  ],
}

function t(x, y, w, h, text, size, color, style = {}) {
  return { id: uid('text'), type: 'text', x, y, w, h, rotation: 0, locked: false,
           hidden: false, content: { text }, style: { size, color, ...style } }
}
function box(x, y, w, h, fill) {
  return { id: uid('shape'), type: 'shape', x, y, w, h, rotation: 0, locked: false,
           hidden: false, content: { shape: 'rect' }, style: { fill } }
}

const TABS = [
  ['layouts', 'Layouts', 'grid'],
  ['text', 'Text', 'type'],
  ['elements', 'Shapes', 'shape'],
  ['images', 'Images', 'image'],
  ['charts', 'Charts', 'chart'],
  ['ai', 'Ask AI', 'spark'],
]

export default function Panels({
  tab, setTab, deck, template, insert, applyLayout, ai, meta, putImage, replacing,
}) {
  return (
    <>
      <nav className="rail" aria-label="Insert">
      {TABS.filter(([id]) => !meta.integrated || !['images', 'ai'].includes(id)).map(([id, label, icon]) => (
          <button key={id} className={'rail-btn' + (tab === id ? ' on' : '')}
                  aria-pressed={tab === id} title={label}
                  onClick={() => setTab(tab === id ? null : id)}>
            <Icon name={icon} size={19} />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      {tab && (
        <aside className="panel" aria-label={TABS.find(t2 => t2[0] === tab)?.[1]}>
          <div className="panel-head">
            <h3>{TABS.find(t2 => t2[0] === tab)?.[1]}</h3>
            <IconButton icon="x" label="Close panel" onClick={() => setTab(null)} />
          </div>
          <div className="panel-body">
            {tab === 'layouts' && <Layouts deck={deck} template={template} apply={applyLayout} />}
            {tab === 'text' && <TextPanel insert={insert} />}
            {tab === 'elements' && <Shapes insert={insert} template={template} />}
            {tab === 'images' && <Images put={putImage} meta={meta} replacing={replacing} />}
            {tab === 'charts' && <Charts insert={insert} />}
            {tab === 'ai' && <Assistant {...ai} />}
          </div>
        </aside>
      )}
    </>
  )
}

/* ---------- layouts ---------- */

function Layouts({ deck, template, apply }) {
  return (
    <>
      <p className="hint">Replaces what is on the current slide. Undo puts it back.</p>
      <div className="layout-grid">
        {Object.entries(LAYOUTS).map(([name, build]) => (
          <button key={name} className="layout" onClick={() => apply(build(deck))}>
            <Slide slide={{ background: { color: 'bg' }, elements: build(deck) }}
                   template={template} />
            <span>{name}</span>
          </button>
        ))}
      </div>
    </>
  )
}

/* ---------- text & shapes ---------- */

function TextPanel({ insert }) {
  const items = [['heading', 'Heading', 30], ['subheading', 'Subheading', 21],
                 ['body', 'Body text', 16], ['bullets', 'Bullet list', 15],
                 ['caption', 'Caption', 12]]
  return (
    <div className="stack">
      {items.map(([key, label, px]) => (
        <button key={key} className="text-preset" onClick={() => insert(key)}
                style={{ fontSize: px, fontWeight: px > 24 ? 700 : 400 }}>{label}</button>
      ))}
    </div>
  )
}

function Shapes({ insert, template }) {
  const items = ['rect', 'ellipse', 'triangle', 'arrow', 'line']
  return (
    <div className="tile-grid">
      {items.map(k => (
        <button key={k} className="tile" title={k} onClick={() => insert(k)}>
          <span className={'shape-swatch s-' + k}
                style={{ background: k === 'line' ? 'none' : paint('accent', template),
                         borderColor: paint('accent', template) }} />
          <em>{k}</em>
        </button>
      ))}
    </div>
  )
}

function Charts({ insert }) {
  return (
    <div className="tile-grid">
      {['bar', 'line', 'area', 'pie', 'donut', 'scatter'].map(k => (
        <button key={k} className="tile" onClick={() => insert(chartPreset(k))}>
          <Icon name="chart" size={22} /><em>{k}</em>
        </button>
      ))}
      <button className="tile wide" onClick={() => insert('table')}>
        <Icon name="table" size={22} /><em>table</em>
      </button>
    </div>
  )
}

/* ---------- images ---------- */

/* Three ways in -- a file, an address, a stock search -- and one way out: every one of
   them ends as an image the server stored and can hand back later. The browser never
   keeps a picture the deck cannot resolve on its own tomorrow. */

function Images({ put, meta, replacing }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [mine, setMine] = useState([])          // { key, name, state, img, error, file }
  const [link, setLink] = useState('')
  const picker = useRef(null)
  const live = (meta?.providers || []).length > 0
  const track = (key, patch) =>
    setMine(v => v.map(m => (m.key === key ? { ...m, ...patch } : m)))

  async function run(e) {
    e?.preventDefault()
    if (!q.trim() || busy) return
    setBusy(true); setError(null); setHits([])
    try {
      setHits((await searchImages(q.trim())).images)
    } catch (err) {
      setError(String(err.message || err))
    } finally { setBusy(false) }
  }

  /** One upload, and the tile it lands in tells the truth the whole way: uploading,
   *  ready, or failed with the reason and a button that tries the same file again. */
  async function send(key, file) {
    track(key, { state: 'uploading', error: null })
    try {
      track(key, { state: 'ready', img: await uploadImage(file) })
    } catch (err) {
      track(key, { state: 'failed', error: String(err.message || err) })
    }
  }

  function take(files) {
    const added = [...files].map(f => ({ key: uid('up'), name: f.name, file: f,
                                         state: 'uploading' }))
    setMine(v => [...added, ...v])
    added.forEach(a => send(a.key, a.file))     // in parallel; each reports for itself
  }

  /** Paste an address, get the picture. The fetch happens on the server -- a remote URL
   *  expires, hotlinks or blocks by origin, and the deck must not depend on one. */
  async function fromLink(e) {
    e?.preventDefault()
    const url = link.trim()
    if (!url) return
    const key = uid('url')
    setMine(v => [{ key, name: url, state: 'uploading' }, ...v])
    setLink('')
    try {
      const img = await imageFromUrl(url)
      track(key, { state: 'ready', img })
      put(img)
    } catch (err) {
      track(key, { state: 'failed', error: String(err.message || err) })
    }
  }

  return (
    <>
      <p className="hint">
        {replacing
          ? 'Replacing the selected image — its size, position and styling stay put.'
          : 'Pick a picture and it lands on the slide. Select one first to replace it.'}
      </p>

      <button className="upload" onClick={() => picker.current?.click()}>
        <Icon name="clip" size={17} />Upload an image
      </button>
      <input type="file" ref={picker} accept="image/png,image/jpeg,image/gif,image/webp"
             multiple hidden onChange={e => { take(e.target.files); e.target.value = '' }} />

      <form className="search" onSubmit={fromLink}>
        <input value={link} onChange={e => setLink(e.target.value)} type="url"
               onKeyDown={e => e.stopPropagation()} placeholder="…or paste an image URL" />
        <button disabled={!link.trim()}>Fetch</button>
      </form>

      {mine.length > 0 && (
        <div className="shot-grid">
          {mine.map(m => m.state === 'ready' ? (
            <button key={m.key} className="shot" onClick={() => put(m.img)} title={m.name}>
              <img src={m.img.url} alt={m.img.alt || ''} />
            </button>
          ) : m.state === 'failed' ? (
            <div key={m.key} className="shot bad" title={m.error}>
              <span>{m.error}</span>
              {m.file && <button onClick={() => send(m.key, m.file)}>Retry</button>}
            </div>
          ) : (
            <div key={m.key} className="shot skeleton" aria-label={'Uploading ' + m.name} />
          ))}
        </div>
      )}

      <form className="search" onSubmit={run}>
        <input value={q} onChange={e => setQ(e.target.value)} disabled={!live}
               onKeyDown={e => e.stopPropagation()}
               placeholder={live ? 'Search stock photos' : 'No stock-photo key set'} />
        <button disabled={!live || busy || !q.trim()}>{busy ? '…' : 'Search'}</button>
      </form>

      {error && <div className="alert small"><Icon name="alert" size={16} /><div>{error}</div></div>}
      {busy && <p className="hint">Searching, ranking and downloading — a few seconds.</p>}

      <div className="shot-grid">
        {busy
          // A grid of the right shape beats an empty panel: the layout does not jump
          // when the results land.
          ? Array.from({ length: 6 }, (_, i) => <div key={i} className="shot skeleton" />)
          : hits.map(img => (
            <button key={img.url} className="shot" onClick={() => put(img)} title={img.credit}>
              <img src={img.url} alt={img.alt || ''} loading="lazy" />
              <span>{img.credit}</span>
            </button>
          ))}
      </div>
      {!live && (
        <p className="hint">Set <code>UNSPLASH_ACCESS_KEY</code>, <code>PEXELS_API_KEY</code> or{' '}
          <code>PIXABAY_API_KEY</code> and restart the backend to search stock photos.
          Uploads and image URLs work either way.</p>
      )}
    </>
  )
}

/* ---------- AI ---------- */

const SUGGESTIONS = [
  'Make this slide more visually balanced',
  'Rewrite this in half the words',
  'Turn this into a comparison table',
  'Add three supporting points',
  'Improve the visual hierarchy',
  'Make this deck suitable for investors',
]

function Assistant({ deckId, selection, slide, onDeck, hasKey }) {
  const [ask, setAsk] = useState('')
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState([])
  const area = useRef(null)

  useEffect(() => {
    const el = area.current
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 180) + 'px' }
  }, [ask])

  const scope = selection.elements?.length
    ? `${selection.elements.length} selected element${selection.elements.length > 1 ? 's' : ''}`
    : selection.slides?.length ? `slide “${slide?.name}”` : 'the whole deck'

  async function run() {
    if (!ask.trim() || busy) return
    const question = ask.trim()
    setBusy(true)
    setLog(v => [...v, { role: 'you', text: question }])
    setAsk('')
    try {
      const { deck, report } = await askAI(deckId, question, selection)
      onDeck(deck)
      setLog(v => [...v, {
        role: 'ai',
        text: report.summary || (report.applied ? 'Done.' : 'Nothing needed changing.'),
        applied: report.applied, rejected: report.rejected,
      }])
    } catch (e) {
      setLog(v => [...v, { role: 'error', text: String(e.message || e) }])
    } finally { setBusy(false) }
  }

  return (
    <div className="ai">
      <p className="hint">
        Editing <b>{scope}</b>. Select an element or a slide first to narrow it down.
      </p>

      <div className="ai-log">
        {log.map((m, i) => (
          <div key={i} className={'ai-msg ' + m.role}>
            <p>{m.text}</p>
            {m.rejected?.length > 0 && (
              <details>
                <summary>{m.rejected.length} change{m.rejected.length > 1 ? 's were' : ' was'} refused</summary>
                <ul>{m.rejected.map((r, j) => <li key={j}>{r}</li>)}</ul>
              </details>
            )}
          </div>
        ))}
        {busy && <div className="ai-msg ai working"><span className="spin" />Thinking</div>}
      </div>

      {!log.length && (
        <div className="seeds">
          {SUGGESTIONS.map(s => (
            <button key={s} className="seed" onClick={() => setAsk(s)}>{s}</button>
          ))}
        </div>
      )}

      <div className="ai-compose">
        <textarea ref={area} rows={2} value={ask} disabled={!hasKey}
                  placeholder={hasKey ? 'Ask for a change…' : 'Set GROQ_API_KEY to use AI editing'}
                  onChange={e => setAsk(e.target.value)}
                  onKeyDown={e => {
                    e.stopPropagation()
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); run() }
                  }} />
        <button className="go small" disabled={busy || !ask.trim() || !hasKey} onClick={run}>
          {busy ? <span className="spin" /> : <Icon name="right" size={17} />}
        </button>
      </div>
    </div>
  )
}
