/* The studio. Slide rail on the left, canvas in the middle, inspector on the right, and
 * one reducer under all three.
 *
 * The split that matters: `state.deck` is the document and lives in doc.js; everything
 * else here (which slide, what is selected, which panel is open, zoom) is UI state that
 * is deliberately NOT in the document, so opening a panel is not an undo step and two
 * people editing the same deck later will not fight over each other's scroll position.
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import Canvas from './Canvas'
import Inspector from './Inspector'
import Panels from './Panels'
import Present from './Present'
import Slide, { setGeometry } from './Slide'
import Icon, { IconButton } from './Icon'
import { blankSlide, copyElement, copySlide, initial, makeElement, PRESETS, reducer } from './doc'
import { createVideo, downloadUrl, getVideo, saveDeck } from './api'

const AUTOSAVE = 1200        // ms of quiet before the document goes to the server
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2]

export default function Editor({ deck: initialDeck, meta, onClose, onSaved }) {
  const [state, dispatch] = useReducer(reducer, initialDeck, initial)
  const { deck } = state
  const [index, setIndex] = useState(0)
  const [selection, setSelection] = useState([])
  const [editing, setEditing] = useState(null)
  const [tab, setTab] = useState(null)
  const [zoom, setZoom] = useState(1)
  const [grid, setGrid] = useState(false)
  const [present, setPresent] = useState(false)
  const [workspace, setWorkspace] = useState('canvas')
  const [status, setStatus] = useState({ state: 'saved', at: Date.now() })
  const clip = useRef({ elements: [], slides: [] })
  const railRef = useRef(null)

  const slide = deck.slides[Math.min(index, deck.slides.length - 1)]
  const template = meta.templates[deck.template] || Object.values(meta.templates)[0]
  useEffect(() => { setGeometry({ w: deck.w, h: deck.h }) }, [deck.w, deck.h])

  // Selection is per-slide; carrying ids across a slide change selects nothing visible.
  useEffect(() => { setSelection([]); setEditing(null) }, [slide?.id])
  useEffect(() => {
    setSelection(s => s.filter(id => slide?.elements.some(e => e.id === id)))
  }, [slide])

  /* ---------- autosave ---------- */

  const dirtyAt = state.at
  useEffect(() => {
    if (!state.dirty) return
    const stamp = dirtyAt
    const id = setTimeout(async () => {
      setStatus({ state: 'saving', at: Date.now() })
      try {
        const saved = await saveDeck(deck.id, deck)
        dispatch({ type: 'saved', at: stamp })
        setStatus({ state: 'saved', at: Date.now() })
        onSaved?.(saved)
      } catch (e) {
        // Never say "saved" when it is not. The work is still in memory and the next
        // edit retries, so this is a warning, not a loss.
        setStatus({ state: 'error', at: Date.now(), message: String(e.message || e) })
      }
    }, AUTOSAVE)
    return () => clearTimeout(id)
  }, [state.dirty, dirtyAt, deck])

  // The browser's own "you have unsaved changes" prompt, which is the only one that
  // can stop a tab closing over the top of an in-flight save.
  useEffect(() => {
    const warn = e => { if (state.dirty) { e.preventDefault(); e.returnValue = '' } }
    addEventListener('beforeunload', warn)
    return () => removeEventListener('beforeunload', warn)
  }, [state.dirty])

  const commit = useCallback(() => dispatch({ type: 'commit' }), [])
  const flush = useCallback(async () => {
    if (!state.dirty) return true
    setStatus({ state: 'saving', at: Date.now() })
    try {
      const saved = await saveDeck(deck.id, deck)
      dispatch({ type: 'saved', at: state.at })
      setStatus({ state: 'saved', at: Date.now() })
      onSaved?.(saved)
      return true
    } catch (e) { setStatus({ state: 'error', at: Date.now(), message: String(e.message || e) }); return false }
  }, [deck, state.dirty, state.at, onSaved])

  /* ---------- element and slide actions ---------- */

  const insert = useCallback(preset => {
    const el = makeElement(preset, deck)
    dispatch({ type: 'add', slide: index, elements: [el] })
    setSelection([el.id])
    if (el.type === 'text') setEditing(el.id)
  }, [deck, index])

  /* Replacing a picture keeps the box. Size, position, rotation, layer and styling are
     layout decisions the user made; swapping the photo inside them is not a request to
     throw them away. With nothing selected this inserts instead. */
  const putImage = useCallback(img => {
    const content = { url: img.url, path: img.path, alt: img.alt || '',
                      credit: img.credit ?? null, source_url: img.source_url ?? null }
    const target = slide.elements.find(e => e.id === selection[0] && e.type === 'image')
    if (target) {
      dispatch({ type: 'patch', slide: index, ids: [target.id], patch: { content } })
      commit()
      return
    }
    const el = makeElement({ ...PRESETS.image, w: 4.5, h: 3.4, content }, deck)
    dispatch({ type: 'add', slide: index, elements: [el] })
    setSelection([el.id])
  }, [slide, selection, index, deck, commit])

  const applyLayout = useCallback(elements => {
    dispatch({ type: 'slidePatch', slide: index, patch: { elements } })
    setSelection([])
  }, [index])

  const duplicate = useCallback(() => {
    if (!selection.length) return
    const copies = slide.elements.filter(e => selection.includes(e.id)).map(e => copyElement(e))
    dispatch({ type: 'add', slide: index, elements: copies })
    setSelection(copies.map(c => c.id))
  }, [selection, slide, index])

  const remove = useCallback(() => {
    if (!selection.length) return
    dispatch({ type: 'remove', slide: index, ids: selection })
    setSelection([])
  }, [selection, index])

  const addSlide = useCallback((at = index + 1, slides = [blankSlide()]) => {
    dispatch({ type: 'slideAdd', at, slides })
    setIndex(at)
  }, [index])

  const move = useCallback((from, to) => {
    dispatch({ type: 'slideMove', from, to })
    setIndex(Math.max(0, Math.min(deck.slides.length - 1, to)))
  }, [deck.slides.length])

  /* ---------- keyboard ---------- */

  useEffect(() => {
    const onKey = e => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); flush(); return }
      if (typing) return

      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        dispatch({ type: e.shiftKey ? 'redo' : 'undo' })
      } else if (mod && e.key.toLowerCase() === 'y') {
        e.preventDefault(); dispatch({ type: 'redo' })
      } else if (mod && e.key.toLowerCase() === 'a') {
        e.preventDefault(); setSelection(slide.elements.filter(x => !x.locked).map(x => x.id))
      } else if (mod && e.key.toLowerCase() === 'd') {
        e.preventDefault(); duplicate()
      } else if (mod && e.key.toLowerCase() === 'c') {
        clip.current.elements = slide.elements.filter(x => selection.includes(x.id))
      } else if (mod && e.key.toLowerCase() === 'x') {
        clip.current.elements = slide.elements.filter(x => selection.includes(x.id))
        remove()
      } else if (mod && e.key.toLowerCase() === 'v') {
        const copies = clip.current.elements.map(x => copyElement(x))
        if (!copies.length) return
        dispatch({ type: 'add', slide: index, elements: copies })
        setSelection(copies.map(c => c.id))
      } else if (e.key === 'F5' || ((e.key === 'f' || e.key === 'F') && !mod)) {
        e.preventDefault(); setPresent(true)
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault(); remove()
      } else if (e.key === 'Escape') {
        setEditing(null); selection.length ? setSelection([]) : setTab(null)
      } else if (e.key === 'Enter' && selection.length === 1) {
        const el = slide.elements.find(x => x.id === selection[0])
        if (el?.type === 'text') { e.preventDefault(); setEditing(el.id) }
      } else if (e.key.startsWith('Arrow')) {
        if (!selection.length) {
          if (e.key === 'ArrowDown') setIndex(i => Math.min(deck.slides.length - 1, i + 1))
          if (e.key === 'ArrowUp') setIndex(i => Math.max(0, i - 1))
          return
        }
        e.preventDefault()
        const step = e.shiftKey ? 0.5 : 0.05
        const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0],
                    ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key]
        if (!d) return
        const by = Object.fromEntries(slide.elements
          .filter(x => selection.includes(x.id))
          .map(x => [x.id, { x: +(x.x + d[0]).toFixed(3), y: +(x.y + d[1]).toFixed(3) }]))
        dispatch({ type: 'patch', slide: index, ids: selection, tag: 'nudge', each: id => by[id] })
      }
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [slide, selection, index, deck.slides.length, duplicate, remove, flush])

  useEffect(() => {
    railRef.current?.children[index]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [index])

  const chosen = useMemo(
    () => slide.elements.filter(e => selection.includes(e.id)), [slide, selection])

  if (present) {
    return <Present deck={deck} template={template} from={index}
                    onExit={i => { setPresent(false); setIndex(i) }} />
  }

  return (
    <div className="studio">
      <header className="top">
        <button className="back" onClick={async () => { if (await flush()) onClose() }}>
          <Icon name="left" size={17} />Decks
        </button>
        <input className="deck-name" value={deck.deck_title} aria-label="Presentation name"
               onChange={e => dispatch({ type: 'deck', patch: { deck_title: e.target.value }, tag: 'title' })}
               onBlur={commit} onKeyDown={e => e.stopPropagation()} />

        <Saved status={status} dirty={state.dirty} onRetry={flush} />

        <div className="top-acts">
          <IconButton icon="undo" label="Undo (Ctrl+Z)" disabled={!state.past.length}
                      onClick={() => dispatch({ type: 'undo' })} />
          <IconButton icon="redo" label="Redo (Ctrl+Shift+Z)" disabled={!state.future.length}
                      onClick={() => dispatch({ type: 'redo' })} />
          <div className="sep" />
          <IconButton icon="play" label="Present (F)" onClick={() => setPresent(true)} />
          {!meta.integrated && <button className="dl video-open" onClick={async () => { if (await flush()) setWorkspace('video') }}>
            <Icon name="play" size={15} />Video
          </button>}
          <a className="dl" href={downloadUrl(deck.id)} download
             onClick={async e => {
               // The .pptx is rebuilt on save, so an unsaved edit must land first or the
               // file that downloads is one edit behind what is on screen.
               if (state.dirty) { e.preventDefault(); if (await flush()) location.href = downloadUrl(deck.id) }
             }}>
            <Icon name="dl" size={17} />Export .pptx
          </a>
        </div>
      </header>

      {workspace === 'canvas' && <Toolbar {...{ chosen, slide, index, dispatch, commit,
        template, selection, insert, duplicate, remove, setEditing }} />}

      <div className="studio-body">
        <SlideRail {...{ deck, template, index, setIndex, dispatch, addSlide, move,
                         commit, clip, railRef }} />

        <main className={'stage-wrap ' + workspace}>
          <nav className="workspace-tabs" aria-label="Presentation workspace">
            <button className={workspace === 'canvas' ? 'on' : ''}
                    onClick={() => setWorkspace('canvas')}>Canvas</button>
            {!meta.integrated && <button className={workspace === 'video' ? 'on' : ''}
                    onClick={async () => { if (await flush()) setWorkspace('video') }}>Video</button>}
          </nav>
          {workspace === 'canvas' ? <>
            <Canvas slide={slide} template={template} zoom={zoom} grid={grid}
                    selection={selection} setSelection={setSelection} dispatch={dispatch}
                    slideIndex={index} editing={editing} setEditing={setEditing}
                    onCommit={commit} />
            <footer className="statusbar">
            <div className="zoom">
              <IconButton icon="minus" label="Zoom out"
                          onClick={() => setZoom(z => ZOOMS[Math.max(0, ZOOMS.indexOf(z) - 1)] ?? 0.5)} />
              <button className="mini" onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
              <IconButton icon="plus" label="Zoom in"
                          onClick={() => setZoom(z => ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(z) + 1)] ?? 2)} />
            </div>
            <button className={'mini' + (grid ? ' on' : '')} aria-pressed={grid}
                    onClick={() => setGrid(g => !g)}>Grid</button>
            <span className="spacer" />
            <span className="count">
              {chosen.length ? `${chosen.length} selected · ` : ''}
              Slide {index + 1} of {deck.slides.length}
            </span>
            </footer>
          </> : <VideoWorkspace deck={deck} enabled={meta.video?.available !== false}
                                      missing={meta.video?.missing || []} />}
        </main>

        {workspace === 'canvas' && <Panels tab={tab} setTab={setTab} deck={deck} template={template}
                insert={insert} applyLayout={applyLayout} meta={meta}
                putImage={putImage}
                replacing={chosen.length === 1 && chosen[0].type === 'image'}
                ai={{
                  deckId: deck.id, hasKey: meta.key, slide,
                  selection: selection.length ? { elements: selection }
                                              : { slides: [slide.id] },
                  onDeck: d => { dispatch({ type: 'replace', deck: d }); setSelection([]) },
                }} />}

        {workspace === 'canvas' && <aside className="inspector">
          <Inspector deck={deck} slide={slide} slideIndex={index} selection={selection}
                     setSelection={setSelection} dispatch={dispatch} template={template}
                     templates={meta.templates} commit={commit} />
        </aside>}
      </div>
    </div>
  )
}

function VideoWorkspace({ deck, enabled, missing }) {
  const [job, setJob] = useState({ state: 'loading', percent: 0, text: 'Checking video' })
  const [speed, setSpeed] = useState(1)
  const player = useRef(null)

  useEffect(() => {
    let alive = true
    getVideo(deck.id).then(x => alive && setJob(x))
      .catch(e => alive && setJob({ state: 'error', text: String(e.message || e) }))
    return () => { alive = false }
  }, [deck.id])

  useEffect(() => {
    if (job.state !== 'working') return
    const id = setInterval(() => getVideo(deck.id).then(setJob)
      .catch(e => setJob({ state: 'error', text: String(e.message || e) })), 1200)
    return () => clearInterval(id)
  }, [deck.id, job.state])

  async function generate() {
    setJob({ state: 'working', percent: 1, text: 'Preparing the video' })
    try { setJob(await createVideo(deck.id)) }
    catch (e) { setJob({ state: 'error', percent: 0, text: String(e.message || e) }) }
  }

  const ready = job.state === 'ready' && job.url
  return (
    <section className="video-workspace" aria-live="polite">
      <div className="video-head">
        <div><h2>Explanation video</h2>
          <p>The narrator explains one point at a time while that point is highlighted.</p></div>
        {ready && <button className="mini" onClick={generate}>Regenerate</button>}
      </div>

      {ready ? <>
        <div className="video-stage">
          <video ref={player} controls playsInline preload="metadata"
                 src={`${job.url}?v=${job.updated || 0}`} onLoadedMetadata={e => {
                   e.currentTarget.playbackRate = speed
                 }} />
        </div>
        <div className="video-controls">
          <label>Speed
            <select value={speed} onChange={e => {
              const next = +e.target.value; setSpeed(next)
              if (player.current) player.current.playbackRate = next
            }}>
              {[0.75, 1, 1.25, 1.5, 2].map(n => <option key={n} value={n}>{n}×</option>)}
            </select>
          </label>
          <button className="mini" onClick={() => player.current?.requestFullscreen?.()}>Full screen</button>
          <a className="dl" href={job.url} download={`${deck.id}.mp4`}>
            <Icon name="dl" size={16} />Download MP4
          </a>
        </div>
      </> : job.state === 'working' ? (
        <div className="video-progress">
          <span className="spin" /><h3>{job.text}</h3>
          <div className="track"><i style={{ width: `${job.percent || 1}%` }} /></div>
          <p>{job.percent || 1}% · You can keep editing after generation finishes.</p>
        </div>
      ) : (
        <div className="video-empty">
          <Icon name="play" size={36} />
          <h3>Turn this deck into a guided explanation</h3>
          <p>Each spoken point gets a visible focus highlight. The finished MP4 stays with this deck.</p>
          {!enabled && <div className="alert small"><Icon name="alert" size={16} />
            <div>Install the video tools, then restart the backend.
              <code>pip install -r requirements.txt</code>
              {missing.length > 0 && <span>{missing.join(', ')}</span>}
            </div></div>}
          {job.state === 'error' && <div className="alert small"><Icon name="alert" size={16} />
            <div>{job.text}</div></div>}
          <button className="go" disabled={!enabled} onClick={generate}>
            <Icon name="play" size={16} />Generate video
          </button>
        </div>
      )}
    </section>
  )
}

/* ---------- save indicator ---------- */

function Saved({ status, dirty, onRetry }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => tick(n => n + 1), 10000)
    return () => clearInterval(id)
  }, [])
  if (status.state === 'error') {
    return (
      <button className="saved bad" onClick={onRetry} title={status.message}>
        <Icon name="alert" size={15} />Not saved — retry
      </button>
    )
  }
  const secs = Math.round((Date.now() - status.at) / 1000)
  return (
    <span className={'saved' + (dirty ? ' pending' : '')}>
      {status.state === 'saving' ? 'Saving…'
        : dirty ? 'Unsaved changes'
        : secs < 8 ? 'Saved'
        : secs < 60 ? `Saved ${secs}s ago`
        : `Saved ${Math.round(secs / 60)}m ago`}
    </span>
  )
}

/* ---------- contextual toolbar ---------- */

function Toolbar({ chosen, slide, index, dispatch, commit, template, selection, insert,
                   duplicate, remove, setEditing }) {
  const style = patch => {
    dispatch({ type: 'patch', slide: index, ids: selection, patch: { style: patch }, force: true })
    commit()
  }
  const S = k => chosen[0]?.style?.[k]
  const types = new Set(chosen.map(c => c.type))
  const text = chosen.length && types.size === 1 && types.has('text')

  return (
    <div className="toolbar" role="toolbar" aria-label="Formatting">
      {!chosen.length && (
        <>
          <button className="tb" onClick={() => insert('heading')}><Icon name="type" size={16} />Text</button>
          <button className="tb" onClick={() => insert('rect')}><Icon name="shape" size={16} />Shape</button>
          <button className="tb" onClick={() => insert('table')}><Icon name="table" size={16} />Table</button>
          <span className="tb-hint">Nothing selected — the inspector is showing this slide.</span>
        </>
      )}

      {text && (
        <>
          <select aria-label="Font size" className="tb-num" value={S('size') ?? 18}
                  onChange={e => style({ size: +e.target.value })}>
            {[10, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 60, 72, 90].map(n =>
              <option key={n} value={n}>{n}</option>)}
          </select>
          <div className="seg">
            {[['bold', 'B'], ['italic', 'I'], ['underline', 'U']].map(([k, l]) => (
              <button key={k} className={'ff-' + k} aria-pressed={!!S(k)} title={k}
                      onClick={() => style({ [k]: !S(k) })}>{l}</button>
            ))}
          </div>
          <div className="seg">
            {[['left', '⟵'], ['center', '↔'], ['right', '⟶']].map(([k, l]) => (
              <button key={k} aria-pressed={(S('align') || 'left') === k} title={'Align ' + k}
                      onClick={() => style({ align: k })}>{l}</button>
            ))}
          </div>
          <div className="seg">
            <button aria-pressed={!!S('bullets')} title="Bullets"
                    onClick={() => style({ bullets: !S('bullets'), numbered: false })}>•</button>
            <button aria-pressed={!!S('numbered')} title="Numbering"
                    onClick={() => style({ numbered: !S('numbered'), bullets: false })}>1.</button>
          </div>
          {chosen.length === 1 && (
            <button className="tb" onClick={() => setEditing(chosen[0].id)}>Edit text</button>
          )}
        </>
      )}

      {chosen.length > 0 && (
        <>
          <span className="spacer" />
          <button className="tb" onClick={duplicate}><Icon name="copy" size={15} />Duplicate</button>
          <button className="tb danger" onClick={remove}><Icon name="trash" size={15} />Delete</button>
        </>
      )}
    </div>
  )
}

/* ---------- slide rail ---------- */

function SlideRail({ deck, template, index, setIndex, dispatch, addSlide, move, commit,
                     clip, railRef }) {
  const [drag, setDrag] = useState(null)
  const [menu, setMenu] = useState(null)
  useEffect(() => {
    if (menu == null) return
    const close = () => setMenu(null)
    addEventListener('pointerdown', close)
    return () => removeEventListener('pointerdown', close)
  }, [menu])

  return (
    <aside className="slides" aria-label="Slides">
      <div className="slides-list" ref={railRef}>
        {deck.slides.map((s, i) => (
          <div key={s.id}
               className={'slide-card' + (i === index ? ' on' : '') + (s.hidden ? ' skipped' : '')
                          + (drag?.over === i ? ' drop' : '')}
               draggable onDragStart={() => setDrag({ from: i })}
               onDragOver={e => { e.preventDefault(); setDrag(d => ({ ...d, over: i })) }}
               onDragEnd={() => setDrag(null)}
               onDrop={e => { e.preventDefault(); if (drag) move(drag.from, i); setDrag(null) }}>
            <button className="slide-hit" onClick={() => setIndex(i)}
                    aria-current={i === index} aria-label={`Slide ${i + 1}: ${s.name}`}>
              <span className="n">{i + 1}</span>
              <Slide slide={s} template={template} />
            </button>
            <IconButton icon="more" label={`Options for slide ${i + 1}`}
                        onClick={() => setMenu(menu === i ? null : i)} />
            {menu === i && (
              <div className="menu" onPointerDown={e => e.stopPropagation()}>
                <button onClick={() => { addSlide(i + 1, [copySlide(s)]); setMenu(null) }}>Duplicate</button>
                <button onClick={() => { clip.current.slides = [s]; setMenu(null) }}>Copy</button>
                <button disabled={!clip.current.slides?.length}
                        onClick={() => { addSlide(i + 1, clip.current.slides.map(copySlide)); setMenu(null) }}>
                  Paste after
                </button>
                <button onClick={() => {
                  dispatch({ type: 'slidePatch', slide: i, patch: { hidden: !s.hidden } })
                  commit(); setMenu(null)
                }}>{s.hidden ? 'Show in deck' : 'Skip in deck'}</button>
                <button className="danger" disabled={deck.slides.length < 2}
                        onClick={() => { dispatch({ type: 'slideRemove', ids: [s.id] })
                                         setIndex(Math.max(0, i - 1)); setMenu(null) }}>
                  Delete
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
      <button className="add-slide" onClick={() => addSlide()}>
        <Icon name="plus" size={16} />New slide
      </button>
    </aside>
  )
}
