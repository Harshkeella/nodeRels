/* The property inspector. Every control writes a patch through the same reducer the
 * canvas uses, so typing 4.25 into X and dragging to 4.25 produce the identical undo
 * step and the identical document.
 *
 * Controls are deliberately plain: number inputs, a native colour picker, real buttons.
 * A design tool is used with the keyboard as much as the mouse, and a custom widget that
 * cannot be tabbed into is a worse control than the browser's own.
 */
import { useState } from 'react'
import Icon, { IconButton } from './Icon'
import { paint } from './Slide'
import { alignPatch, bounds } from './doc'

const TOKENS = ['primary', 'accent', 'text', 'muted', 'bg']
const FONTS = ['Segoe UI', 'Arial', 'Georgia', 'Times New Roman', 'Verdana',
               'Trebuchet MS', 'Courier New', 'Tahoma', 'Calibri', 'Garamond']

/* ---------- control kit ---------- */

function Row({ label, children, wide }) {
  return (
    <label className={'row' + (wide ? ' wide' : '')}>
      <span>{label}</span>
      <div className="row-in">{children}</div>
    </label>
  )
}

function Num({ value, onChange, step = 0.1, min, max, suffix }) {
  const [draft, setDraft] = useState(null)
  const shown = draft ?? (value == null ? '' : +(+value).toFixed(3))
  return (
    <span className="num">
      <input type="number" value={shown} step={step} min={min} max={max}
             onChange={e => setDraft(e.target.value)}
             onBlur={e => { setDraft(null)
               const n = parseFloat(e.target.value)
               if (!Number.isNaN(n)) onChange(n) }}
             onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur()
                               e.stopPropagation() }} />
      {suffix && <i>{suffix}</i>}
    </span>
  )
}

function Swatches({ value, onChange, template, allowNone }) {
  const custom = value && !TOKENS.includes(value)
  return (
    <div className="swatches">
      {allowNone && (
        <button className={'sw none' + (value ? '' : ' on')} onClick={() => onChange(null)}
                title="None" aria-pressed={!value} />
      )}
      {TOKENS.map(t => (
        <button key={t} className={'sw' + (value === t ? ' on' : '')} title={t}
                aria-pressed={value === t} onClick={() => onChange(t)}
                style={{ background: paint(t, template) }} />
      ))}
      <label className={'sw custom' + (custom ? ' on' : '')} title="Custom colour"
             style={{ background: custom ? value : 'transparent' }}>
        <input type="color" value={custom ? value : '#888888'}
               onChange={e => onChange(e.target.value)} />
      </label>
    </div>
  )
}

const Seg = ({ options, value, onChange, labels }) => (
  <div className="seg" role="group">
    {options.map(o => (
      <button key={o} aria-pressed={value === o} onClick={() => onChange(o)} title={o}>
        {labels?.[o] ?? o}
      </button>
    ))}
  </div>
)

function Section({ title, children, right }) {
  return (
    <section className="insp-block">
      <h4>{title}{right}</h4>
      {children}
    </section>
  )
}

/* ---------- the panel ---------- */

export default function Inspector({
  deck, slide, slideIndex, selection, setSelection, dispatch, template, commit, templates,
}) {
  const els = slide.elements.filter(e => selection.includes(e.id))
  const one = els.length === 1 ? els[0] : null
  const box = bounds(els)
  const set = (patch, tag) =>
    dispatch({ type: 'patch', slide: slideIndex, ids: els.map(e => e.id), patch, tag, force: true })
  const style = (patch, tag) => set({ style: patch }, tag)
  // Mixed selections show the first element's value rather than blanking every field --
  // you can still see what you are about to overwrite.
  const S = k => one?.style?.[k] ?? els[0]?.style?.[k]
  const types = new Set(els.map(e => e.type))
  const textish = els.length && types.size === 1 && types.has('text')

  if (!els.length) return (
    <SlidePanel {...{ deck, slide, slideIndex, dispatch, template, templates, commit }} />
  )

  return (
    <div className="insp">
      <div className="insp-head">
        <b>{one ? one.type : `${els.length} elements`}</b>
        <div className="insp-head-acts">
          <IconButton icon={one?.locked ? 'lock' : 'unlock'}
                      label={one?.locked ? 'Unlock' : 'Lock'}
                      onClick={() => { set({ locked: !els[0].locked }); commit() }} />
          <IconButton icon={els[0].hidden ? 'hidden' : 'eye'}
                      label={els[0].hidden ? 'Show' : 'Hide'}
                      onClick={() => { set({ hidden: !els[0].hidden }); commit() }} />
        </div>
      </div>

      <Section title="Position">
        <div className="grid2">
          <Row label="X"><Num value={box.x} suffix="in" onChange={v => { set({ x: v }); commit() }} /></Row>
          <Row label="Y"><Num value={box.y} suffix="in" onChange={v => { set({ y: v }); commit() }} /></Row>
          <Row label="W"><Num value={box.w} suffix="in" min={0.05}
                              onChange={v => { set({ w: v }); commit() }} /></Row>
          <Row label="H"><Num value={box.h} suffix="in" min={0.05}
                              onChange={v => { set({ h: v }); commit() }} /></Row>
        </div>
        <Row label="Rotation">
          <Num value={one?.rotation ?? 0} step={1} min={-360} max={360} suffix="°"
               onChange={v => { set({ rotation: v }); commit() }} />
        </Row>
      </Section>

      <Section title={els.length > 1 ? 'Align & distribute' : 'Align to slide'}>
        <div className="align-grid">
          {[['left', 'Left'], ['center', 'Centre'], ['right', 'Right'],
            ['top', 'Top'], ['middle', 'Middle'], ['bottom', 'Bottom']].map(([how, label]) => (
            <button key={how} className="mini" title={label} onClick={() => {
              const p = alignPatch(els, how, deck)
              dispatch({ type: 'patch', slide: slideIndex, ids: Object.keys(p), force: true,
                         each: id => p[id] })
              commit()
            }}>{label}</button>
          ))}
          {els.length > 2 && ['distribute-h', 'distribute-v'].map(how => (
            <button key={how} className="mini" onClick={() => {
              const p = alignPatch(els, how, deck)
              dispatch({ type: 'patch', slide: slideIndex, ids: Object.keys(p), force: true,
                         each: id => p[id] })
              commit()
            }}>{how === 'distribute-h' ? 'Space across' : 'Space down'}</button>
          ))}
        </div>
      </Section>

      {textish && (
        <Section title="Typography">
          <Row label="Font" wide>
            <select value={S('family') || ''} onChange={e => { style({ family: e.target.value || null }); commit() }}>
              <option value="">Theme — {S('font') === 'display' ? 'display' : 'body'}</option>
              {FONTS.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          </Row>
          <Row label="Role">
            <Seg options={['display', 'body']} value={S('font') || 'body'}
                 onChange={v => { style({ font: v }); commit() }} />
          </Row>
          <div className="grid2">
            <Row label="Size"><Num value={S('size') ?? 18} step={1} min={4} max={400} suffix="pt"
                                   onChange={v => { style({ size: v }); commit() }} /></Row>
            <Row label="Line"><Num value={S('lineHeight') ?? 1.2} step={0.05} min={0.6} max={4}
                                   onChange={v => { style({ lineHeight: v }); commit() }} /></Row>
            <Row label="Tracking"><Num value={S('letterSpacing') ?? 0} step={0.25} suffix="pt"
                                       onChange={v => { style({ letterSpacing: v }); commit() }} /></Row>
            <Row label="Gap"><Num value={S('spaceAfter') ?? 0} step={2} min={0} max={96} suffix="pt"
                                  onChange={v => { style({ spaceAfter: v }); commit() }} /></Row>
          </div>
          <Row label="Style">
            <div className="seg">
              {[['bold', 'B'], ['italic', 'I'], ['underline', 'U']].map(([k, l]) => (
                <button key={k} aria-pressed={!!S(k)} title={k} className={'ff-' + k}
                        onClick={() => { style({ [k]: !S(k) }); commit() }}>{l}</button>
              ))}
            </div>
          </Row>
          <Row label="Align">
            <Seg options={['left', 'center', 'right', 'justify']} value={S('align') || 'left'}
                 labels={{ left: '⟵', center: '↔', right: '⟶', justify: '≡' }}
                 onChange={v => { style({ align: v }); commit() }} />
          </Row>
          <Row label="Vertical">
            <Seg options={['top', 'middle', 'bottom']} value={S('valign') || 'top'}
                 labels={{ top: '⌃', middle: '−', bottom: '⌄' }}
                 onChange={v => { style({ valign: v }); commit() }} />
          </Row>
          <Row label="Lists">
            <div className="seg">
              <button aria-pressed={!!S('bullets')}
                      onClick={() => { style({ bullets: !S('bullets'), numbered: false }); commit() }}>•</button>
              <button aria-pressed={!!S('numbered')}
                      onClick={() => { style({ numbered: !S('numbered'), bullets: false }); commit() }}>1.</button>
            </div>
          </Row>
          <Row label="Colour" wide>
            <Swatches value={S('color')} template={template}
                      onChange={v => { style({ color: v }); commit() }} />
          </Row>
        </Section>
      )}

      {(types.has('shape') || types.has('line') || types.has('table') || types.has('chart')) && (
        <Section title="Appearance">
          {types.has('shape') && (
            <Row label="Fill" wide>
              <Swatches value={S('fill')} template={template} allowNone
                        onChange={v => { style({ fill: v }); commit() }} />
            </Row>
          )}
          <Row label="Stroke" wide>
            <Swatches value={S('stroke')} template={template} allowNone
                      onChange={v => { style({ stroke: v }); commit() }} />
          </Row>
          <div className="grid2">
            <Row label="Width"><Num value={S('strokeWidth') ?? 0} step={0.01} min={0} max={1} suffix="in"
                                    onChange={v => { style({ strokeWidth: v }); commit() }} /></Row>
            <Row label="Radius"><Num value={S('radius') ?? 0} step={0.02} min={0} max={4} suffix="in"
                                     onChange={v => { style({ radius: v }); commit() }} /></Row>
          </div>
        </Section>
      )}

      <Section title="Effects">
        <Row label="Opacity" wide>
          <input type="range" min="0" max="1" step="0.01" value={S('opacity') ?? 1}
                 onChange={e => style({ opacity: +e.target.value }, 'opacity')}
                 onPointerUp={commit} onKeyUp={commit} />
          <span className="val">{Math.round((S('opacity') ?? 1) * 100)}%</span>
        </Row>
        <Row label="Shadow">
          <button className={'toggle' + (S('shadow') ? ' on' : '')} aria-pressed={!!S('shadow')}
                  onClick={() => { style({ shadow: !S('shadow') }); commit() }}>
            {S('shadow') ? 'On' : 'Off'}
          </button>
        </Row>
      </Section>

      {one?.type === 'image' && <ImageBits el={one} set={set} style={style} commit={commit} />}
      {one?.type === 'table' && <TableBits el={one} set={set} commit={commit} />}
      {one?.type === 'chart' && <ChartBits el={one} set={set} commit={commit} />}
      {one?.type === 'shape' && (
        <Section title="Shape">
          <Row label="Kind" wide>
            <select value={one.content.shape} onChange={e => {
              set({ content: { ...one.content, shape: e.target.value } }); commit()
            }}>
              {['rect', 'ellipse', 'triangle', 'arrow'].map(s => <option key={s}>{s}</option>)}
            </select>
          </Row>
        </Section>
      )}

      <Layers {...{ slide, slideIndex, selection, setSelection, dispatch, commit }} />
    </div>
  )
}

/* ---------- per-type extras ---------- */

function ImageBits({ el, set, style, commit }) {
  return (
    <Section title="Image">
      <Row label="Fit">
        <Seg options={['cover', 'contain']} value={el.style.fit || 'cover'}
             onChange={v => { style({ fit: v }); commit() }} />
      </Row>
      <Row label="Alt text" wide>
        <input value={el.content.alt || ''} placeholder="Describe the image"
               onKeyDown={e => e.stopPropagation()}
               onChange={e => set({ content: { ...el.content, alt: e.target.value } }, 'alt')}
               onBlur={commit} />
      </Row>
      {el.content.credit && (
        <p className="hint">
          {el.content.source_url
            ? <a href={el.content.source_url} target="_blank" rel="noreferrer">{el.content.credit}</a>
            : el.content.credit}
          {' — '}the licence needs this credit, so it is written into the speaker notes on export.
        </p>
      )}
    </Section>
  )
}

function TableBits({ el, set, commit }) {
  const rows = el.content.rows
  const resize = (dr, dc) => {
    const next = rows.map(r => r.slice())
    if (dc > 0) next.forEach(r => r.push(''))
    if (dc < 0 && next[0].length > 1) next.forEach(r => r.pop())
    if (dr > 0) next.push(new Array(next[0].length).fill(''))
    if (dr < 0 && next.length > 1) next.pop()
    set({ content: { ...el.content, rows: next } }); commit()
  }
  return (
    <Section title="Table">
      <Row label="Rows">
        <div className="seg"><button onClick={() => resize(-1, 0)}>−</button>
          <span className="val">{rows.length}</span>
          <button onClick={() => resize(1, 0)}>+</button></div>
      </Row>
      <Row label="Columns">
        <div className="seg"><button onClick={() => resize(0, -1)}>−</button>
          <span className="val">{rows[0].length}</span>
          <button onClick={() => resize(0, 1)}>+</button></div>
      </Row>
      <Row label="Header row">
        <button className={'toggle' + (el.content.header ? ' on' : '')}
                aria-pressed={!!el.content.header}
                onClick={() => { set({ content: { ...el.content, header: !el.content.header } }); commit() }}>
          {el.content.header ? 'On' : 'Off'}
        </button>
      </Row>
      <div className="cells">
        {rows.map((row, r) => (
          <div key={r} className="cells-row">
            {row.map((cell, c) => (
              <input key={c} value={cell} aria-label={`Row ${r + 1} column ${c + 1}`}
                     onKeyDown={e => e.stopPropagation()}
                     onChange={e => {
                       const next = rows.map(x => x.slice())
                       next[r][c] = e.target.value
                       set({ content: { ...el.content, rows: next } }, 'cell')
                     }} onBlur={commit} />
            ))}
          </div>
        ))}
      </div>
    </Section>
  )
}

function ChartBits({ el, set, commit }) {
  const c = el.content
  const put = patch => set({ content: { ...c, ...patch } }, 'chartdata')
  return (
    <Section title="Chart">
      <Row label="Type" wide>
        <select value={c.chart} onChange={e => { put({ chart: e.target.value }); commit() }}>
          {['bar', 'line', 'area', 'pie', 'donut', 'scatter'].map(k => <option key={k}>{k}</option>)}
        </select>
      </Row>
      <Row label="Legend">
        <button className={'toggle' + (c.legend ? ' on' : '')} aria-pressed={!!c.legend}
                onClick={() => { put({ legend: !c.legend }); commit() }}>{c.legend ? 'On' : 'Off'}</button>
      </Row>
      <Row label="Data labels">
        <button className={'toggle' + (c.labels ? ' on' : '')} aria-pressed={!!c.labels}
                onClick={() => { put({ labels: !c.labels }); commit() }}>{c.labels ? 'On' : 'Off'}</button>
      </Row>
      <div className="cells chart-data">
        <div className="cells-row">
          <input aria-label="Series header" value="" disabled placeholder="" />
          {c.categories.map((cat, i) => (
            <input key={i} value={cat} aria-label={'Category ' + (i + 1)}
                   onKeyDown={e => e.stopPropagation()}
                   onChange={e => {
                     const cats = c.categories.slice(); cats[i] = e.target.value
                     put({ categories: cats })
                   }} onBlur={commit} />
          ))}
        </div>
        {c.series.map((ser, si) => (
          <div key={si} className="cells-row">
            <input value={ser.name} aria-label={'Series ' + (si + 1) + ' name'}
                   onKeyDown={e => e.stopPropagation()}
                   onChange={e => {
                     const s = c.series.map(x => ({ ...x }))
                     s[si].name = e.target.value; put({ series: s })
                   }} onBlur={commit} />
            {c.categories.map((_, i) => (
              <input key={i} type="number" value={ser.values[i] ?? 0}
                     aria-label={`${ser.name} ${c.categories[i]}`}
                     onKeyDown={e => e.stopPropagation()}
                     onChange={e => {
                       const s = c.series.map(x => ({ ...x, values: x.values.slice() }))
                       s[si].values[i] = parseFloat(e.target.value) || 0
                       put({ series: s })
                     }} onBlur={commit} />
            ))}
          </div>
        ))}
      </div>
      <div className="align-grid">
        <button className="mini" onClick={() => {
          put({ categories: [...c.categories, 'New'],
                series: c.series.map(s => ({ ...s, values: [...s.values, 0] })) }); commit()
        }}>Add column</button>
        <button className="mini" onClick={() => {
          put({ series: [...c.series, { name: 'Series ' + (c.series.length + 1),
                                        values: c.categories.map(() => 0) }] }); commit()
        }}>Add series</button>
      </div>
    </Section>
  )
}

/* ---------- layers ---------- */

function Layers({ slide, slideIndex, selection, setSelection, dispatch, commit }) {
  const order = (to, ids) => { dispatch({ type: 'order', slide: slideIndex, ids, to }); commit() }
  const labelOf = e => e.type === 'text'
    ? (e.content.text || '').split('\n')[0].slice(0, 32) || 'Empty text'
    : e.type === 'chart' ? `${e.content.chart} chart`
    : e.type === 'shape' ? e.content.shape
    : e.type === 'image' ? (e.content.alt || 'Image') : e.type
  return (
    <Section title="Layers" right={
      <span className="layer-acts">
        {[['front', 'Bring to front'], ['forward', 'Forward'],
          ['backward', 'Backward'], ['back', 'Send to back']].map(([to, label]) => (
          <button key={to} className="mini" title={label} disabled={!selection.length}
                  onClick={() => order(to, selection)}>
            {{ front: '⤒', forward: '↑', backward: '↓', back: '⤓' }[to]}
          </button>
        ))}
      </span>}>
      <ul className="layers">
        {slide.elements.slice().reverse().map(e => (
          <li key={e.id} className={selection.includes(e.id) ? 'on' : ''}>
            <button className="layer-hit" onClick={ev =>
              setSelection(ev.shiftKey
                ? selection.includes(e.id) ? selection.filter(x => x !== e.id) : [...selection, e.id]
                : [e.id])}>
              <Icon name={{ text: 'notes', image: 'image', chart: 'chart', table: 'table',
                            shape: 'shape', line: 'shape' }[e.type] || 'shape'} size={15} />
              <span>{labelOf(e)}</span>
            </button>
            <IconButton icon={e.hidden ? 'hidden' : 'eye'} label={e.hidden ? 'Show' : 'Hide'}
                        onClick={() => { dispatch({ type: 'patch', slide: slideIndex, ids: [e.id],
                                                    force: true, patch: { hidden: !e.hidden } }); commit() }} />
            <IconButton icon={e.locked ? 'lock' : 'unlock'} label={e.locked ? 'Unlock' : 'Lock'}
                        onClick={() => { dispatch({ type: 'patch', slide: slideIndex, ids: [e.id],
                                                    force: true, patch: { locked: !e.locked } }); commit() }} />
          </li>
        ))}
        {!slide.elements.length && <li className="muted-row">Nothing on this slide yet.</li>}
      </ul>
    </Section>
  )
}

/* ---------- nothing selected: the slide itself ---------- */

function SlidePanel({ deck, slide, slideIndex, dispatch, template, templates, commit }) {
  const put = (patch, tag) => dispatch({ type: 'slidePatch', slide: slideIndex, patch, tag })
  return (
    <div className="insp">
      <div className="insp-head"><b>Slide {slideIndex + 1}</b></div>

      <Section title="Slide">
        <Row label="Name" wide>
          <input value={slide.name} onKeyDown={e => e.stopPropagation()}
                 onChange={e => put({ name: e.target.value }, 'name')} onBlur={commit} />
        </Row>
        <Row label="Background" wide>
          <Swatches value={slide.background?.color} template={template}
                    onChange={v => { put({ background: { color: v } }); commit() }} />
        </Row>
        <Row label="Hidden">
          <button className={'toggle' + (slide.hidden ? ' on' : '')} aria-pressed={!!slide.hidden}
                  onClick={() => { put({ hidden: !slide.hidden }); commit() }}>
            {slide.hidden ? 'Skipped' : 'Shown'}
          </button>
        </Row>
      </Section>

      <Section title="Speaker notes">
        <textarea className="notes-edit" rows={6} value={slide.notes || ''}
                  placeholder="What you say while this slide is up."
                  onKeyDown={e => e.stopPropagation()}
                  onChange={e => put({ notes: e.target.value }, 'notes')} onBlur={commit} />
      </Section>

      <Section title="Theme">
        <div className="theme-grid">
          {Object.entries(templates || {}).map(([name, t]) => (
            <button key={name} className={'theme' + (deck.template === name ? ' on' : '')}
                    aria-pressed={deck.template === name} title={t.use_for}
                    onClick={() => { dispatch({ type: 'deck', patch: { template: name } }); commit() }}>
              <span className="theme-chips">
                {['bg', 'primary', 'accent'].map(k => (
                  <i key={k} style={{ background: paint(k, t) }} />
                ))}
              </span>
              {name}
            </button>
          ))}
        </div>
        <p className="hint">
          Elements coloured with a theme token follow the theme. Anything you set to a
          specific colour stays put.
        </p>
      </Section>

      <Section title="Slide size">
        <div className="grid2">
          <Row label="Width"><Num value={deck.w} step={0.5} min={4} max={60} suffix="in"
                                  onChange={v => { dispatch({ type: 'deck', patch: { w: v } }); commit() }} /></Row>
          <Row label="Height"><Num value={deck.h} step={0.5} min={3} max={60} suffix="in"
                                   onChange={v => { dispatch({ type: 'deck', patch: { h: v } }); commit() }} /></Row>
        </div>
        <div className="align-grid">
          {[['16:9', 13.333, 7.5], ['4:3', 10, 7.5]].map(([label, w, h]) => (
            <button key={label} className={'mini' + (deck.w === w && deck.h === h ? ' on' : '')}
                    onClick={() => { dispatch({ type: 'deck', patch: { w, h } }); commit() }}>{label}</button>
          ))}
        </div>
      </Section>
    </div>
  )
}
