# Deck Studio

Notes in, deck out — then edit or explain it. One Groq call turns even a short topic into
a plain-English story, an evidence check blocks unsupported numeric visuals, and an image
engine adds one or two licensed photos from stock providers or key-free Wikimedia Commons.
The deck opens in a browser editor where every box, word and colour is movable, and its
Video workspace can create a narrated MP4 that highlights each point while it is spoken.

Two terminals:

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload          # terminal 1 — the whole backend, :8000
```
```bash
cd frontend && npm install
npm run dev                                # terminal 2 — the UI, :5173
```

Open http://localhost:5173. Set `BACKEND=http://127.0.0.1:8010 npm run dev` if something
else already owns port 8000.

Copy `.env.example` to `.env` and fill in `GROQ_API_KEY` before generating. The photo
providers are optional and independent — with none set you get text decks, with any one
set you get illustrated ones. Without a key at all the editor still works on decks you
already have.

---

## The one decision everything else follows from

**A deck is a list of elements, and there is exactly one description of it.**

```
Presentation { id, deck_title, template, w, h, slides[], created, updated }
  Slide      { id, name, background, elements[], notes, hidden }
    Element  { id, type, x, y, w, h, rotation, locked, hidden, content, style }
```

`type` is one of `text · image · shape · line · table · chart`. Geometry is in inches
(the slide is 13.333 × 7.5), type size in points — exactly what python-pptx wants and
what the browser's `cqw` maths already converts, so neither renderer does arithmetic the
other might get wrong.

`ppt.render()` is a loop over that list. `Slide.jsx` is the same loop in the browser.
Neither knows what a "stat slide" is any more — that lives in `deck.expand()`, which runs
once, on the way in. **The editor cannot show you something the export will not**, because
there is nothing else for either of them to read.

Colours are a **theme token** (`primary`, `accent`, `text`, `muted`, `bg`) or a literal
`#rrggbb`. Tokens are the whole theme system: change the template and every token element
follows, while anything you set to a specific colour stays put. Fonts work the same way —
a role (`display` / `body`) that the template resolves, or a literal family you picked.

Array order is z-order. There is no separate `zIndex` to fall out of step with it.

## Every slide has something to look at

A deck of bullet lists is a document. Two things stop one being generated.

**The planner can ask for a chart or a table.** `chart` and `table` are slide kinds
alongside `title · bullets · stat · two_col · closing`, and they carry the categories,
series and rows to fill them. Both element types already existed in the document, in both
renderers and in the exporter — the planner simply could not reach them, so a slide about
numbers arrived as a paragraph about numbers. A chart slide exports as a *native
PowerPoint chart* with value labels, category labels, and a legend. Data stays editable.
Values must appear in the user's request or supplied notes; unsupported numbers, ragged
series, and empty tables are caught before the deck is saved.

**The application checks, and repairs what it finds.** `deck.has_visual()` reads the
elements a slide actually ended up with. It never asks the model whether it added a
picture — that is asking the thing that just failed to do it whether it did it. An image
counts once it has a URL to draw, a chart or table counts once it is big enough to see,
and shapes count when one repeats (chips down a list, nodes on a timeline) rather than
when a single rule sits under a heading.

A slide that fails gets one repair pass — `deck.add_visual()`, deterministic, offline, no
second model call — in the order a designer reaches for:

| the slide has | it gets |
|---|---|
| three or more comparable numbers | a bar chart of them |
| two columns level with each other | a comparison table |
| a list of points | a numbered composition |
| one sentence, or nothing to work with | nothing — and it says so |

Mixed units never become a chart: `412 ms` plotted against `1.2 s` as 412 and 1.2 is
worse than the bullet list it replaced. `title`, `closing` and `stat` slides are exempt —
an opener is a title, and one number at 90pt already *is* the statistical visualisation.

The pass runs after the photos land, so a slide the image engine filled is left alone and
only the ones it could not fill are repaired. That ordering is the whole fallback chain:
photo first, then the honest offline visual, and a slide is never padded with a picture
that means nothing.

## What the editor does

Drag, resize, rotate, multi-select, marquee, snap to edges and centres (hold <kbd>Alt</kbd>
to ignore guides), align, distribute, group z-order, lock, hide, inline text editing.
Slides add, duplicate, reorder by drag, hide and delete. Undo/redo across all of it —
a drag is **one** undo step, not two hundred.

<kbd>Ctrl</kbd>+<kbd>Z</kbd>/<kbd>⇧Z</kbd> undo/redo · <kbd>C</kbd>/<kbd>V</kbd>/<kbd>X</kbd>/<kbd>D</kbd> copy/paste/cut/duplicate ·
<kbd>A</kbd> select all · <kbd>S</kbd> save now · arrows nudge (<kbd>⇧</kbd> for 0.5in) ·
<kbd>Enter</kbd> edit text · <kbd>Del</kbd> delete · <kbd>Esc</kbd> deselect.

Autosave writes the document about a second after you stop; the indicator says *Saving…*,
*Saved*, or *Not saved — retry*, and never says "saved" when it is not. The last 20 saves
are kept and restorable.

## Ask AI

Select an element, a slide, or nothing, and ask. The model gets the **current structured
state** of what you selected and returns *operations*, never prose and never state:

```json
{"action": "update_element", "element_id": "text_123", "changes": {"text": "…", "fontSize": 44}}
```

Every operation goes through `deck.apply_ops` → `deck.clean_element` before it touches the
document. An invented element id is refused and reported. An off-site image URL is stripped.
An unknown action is rejected. The deck you were working on is not mutated until a batch
succeeds, so a bad answer costs you nothing but a message saying so.

This is the same document the editor writes, so AI edits and hand edits compose: move a
box, ask AI to rebalance the slide, then nudge it again.

## Layout

```
backend/                one uvicorn target, no second process
  deck.py               THE MODEL: schema, expand(), validation, AI ops, visual coverage
  ppt.py                prompt → plan → check → repair; and elements → .pptx
  assist.py             Ask AI: structured operations, validated before they land
  main.py               API, SSE generation, save, upload, versions, export
  templates/*.json      five looks; add a file, it appears in the UI
  image_engine/         plan → search → rank → fetch → validate → dedupe → select
frontend/src/
  Slide.jsx             elements → DOM. Pure: it draws, it never handles a pointer
  Canvas.jsx            everything you can do with a pointer, layered on top
  doc.js                the document reducer: history, z-order, snapping, alignment
  Editor.jsx            the studio shell — rail, toolbar, autosave, shortcuts
  Inspector.jsx         properties, layers, theme, speaker notes
  Panels.jsx            layouts, text, shapes, images (upload/URL/search), charts, Ask AI
  Present.jsx           fullscreen present mode
ppt_video_agent/
  agent.py              deck → narration points → TTS → highlighted clips → MP4
outputs/                <id>.json is the deck; uploads/ and versions/ sit beside it
```

## Why this stack

**FastAPI + uvicorn.** The image engine already used pydantic and already had a FastAPI
surface, so the API layer cost one file rather than a new dependency. It gives streaming
responses natively, which is what generation needs.

**Server-sent events, not a POST.** An illustrated deck is one Groq call plus a plan call,
a provider search and a download per slide — a minute or more. The stream sends the plan
the moment it exists, so the deck is on screen and readable before any photo lands, then
one event per slide as its photo arrives. A silent POST for a minute is a broken product.

**Vite + React, no framework and still no dependencies.** One page, no routing, no SSR,
no data layer. `react` and `react-dom` are the entire runtime dependency list — no state
store (a `useReducer` is a state store), no CSS framework, no drag library, no chart
library (charts are hand-drawn SVG, because the export is a *native PowerPoint chart*
whose look this has to match rather than improve on), no icon package.

**Undo is a stack of documents, not a diff log.** Nothing is mutated in place, so an
untouched slide is the same object in every history entry — a "snapshot" of a 60-slide
deck is 60 pointers. A diff log would be a second model of the document to keep correct.

**The `.pptx` is built on download, not on save.** Autosave fires while you drag;
rendering a deck of charts and photos that often is work nobody asked for, and it is the
only way a download could ever disagree with what is on screen.

## The API

| | |
|---|---|
| `GET /api/meta` | templates, keys, providers, budgets, geometry, element vocabulary |
| `GET /api/decks`, `GET /api/deck/{id}` | every deck; one deck |
| `POST /api/deck` | a blank deck |
| `PUT /api/deck/{id}` | autosave — body is untrusted, rebuilt field by field |
| `POST /api/generate` | SSE: `status` → `plan` → `image`×n → `done`, or `error` |
| `POST /api/deck/{id}/ai` | Ask AI — returns the deck and what was refused |
| `GET /api/deck/{id}/versions`, `POST …/restore/{v}` | the last 20 saves |
| `POST /api/images`, `POST /api/upload` | stock search (cached), user uploads |
| `POST /api/upload/url` | an image by its address — fetched, sniffed and stored here |
| `GET/POST /api/deck/{id}/video` | inspect or start the deck's background video job |
| `GET /api/video/{id}.mp4` | stream or download the finished explanation video |
| `GET /api/file/{id}.pptx` | export, rendered from the document at request time |
| `GET /media/{path}`, `GET /uploads/{name}` | images, type sniffed from magic numbers |

## Security

The browser is not trusted and neither is the model. Every element enters the document
through `deck.clean_element`: unknown types, out-of-range geometry, NaN, and unknown style
keys are coerced or dropped. Image sources must be `/media/`, `/uploads/` or a `data:`
URI — a saved deck cannot be made to fetch from anywhere else when it opens. Uploads are
accepted only if they *are* a PNG, JPEG, GIF or WebP by magic number; **SVG is refused on
purpose** (it is a document with script in it). An image pasted as a URL goes through the
same door: the backend fetches it, checks the bytes and stores a copy, because a URL
ending `.jpg` is not proof that it returns a JPEG, and a deck that points at somebody
else's CDN is a deck that breaks when the link expires. `id`, `created` and `prompt` are read from
disk on every save, so a request body cannot rewrite them or move a deck.

## Presenting

Press Present in the editor, or <kbd>F</kbd>. `←` `→` `space` move, `Home` `End` jump to
the ends, `N` toggles speaker notes, `Esc` exits. Slides marked *skip in deck* are skipped
in present mode and in the export.

## The CLI still works

```bash
python -m backend.ppt "board update, keep it blunt" notes.md research/
python -m backend.ppt "series A pitch" notes.md --images --template startup
```

## Tests

No framework. Five self-checks, each one command, none spends an API call:

```bash
python -m backend.deck                         # the model: expand, validation, AI ops, visual coverage
python -m backend.assist                       # selection scoping, refused operations
python -m backend.ppt --demo                   # every template, every element type → .pptx
python -m backend.main --selftest              # routes, guards, and the whole lifecycle
python -m backend.image_engine.test_engine     # 13 checks across the image pipeline
cd frontend && npm test                        # 15 checks: the renderer and the reducer
```

`--selftest` runs the acceptance test end to end: generate → editor → hand edit → AI edit
→ insert an asset → reorder → autosave → reload → export, then reads the `.pptx` back and
checks the position, size and colour it finds there are the ones the editor was showing.

## Known limits

- **No PPTX import.** The pipeline for it is the obvious one (parse → canonical JSON →
  editor) and nothing in the model blocks it, but it is not written.
- **No PDF or PNG export.** Both need a headless browser or LibreOffice; neither is a
  dependency this project has.
- **Charts approximate.** The editor draws SVG; the export is a native PowerPoint chart
  laid out by PowerPoint. Data, type, colours and legend match; gridline and axis
  furniture will not be pixel-identical.
- **The repair pass draws, it does not illustrate.** With no photo key set, a bullets
  slide falls back to a numbered composition. That is an honest visual and it is not a
  photograph; set a provider key and the engine's picture wins, because the pass only
  touches slides it could not fill.
- **Rotate-then-resize** grows along the slide's axes, not the element's.
- **Single editor per deck.** The document model does not assume otherwise — slides and
  elements have stable ids and UI state is kept out of the document — but there is no
  presence, locking or merge, so two tabs on one deck will last-write-wins each other.
- **Desktop-first.** Below 860px the editor chrome hides itself and says so; presenting
  and exporting still work.
