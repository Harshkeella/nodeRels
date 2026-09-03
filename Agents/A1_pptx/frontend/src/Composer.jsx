import { useEffect, useRef, useState } from 'react'
import Icon, { IconButton } from './Icon'
import { generate } from './api'

const SEEDS = [
  'Board update on Q3 — blunt, lead with churn',
  'Series A pitch, ten minutes, investor audience',
  'Postmortem for the auth outage, engineers',
]

/** Turn a raw error into something a person can act on. */
function explain(msg) {
  if (/401|invalid_api_key/.test(msg))
    return 'Groq rejected the key. Set GROQ_API_KEY and restart the backend.'
  if (/groq 413/.test(msg))
    return 'That request is bigger than your Groq tier allows in one minute. Trim the notes, '
      + 'or set GROQ_TPM to match your tier and restart the backend.'
  if (/429|rate limit/i.test(msg))
    return 'Groq’s per-minute token budget is spent. It already waited and retried once — '
      + 'give it another minute.'
  if (/Failed to fetch|NetworkError/i.test(msg))
    return 'The backend is not answering. Start it with: uvicorn backend.main:app --reload'
  return 'That did not work.'
}

export default function Composer({ meta, onDeck }) {
  const [prompt, setPrompt] = useState('')
  const [files, setFiles] = useState([])
  const [pick, setPick] = useState(null)
  const [images, setImages] = useState(true)
  const [busy, setBusy] = useState(false)
  const [steps, setSteps] = useState([])
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState(null)
  const [log, setLog] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [dragging, setDragging] = useState(false)
  const area = useRef(null)
  const picker = useRef(null)

  useEffect(() => {                                   // autogrow, without a layout library
    const el = area.current
    if (el) { el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px' }
  }, [prompt])
  useEffect(() => {
    if (!busy) return
    const t0 = Date.now()
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 500)
    return () => clearInterval(id)
  }, [busy])

  const budget = meta.source_budget || 7000
  const totalChars = files.reduce((n, f) => n + f.text.length, 0)
  const hasProviders = (meta.providers || []).length > 0

  const accept = list => {
    ;[...list].filter(f => /\.(md|txt|markdown)$/i.test(f.name)).forEach(f => {
      const r = new FileReader()
      r.onload = () => setFiles(v => [...v, { name: f.name, text: String(r.result) }])
      r.readAsText(f)
    })
  }

  async function run() {
    if (busy || !prompt.trim()) return
    setBusy(true); setError(null); setLog(''); setSteps([]); setProgress(null); setElapsed(0)
    try {
      await generate(
        { prompt: prompt.trim(), sources: files, template: pick, images },
        (name, data) => {
          if (name === 'status') {
            setSteps(v => [...v, data.text])
            if (data.total) setProgress({ done: data.done, total: data.total })
          } else if (name === 'plan') {
            onDeck(data.deck, false)          // slides appear before a single photo lands
          } else if (name === 'image') {
            setProgress({ done: data.done, total: data.total })
            // A photo re-lays its slide out, so the server sends the whole rebuilt slide
            // rather than an image to patch in -- layout is the server's to decide.
            onDeck(d => {
              if (!d || !data.slide) return d
              const slides = d.slides.slice()
              slides[data.index] = data.slide
              return { ...d, slides }
            }, false)
          } else if (name === 'done') {
            onDeck(data.deck, true)
            setLog(data.deck.log || '')
            setPrompt(''); setFiles([])
          } else if (name === 'error') {
            throw new Error(data.error)
          }
        })
    } catch (e) {
      setError(String(e.message || e))
      onDeck(null, true)
    } finally {
      setBusy(false); setSteps([]); setProgress(null)
    }
  }

  return (
    <section className={'composer' + (dragging ? ' dragging' : '')}
             onDragEnter={e => { e.preventDefault(); setDragging(true) }}
             onDragOver={e => { e.preventDefault(); setDragging(true) }}
             onDragLeave={e => { e.preventDefault(); if (e.currentTarget === e.target) setDragging(false) }}
             onDrop={e => { e.preventDefault(); setDragging(false); accept(e.dataTransfer.files) }}>
      <h1>Tell me what deck you need.</h1>
      <label className="sr" htmlFor="prompt">What the deck should do</label>
      <textarea id="prompt" ref={area} rows={2} value={prompt}
                onChange={e => setPrompt(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) run() }}
                placeholder="Board update on Q3 — blunt, lead with the churn number, ten minutes." />

      {files.length > 0 && (
        <div className="files">
          {files.map((f, i) => (
            <div className="chip" key={i}>
              <div>{f.name}</div>
              <span>{(f.text.length / 1000).toFixed(1)}k</span>
              <IconButton icon="x" label={'Remove ' + f.name}
                          onClick={() => setFiles(v => v.filter((_, j) => j !== i))} />
            </div>
          ))}
          {totalChars > budget && (
            <div className="overflow">
              {(totalChars / 1000).toFixed(1)}k of notes — only the first{' '}
              {(budget / 1000).toFixed(1)}k fits your Groq per-minute budget. The rest is
              trimmed before sending.
            </div>
          )}
        </div>
      )}

      <div className="bar">
        <button className="attach" onClick={() => picker.current?.click()}>
          <Icon name="clip" size={19} />Add .md or .txt
        </button>
        <input type="file" ref={picker} accept=".md,.txt,.markdown" multiple hidden
               onChange={e => { accept(e.target.files); e.target.value = '' }} />

        <div className="pills">
          <button className="pill toggle" aria-pressed={images && hasProviders} disabled={!hasProviders}
                  title={hasProviders
                    ? 'Find a stock photo for every slide that has room for one'
                    : 'Set UNSPLASH_ACCESS_KEY, PEXELS_API_KEY or PIXABAY_API_KEY to enable photos'}
                  onClick={() => setImages(v => !v)}
                  style={hasProviders ? undefined : { opacity: .4, cursor: 'not-allowed' }}>
            Photos
          </button>
          <div className="sep" />
          {[['auto', null], ...Object.keys(meta.templates).map(k => [k, k])].map(([label, val]) => (
            <button className="pill" key={label} aria-pressed={pick === val}
                    title={val ? meta.templates[val].use_for : 'Let the model choose the template'}
                    onClick={() => setPick(val)}>
              {val && <i style={{ background: '#' + meta.templates[val].accent }} />}
              {label}
            </button>
          ))}
        </div>

        <button className={'go' + (busy ? ' busy' : '')} disabled={busy || !prompt.trim()}
                onClick={run}>
          {busy
            ? <><span className="spin" />Writing<span className="elapsed"> {elapsed}s</span></>
            : <>Generate<Icon name="right" size={18} /></>}
        </button>
      </div>

      {busy && steps.length > 0 && (
        <div className="run">
          <div className="steps">
            {steps.map((s, i) => (
              <div className={'step ' + (i === steps.length - 1 ? 'on' : 'past')} key={i}>
                <span className="tick"><span className="bead" /></span>
                <span>{s}</span>
                {i === steps.length - 1 && progress?.total > 0 &&
                  <span className="detail">{progress.done} / {progress.total}</span>}
              </div>
            ))}
          </div>
          {progress?.total > 0 && (
            <div className="track">
              <i style={{ width: (progress.done / progress.total) * 100 + '%' }} />
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="alert">
          <Icon name="alert" size={18} />
          <div>{explain(error)}<code>{error}</code></div>
        </div>
      )}

      {log && !busy && (
        <div className="note"><b>Checker stepped in.</b> {log.replace(/\s+/g, ' ')}</div>
      )}

      {!prompt && !busy && !error && (
        <div className="seeds" style={{ marginTop: 18, justifyContent: 'flex-start' }}>
          {SEEDS.map(s => (
            <button className="seed" key={s} onClick={() => setPrompt(s)}>{s}</button>
          ))}
        </div>
      )}
    </section>
  )
}
