import { useEffect, useState } from 'react'
import { connection, getDecks, getMeta } from './api'
import Editor from './Editor'
import Slide, { setGeometry } from './Slide'

export default function ConnectedStudio() {
  const [deck, setDeck] = useState(null)
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState('')
  const [index, setIndex] = useState(0)
  useEffect(() => {
    let live = true
    Promise.all([getDecks(), getMeta()]).then(([decks, m]) => {
      if (!live) return
      setGeometry(decks[0]); setDeck(decks[0]); setMeta(m)
    }).catch(e => live && setError(e.message))
    return () => { live = false }
  }, [])
  const shown = deck?.slides.filter(s => !s.hidden) || []
  useEffect(() => {
    if (connection.mode === 'edit') return
    function key(e) {
      if (e.key === 'Escape' && connection.parent) window.parent.postMessage({ type: 'noderels-preview-escape' }, connection.parent)
      if (e.key === 'ArrowRight') setIndex(i => Math.min(shown.length - 1, i + 1))
      if (e.key === 'ArrowLeft') setIndex(i => Math.max(0, i - 1))
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [shown.length])
  if (error) return <div className="connected-message" role="alert">{error}<p>Reopen this presentation from nodeRels chat.</p></div>
  if (!deck || !meta) return <div className="connected-message" role="status">Opening presentation…</div>
  if (connection.mode === 'edit') return <Editor deck={deck} meta={meta} onSaved={setDeck} onClose={() => window.close()} />
  if (!shown.length) return <div className="connected-message">All slides are hidden. Open the editor to restore a slide.</div>
  return <main className="connected-preview">
    <div className="connected-slide"><Slide slide={shown[index]} template={meta.templates[deck.template]} /></div>
    <nav className="connected-nav" aria-label="Slide navigation">
      <button disabled={index === 0} onClick={() => setIndex(i => i - 1)}>Previous</button>
      <span aria-live="polite">Slide {index + 1} of {shown.length}</span>
      <button disabled={index === shown.length - 1} onClick={() => setIndex(i => i + 1)}>Next</button>
    </nav>
  </main>
}
