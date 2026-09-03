import { useEffect, useState } from 'react'
import Composer from './Composer'
import Editor from './Editor'
import Slide, { setGeometry } from './Slide'
import Icon, { IconButton } from './Icon'
import { createDeck, deleteDeck, downloadUrl, getDecks, getMeta } from './api'
import { connection } from './api'
import ConnectedStudio from './ConnectedStudio'

const ago = ts => {
  const m = (Date.now() / 1000 - ts) / 60
  if (m < 1) return 'just now'
  if (m < 60) return `${m | 0}m ago`
  if (m < 1440) return `${(m / 60) | 0}h ago`
  return `${(m / 1440) | 0}d ago`
}

function Card({ deck, template, fresh, live, onOpen, onDelete }) {
  const shot = deck.slides.reduce(
    (n, s) => n + s.elements.filter(e => e.type === 'image' && e.content.url).length, 0)
  return (
    <div className={'card' + (fresh ? ' deal' : '')}>
      <button className="card-hit" onClick={onOpen}
              aria-label={`Edit ${deck.deck_title}, ${deck.slides.length} slides`}>
        <Slide slide={deck.slides[0]} template={template} />
      </button>
      <div className="card-body">
        <div>
          <h3>{deck.deck_title}</h3>
          <p>
            {deck.slides.length} slides · {deck.template}
            {shot > 0 && ` · ${shot} photo${shot > 1 ? 's' : ''}`}
            {' · '}{live ? 'assembling…' : ago(deck.updated || deck.created)}
          </p>
        </div>
        {!live && (
          <div className="card-acts">
            <IconButton icon="dl" label={'Download ' + deck.deck_title}
                        onClick={() => { location.href = downloadUrl(deck.id) }} />
            <IconButton icon="trash" label={'Delete ' + deck.deck_title}
                        className="danger" onClick={onDelete} />
          </div>
        )}
      </div>
    </div>
  )
}

export default function App() {
  return connection ? <ConnectedStudio /> : <StandaloneStudio />
}

function StandaloneStudio() {
  const [meta, setMeta] = useState(null)
  const [decks, setDecks] = useState([])
  const [draft, setDraft] = useState(null)     // the deck currently being built
  const [freshId, setFreshId] = useState(null)
  const [open, setOpen] = useState(null)       // deck id being edited

  useEffect(() => {
    getMeta().then(m => { setGeometry(m.doc); setMeta(m) })
      .catch(() => setMeta({ templates: {}, key: false, providers: [], offline: true }))
    getDecks().then(setDecks).catch(() => {})
  }, [])

  // Composer sends the plan first, then each slide as its photo lands, then the deck.
  const onDeck = (next, final) => {
    if (final) {
      setDraft(null)
      if (next) {
        setDecks(d => [next, ...d.filter(x => x.id !== next.id)])
        setFreshId(next.id)
        setOpen(next.id)                       // straight into the editor, as promised
      }
      return
    }
    setDraft(prev => (typeof next === 'function' ? next(prev) : next))
  }

  async function remove(deck) {
    if (!confirm(`Delete “${deck.deck_title}”? The .pptx goes with it.`)) return
    await deleteDeck(deck.id)
    setDecks(d => d.filter(x => x.id !== deck.id))
    if (open === deck.id) setOpen(null)
  }

  /** A deck with nothing in it. The server mints the id, because autosave and AI both
   *  address a deck by an id the server already knows. */
  const [blankError, setBlankError] = useState(null)
  async function blank() {
    try {
      const fresh = await createDeck('Untitled deck', Object.keys(meta.templates)[0])
      setDecks(d => [fresh, ...d])
      setOpen(fresh.id)
    } catch (e) { setBlankError(String(e.message || e)) }
  }

  if (!meta) return null                       // one paint, not a skeleton that flashes

  const editing = decks.find(d => d.id === open)
  if (editing) {
    return (
      <Editor key={editing.id} deck={editing} meta={meta} onClose={() => setOpen(null)}
              onSaved={saved => setDecks(d => d.map(x => (x.id === saved.id ? saved : x)))} />
    )
  }

  const shown = draft ? [draft, ...decks.filter(d => d.id !== draft.id)] : decks
  const status = meta.offline ? 'backend not running'
    : !meta.key ? 'no GROQ_API_KEY — editing only'
    : `${Object.keys(meta.templates).length} templates` +
      (meta.providers.length ? ` · ${meta.providers.join(', ')}` : ' · no photo keys')

  return (
    <div className="wrap">
      <header>
        <div className="mark">Deck <em>Studio</em></div>
        <div className="status">
          <span className={'dot' + (meta.key && !meta.offline ? '' : ' off')} />
          <span>{status}</span>
        </div>
      </header>

      {!meta.offline && <Composer meta={meta} onDeck={onDeck} />}
      {meta.offline && (
        <section className="composer">
          <h1>The backend is not running.</h1>
          <div className="alert">
            <Icon name="alert" size={18} />
            <div>Start it in another terminal, then reload.
              <code>uvicorn backend.main:app --reload</code></div>
          </div>
        </section>
      )}

      <section className="gallery">
        <div className="gallery-head">
          <h2>Your decks</h2>
          <span>{decks.length ? `${decks.length} saved` : ''}</span>
          {!meta.offline && (
            <button className="mini new-deck" onClick={blank}>
              <Icon name="plus" size={15} />Blank deck
            </button>
          )}
        </div>
        {blankError && (
          <div className="alert"><Icon name="alert" size={18} />
            <div>Could not start a new deck.<code>{blankError}</code></div></div>
        )}
        {shown.length === 0 ? (
          <div className="empty">
            <h3>Nothing here yet</h3>
            <p>Drop in your notes and say what the deck has to do. The model reads the
              material, decides how many slides it earns, picks a template, writes it, and
              finds a photo for every slide with room for one — then it opens in the editor
              and every word, box and colour is yours to move.</p>
          </div>
        ) : (
          <div className="grid">
            {shown.map(d => (
              <Card key={d.id} deck={d} template={meta.templates[d.template]}
                    fresh={d.id === freshId} live={d.id === draft?.id}
                    onOpen={() => setOpen(d.id)} onDelete={() => remove(d)} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
