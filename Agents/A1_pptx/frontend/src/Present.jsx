/* Present mode. One slide, no chrome, and the same renderer the editor uses -- so what
 * you present is what you were editing, down to the pixel.
 *
 * Keys: → ← space PageUp/Down move, Home/End jump, N notes, Esc exits.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Slide from './Slide'
import Icon, { IconButton } from './Icon'

export default function Present({ deck, template, from = 0, onExit }) {
  // A slide marked "skip in deck" is skipped here and in the export, which is the only
  // reading of "hidden" that does not surprise someone mid-presentation.
  const live = deck.slides.filter(s => !s.hidden)
  const start = Math.max(0, live.findIndex(s => s.id === deck.slides[from]?.id))
  const [n, setN] = useState(start === -1 ? 0 : start)
  const [notes, setNotes] = useState(false)
  const root = useRef(null)
  const total = live.length
  const slide = live[Math.min(n, total - 1)]

  // Where to put the editor back when this closes. A ref, because the fullscreenchange
  // listener is registered once and would otherwise close over the first slide forever
  // and drop you back on slide 1 however far you had presented.
  const landing = useRef(from)
  useEffect(() => {
    const at = deck.slides.findIndex(s => s.id === slide?.id)
    if (at >= 0) landing.current = at
  }, [slide, deck.slides])

  const done = useRef(false)
  const leave = useCallback(() => {
    if (done.current) return          // exitFullscreen fires the change handler too
    done.current = true
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {})
    onExit(landing.current)
  }, [onExit])

  useEffect(() => {
    root.current?.requestFullscreen?.().catch(() => {})   // refused is fine: still works
    // F11, Esc handled by the browser, a window blur -- every route out has to put the
    // editor back rather than leaving a black screen with no chrome.
    const sync = () => { if (!document.fullscreenElement) leave() }
    document.addEventListener('fullscreenchange', sync)
    return () => document.removeEventListener('fullscreenchange', sync)
  }, [leave])

  useEffect(() => {
    const go = i => setN(Math.max(0, Math.min(total - 1, i)))
    const onKey = e => {
      if (e.key === 'Escape') { e.preventDefault(); leave() }
      else if ([' ', 'ArrowRight', 'PageDown', 'ArrowDown'].includes(e.key)) {
        e.preventDefault(); go(n + 1)
      } else if (['ArrowLeft', 'PageUp', 'ArrowUp'].includes(e.key)) { e.preventDefault(); go(n - 1) }
      else if (e.key === 'Home') go(0)
      else if (e.key === 'End') go(total - 1)
      else if (e.key === 'n' || e.key === 'N') setNotes(v => !v)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [n, total, leave])

  if (!slide) return null

  return (
    <div className={'present' + (notes ? ' with-notes' : '')} ref={root}
         role="region" aria-label={`Presenting ${deck.deck_title}`}>
      <div className="present-stage" onClick={() => setN(i => Math.min(total - 1, i + 1))}>
        <Slide slide={slide} template={template} />
      </div>

      {notes && (
        <div className="present-notes">
          <b>Slide {n + 1} — speaker notes</b>
          <p>{slide.notes || 'No notes on this slide.'}</p>
        </div>
      )}

      <div className="present-hud">
        <span>{n + 1} / {total}</span>
        <IconButton icon="left" label="Previous" disabled={n === 0} onClick={() => setN(n - 1)} />
        <IconButton icon="right" label="Next" disabled={n === total - 1} onClick={() => setN(n + 1)} />
        <IconButton icon="notes" label={notes ? 'Hide notes (N)' : 'Speaker notes (N)'}
                    onClick={() => setNotes(v => !v)} />
        <IconButton icon="x" label="Exit (Esc)" onClick={leave} />
      </div>
    </div>
  )
}
