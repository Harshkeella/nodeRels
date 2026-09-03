# Agent: Deck Planner

> `Agents/A1_pptx/backend/ppt.py` — notes in, deck plan out. One constrained model call
> writes the slides, a checker verifies them, and a repair pass fixes what it finds.

| # | Chapter |
|---|---|
| 1 | [Overview](#1-overview) |
| 2 | [Tech Stack](#2-tech-stack) |
| 3 | [Folder & File Structure](#3-folder--file-structure) |
| 4 | [How This Fits Into the Bigger Picture](#4-how-this-fits-into-the-bigger-picture) |
| 5 | [Core Concepts & Key Components](#5-core-concepts--key-components) |
| 6 | [Function & Component Reference](#6-function--component-reference) |
| 7 | [End-to-End Walkthroughs](#7-end-to-end-walkthroughs) |
| 8 | [Configuration & Setup](#8-configuration--setup) |
| 9 | [Known Limitations & Open TODOs](#9-known-limitations--open-todos) |
| 10 | [See Also](#10-see-also) |

> Deck Studio (`Agents/A1_pptx/`) runs as a separate service with its own React editor
> and `.env`. The integrated MCP endpoint receives approved nodeRels content and the
> original design request. `grounded.py` uses this module's model client and template
> vocabulary to plan design, then preserves the source text during pagination. The
> standalone generation flow described below also remains available.

---

## 1. Overview

Give it a request and some source material and it produces a slide deck. One Groq call with
**strict constrained decoding** writes the whole plan — how many slides the material
justifies, what each is about, which template fits, and the words on each slide. The reply is
guaranteed to be valid JSON matching a schema, because the schema is enforced by the decoder,
not by parsing and hoping.

A JSON schema can't express *"exactly one title slide, and it must be first"* or *"never
three bullet slides in a row"*, so `check()` does. It returns a list of complaints in plain
English. If there are any, the deck and the complaints — but **not** the source material
again — go back for one repair round. If that still fails, `salvage()` fixes it
deterministically: demote a chart slide whose data doesn't add up to bullets, drop slides
that can't be rescued, force the ends, de-duplicate titles.

The same module is also the renderer. `render()` walks the element list and emits a real
`.pptx` via python-pptx, including **native PowerPoint charts** whose data stays editable
rather than pictures of charts. Both jobs live here because both are loops over the same
document, and there is only one description of a slide in this application.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **`urllib.request`** (stdlib) | The Groq call. No SDK, no `httpx`, no `requests` — one POST with a JSON body. The whole provider integration is ~30 lines. |
| **Groq `response_format: json_schema, strict: true`** | Constrained decoding. The model *cannot* emit invalid JSON or an unknown field, which removes an entire class of parsing code. |
| **`python-pptx`** ≥1.0 | The `.pptx` writer. Chosen because it emits native chart XML — a chart exports as a real PowerPoint chart with editable data, not an image. |
| **`json`** (stdlib) | Templates are JSON files; add a file to `templates/` and it appears in the UI. |

`reasoning_effort: "low"` is set on the call because gpt-oss reasoning tokens bill as
completion tokens and count against `MAX_OUT`.

---

## 3. Folder & File Structure

```
Agents/A1_pptx/backend/
├── ppt.py                     # 883 lines
│   ├── MODEL / GROQ_URL / TPM / MAX_OUT / SOURCE_BUDGET
│   ├── TEMPLATES              # loaded from templates/*.json at import
│   ├── SCHEMA                 # the strict deck-plan JSON schema
│   ├── PROMPT                 # the planning prompt
│   ├── REPAIR                 # the repair prompt
│   │
│   ├── ── render ──
│   │   _rgb / _alpha / _font_name
│   │   _text / _picture / _shape / _table / _chart
│   │   DRAW = {...}           # element type -> drawer
│   │   render(deck, path)     # THE EXPORTER
│   │
│   ├── ── check + repair ──
│   │   check(deck)            # semantic faults the schema cannot express
│   │   _plottable(s)
│   │   salvage(deck)          # deterministic last resort
│   │
│   ├── ── model ──
│   │   fit(text, used)        # trim source to one TPM window
│   │   _ask(messages, schema, name, effort)   # THE ONE GROQ CALL
│   │   build(request, data)   # THE ENTRY POINT: plan -> check -> repair -> salvage
│   │   read_sources(paths)
│   │
│   ├── ── images ──
│   │   slug_id / deck_pid / illustrate_slide / illustrate
│   └── demo()                 # --demo: every template, every element type -> .pptx
│
├── deck.py                    # the document model — see visual-coverage.md
├── templates/*.json           # corporate, startup, technical, academic, sales
└── main.py                    # the API and the SSE generation route
```

---

## 4. How This Fits Into the Bigger Picture

```
   POST /api/generate  (SSE)              or   python -m backend.ppt "request" notes.md
        │
        ▼
   ppt.build(request, data)
        │  fit(data, len(scaffold))          trim source to one TPM window
        │  _ask([...])                        ← ONE Groq call, strict schema
        │  check(deck)  ──── faults ───► _ask(REPAIR)  ──── still bad ───► salvage(deck)
        ▼
   deck.expand(deck)                        authored slides -> flat element lists
        │
        ├──► SSE "plan"        the deck is on screen and readable NOW
        │
        ▼
   ppt.illustrate(deck)  ──► image-engine   one photo per slide with room for one
        │
        ├──► SSE "image" × n  one event per slide as its photo lands
        │
        ▼
   deck.ensure_visuals(deck)  ──► visual-coverage   repair slides the photos missed
        │
        ├──► SSE "done"
        ▼
   main.save(deck)  → outputs/<id>.json
        │
        └── GET /api/file/{id}.pptx  → ppt.render(deck, path)
```

**Why SSE and not a POST:** an illustrated deck is a plan call plus a provider search and a
download per slide — a minute or more. The stream sends the plan the moment it exists, so the
deck is on screen and readable before any photo lands. A silent POST for a minute is a broken
product.

**Why `ensure_visuals` runs after the photos:** so a slide the image engine filled is left
alone and only the ones it could not fill are repaired. That ordering *is* the fallback
chain — photo first, then the honest offline visual.

---

## 5. Core Concepts & Key Components

### Constrained decoding removes the parsing problem

`response_format: {"type": "json_schema", "json_schema": {"strict": true, "schema": SCHEMA}}`.
The model cannot emit invalid JSON, an unknown field, or a `kind` outside the enum. So there
is no JSON repair, no retry-on-parse-failure, and no defensive `.get()` chain in the caller.

Strict mode accepts **no length keywords** — no `maxLength`, no `maxItems` — so the budgets
live in the prompt and are enforced for real by `clip()` and the `[:5]` in `render()`. Stated
in a comment, because it looks like an omission otherwise.

Every field is `required` with a nullable type rather than optional, because that's what
strict mode demands. Unused fields arrive as `null`.

### The prompt asks for a decision order, not a format

> *Decide, in this order: how many slides the material actually justifies (6–14 — do not
> pad), what each slide is about, which template fits, then the words on each slide.*

And it pushes hard against the default failure mode of deck generation, which is a document
in slide clothing:

- *"Reach for a visual kind whenever the material supports one. If a point is three or more
  numbers, it is a chart slide, not a bullets slide."*
- *"At least one stat, chart, table or two_col per 4 slides — a deck of nothing but bullets
  is a document, not a deck."*
- *"Titles are specific claims, not category labels ('Churn doubled in EU', not 'Churn')."*
- *"Stats come from the source material. Do not invent numbers."*

The template menu is generated from each template's own `use_for` field, so adding a template
file changes the prompt automatically.

### `chart` and `table` are slide kinds the planner can reach

Both element types already existed — in both renderers and in the exporter. The planner
simply could not ask for one, *"which is the whole reason a deck about numbers arrived as
paragraphs about numbers."* A chart slide exports as a **native PowerPoint chart** whose data
is still editable, not a picture of one.

### `check()` finds what a schema can't express

Eleven rules, each returning a plain-English complaint:

| Rule | Complaint |
|---|---|
| Slide 1 must be `title`; last must be `closing`; exactly one `title` | `"slide 1 must be kind=title"` |
| Non-empty title and notes on every slide | `"slide 3: empty notes"` |
| A `bullets` slide needs 2–5 bullets, none blank | `"slide 4: bullets slide needs 2-5 bullets"` |
| A `stat` slide needs a stat; a `two_col` needs both sides | `"slide 6: two_col slide needs both sides"` |
| A `chart` slide must be `_plottable` | `"slide 7: chart slide needs categories and one value per category in every series"` |
| A `table` slide needs a header row plus one more | |
| No duplicate titles | `"slide 9: duplicate title Growth"` |
| Never three `bullets` slides in a row | `"slides 2-4: three bullets slides in a row"` |

`_plottable` is the one worth reading: *a series one value short is a chart python-pptx will
draw with a hole in it.* Catching it here is cheaper than debugging the export.

### Repair sees the complaints, never the source again

```python
deck = _ask([{"role": "user", "content": REPAIR.format(faults=…, caps=…, deck=…)}])
```

The comment states the reason: these are **structural** fixes, and resending the notes
doubles the TPM spend. Carries a `ponytail:` marker — one repair round, then deterministic
salvage; loop it only if a second round ever measurably helps, *"so far the first fixes it or
nothing does."*

### `salvage()` demotes rather than drops

The interesting choice: a data slide whose data doesn't add up still has a title and notes,
so it's **demoted to the kind it can actually be** rather than dropped — the point it was
making survives as bullets, sourced from its own categories or the first column of its rows.

Only after that are genuinely unusable slides removed. Then the ends are forced (positional,
so fixed before judging), notes are backfilled from titles, and duplicate titles get
`(cont.)` appended until unique. Fewer than two survivors raises.

### `fit()` protects the TPM window

```python
SOURCE_BUDGET = int((TPM * 0.85 - MAX_OUT) * 4) - 2200
```

Request plus reserved output must fit one tokens-per-minute window. 15% headroom because the
4-chars-per-token ratio is an estimate. If the budget leaves under 400 characters it raises
with the fix (`raise GROQ_TPM`); over budget it trims and prints how much to stderr rather
than silently truncating.

### `_ask()` is shared with the assistant

Both `ppt.build` and [`deck-assist`](deck-assist.md) call `_ask`, passing different schemas.
So the retry, the rate-limit handling and the one place the API key is read are shared — one
integration, two callers.

Its 429 handling distinguishes two cases: **429** means the window is spent and it refills,
so wait `retry-after` (once, if ≤65s). **413** means this one request is bigger than the whole
window, so waiting changes nothing and it fails immediately. There's also a `User-Agent`
header, because Groq's edge 403s the default Python-urllib agent — any real name gets through.

---

## 6. Function & Component Reference

---

### `build(request, data)`

**What it does:** The entry point. Plans a deck, checks it, repairs it, salvages it.

**Input:** `request: str` (what the user asked for), `data: str` (the source material).

**Output:** `dict` — the authored deck plan.

**Example:**
```python
build("board update, keep it blunt", open("notes.md").read())
# => {"deck_title": "Q3 Board Update", "template": "corporate", "slides": [
#      {"kind": "title", "title": "Q3 Board Update", "subtitle": "Revenue up, churn up",
#       "bullets": None, "stat": None, "label": None, "left": None, "right": None,
#       "chart": None, "categories": None, "series": None, "rows": None,
#       "notes": "Set the frame: growth is real, retention is the problem."},
#      {"kind": "chart", "title": "Revenue grew 34% but churn doubled",
#       "chart": "bar", "categories": ["Q1", "Q2", "Q3"],
#       "series": [{"name": "Revenue", "values": [412000, 388500, 501200]}],
#       "notes": "…", …},
#      …
#      {"kind": "closing", "title": "Fix retention before scaling spend", …}]}
```

**Notes:** Costs **one** Groq call for a clean plan, **two** if the checker complains, and
zero more after that — `salvage` is offline. Complaints are printed to stderr, so a CLI run
shows what the checker caught.

---

### `check(deck)`

**What it does:** Semantic faults the JSON schema cannot express.

**Input:** `deck: dict`. **Output:** `list[str]` — empty means clean.

**Example:**
```python
check({"slides": [
    {"kind": "bullets", "title": "Growth", "bullets": ["a"], "notes": "n"},
    {"kind": "bullets", "title": "Growth", "bullets": ["a", "b"], "notes": ""},
]})
# => ["slide 1 must be kind=title",
#     "slide 2 must be kind=closing",
#     "exactly one title slide allowed",
#     "slide 1: bullets slide needs 2-5 bullets",
#     "slide 2: empty notes",
#     "slide 2: duplicate title Growth"]
```

**Notes:** Reads the **authored** fields (`bullets`, `stat`, `left`), not elements — it runs
before `expand()`. Every complaint names the slide number, because that's what the repair
prompt needs to act on.

---

### `salvage(deck)`

**What it does:** The deterministic last resort when the repair call still fails.

**Input:** `deck: dict`. **Output:** the same dict, mutated. Raises `SystemExit` if fewer than
two slides survive.

**Example:**
```python
salvage({"slides": [
    {"kind": "chart", "title": "Growth", "categories": ["Q1", "Q2"],
     "series": [{"name": "Rev", "values": [1]}], "notes": ""},      # one value short
    {"kind": "bullets", "title": "", "bullets": [], "notes": ""},   # nothing usable
    {"kind": "bullets", "title": "Next", "bullets": ["a", "b"], "notes": ""},
]})
# slide 1 demoted to bullets with ["Q1", "Q2"] — the point survives
# slide 2 dropped — no title
# ends forced: slide 1 -> title, slide 3 -> closing
# empty notes backfilled from titles
```

**Notes:** Order matters. The ends are forced **before** judging, because they're positional.
Demotion happens **before** dropping, because a chart with bad numbers is still a point worth
making.

---

### `_ask(messages, schema=None, name="deck", effort="low")`

**What it does:** One Groq call with strict constrained decoding. Shared with
[`deck-assist`](deck-assist.md).

**Input:** `messages: list[dict]`, `schema: dict | None` (defaults to the deck plan),
`name: str`, `effort: str`.

**Output:** `dict` — the parsed JSON content. Raises `SystemExit` on a non-recoverable HTTP
error or a missing key.

**Notes:** `temperature=0.4`, `max_completion_tokens=MAX_OUT`, `timeout=180`. One retry, and
only for a 429 with `retry-after ≤ 65`. A 413 fails immediately — waiting cannot make one
request smaller than the whole window.

---

### `render(deck, path="deck.pptx")`

**What it does:** Writes the `.pptx`. A loop over each slide's element list.

**Input:** `deck: dict` (expanded, with elements), `path: str`.

**Output:** the path. Side effect: writes the file.

**Notes:** Dispatches through `DRAW = {"text": _text, "image": _picture, "shape": _shape,
"line": _shape, "table": _table, "chart": _chart}`. Geometry is inches and type size is
points **because that is what python-pptx wants** — the browser's `cqw` maths converts to the
same units, so neither renderer does arithmetic the other might get wrong. Slides marked
`hidden` are skipped, in the export and in present mode. **The `.pptx` is built on download,
not on save**: autosave fires while you drag, and rendering a deck of charts and photos that
often is work nobody asked for — and it's the only way a download could ever disagree with
what's on screen.

---

### `fit(text, used)`

```python
fit("x" * 100_000, used=3000)
# prints to stderr: "source trimmed to 24800 of 100000 chars to fit the 8000 TPM limit"
# => "xxxx…"  (24800 chars)
```

Raises `SystemExit` naming `GROQ_TPM` if the budget leaves under 400 characters.

---

### `illustrate(deck, presentation_id=None)` / `illustrate_slide(deck, index, presentation_id=None)`

The seam to [`image-engine`](image-engine.md). The **caller owns the layout**, so the caller
supplies the slots — `IMAGE_RATIO = "4:5"`, and only `IMAGE_KINDS = ("title", "closing",
"bullets")` get one. A `stat` slide is one number at 90pt; a `two_col` is full.

`deck_pid` mints the dedupe scope. Reusing one across runs makes every good image look
"already used", so the server mints a fresh id per deck.

---

### `read_sources(paths)` / `slug_id(title)`

`read_sources` reads files and directories into one string for the CLI. `slug_id` turns a
title into a filesystem-safe id, capped at 40 characters.

---

## 7. End-to-End Walkthroughs

### 7.1 A clean generation over SSE

1. `POST /api/generate {"request": "board update, keep it blunt", "sources": [...]}`.
2. `ppt.build(request, data)`:
   - `PROMPT.format(...)` with an empty `data` measures the scaffold; `fit(data, len(scaffold))`
     trims the source to what's left of the TPM window.
   - `_ask` → one Groq call, strict schema → a 9-slide plan.
   - `check(deck)` → `[]`. **No repair call.**
3. `deck.expand(deck)` turns authored slides into flat element lists — once, on the way in.
4. **SSE `plan`.** The editor renders the deck immediately; nothing is illustrated yet.
5. `ppt.illustrate(deck, pid)`: for each slide whose `kind` is in `IMAGE_KINDS`, one
   `select_for_slide_sync` call. **SSE `image`** per slide as its photo lands.
6. `deck.ensure_visuals(deck)` — only the slides the engine couldn't fill are repaired. See
   [`visual-coverage`](visual-coverage.md).
7. `main.save(deck)` → `outputs/<id>.json`. **SSE `done`.**
8. Later, `GET /api/file/<id>.pptx` → `ppt.render(deck, path)` from the document **as it is
   at request time**, so the download always matches the editor.

---

### 7.2 The checker catches a broken chart

1. `_ask` returns a plan whose slide 5 is `kind: "chart"` with three categories and a series
   of two values.
2. `check` → `["slide 5: chart slide needs categories and one value per category in every
   series"]`. Printed to stderr.
3. `_ask(REPAIR)` — the deck and the one complaint, **not** the source material. The model
   returns a corrected plan with three values.
4. `check` again → `[]`. Generation continues.

**Cost: one extra call, no source resend.**

---

### 7.3 Repair fails and salvage takes over

1. Same as 7.2, but the repair returns a plan where slide 5 *still* has a ragged series and
   slide 8 has an empty title.
2. `check` is non-empty → `"repair incomplete -- salvaging"` to stderr → `salvage(deck)`.
3. Ends forced positionally first.
4. Slide 5: `_plottable` is `False` → **demoted to `bullets`**, its bullets sourced from its
   own `categories`. The point survives; only the chart is lost.
5. Slide 8: empty title → dropped.
6. Notes backfilled from titles where empty; duplicate titles get `(cont.)`.
7. Enough slides survive, so generation continues. **No third model call.**

---

## 8. Configuration & Setup

```bash
cd Agents/A1_pptx
pip install -r requirements.txt
cp .env.example .env          # fill in GROQ_API_KEY
uvicorn backend.main:app --reload      # :8000
```

`backend/__init__.py` loads `.env` **before** any submodule reads `os.getenv` — `ppt.py` and
`image_engine/config.py` freeze their settings into module constants at import time, so the
file has to be in `os.environ` first. Python imports the package first for every entry point,
so that one file covers `uvicorn backend.main:app`, `python -m backend.ppt` and
`python -m backend.image_engine`. Real environment variables still win.

| Variable | Default | Effect |
|---|---|---|
| `GROQ_API_KEY` | — | Required for generation. Without it the editor still works on existing decks. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | |
| `GROQ_TPM` | `8000` | Your tier's tokens-per-minute. Drives `SOURCE_BUDGET`. |

### CLI

```bash
python -m backend.ppt "board update, keep it blunt" notes.md research/
python -m backend.ppt "series A pitch" notes.md --images --template startup
```

### Self-check

```bash
python -m backend.ppt --demo     # every template, every element type -> .pptx
```

No API call. It renders a fixed demo deck through every template and reads the `.pptx` back,
checking images, elements and data slides.

### Adding a template

Drop a JSON file in `templates/`. It's loaded at import and **appears in the UI and in the
planning prompt automatically** — the prompt's template menu is built from each file's
`use_for` field, and `SCHEMA`'s `template` enum is `sorted(TEMPLATES)`.

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **One repair round** (`ponytail:`) | Then deterministic salvage. Loop it only if a second round ever measurably helps. |
| **Charts approximate on export** | The editor draws SVG; the export is a native PowerPoint chart laid out by PowerPoint. Data, type, colours and legend match; gridline and axis furniture will not be pixel-identical. |
| **No PPTX import** | The pipeline is obvious (parse → canonical JSON → editor) and nothing in the model blocks it, but it isn't written. |
| **No PDF or PNG export** | Both need a headless browser or LibreOffice; neither is a dependency this project has. |
| **`salvage` raises `SystemExit`** | Fine for the CLI, wrong inside a web request — it's caught by the SSE handler, but the exception type is a CLI idiom leaking into a server. |
| **`fit()` truncates by characters** | Mid-sentence, mid-word. A smarter trim would cut at a paragraph boundary. |
| **Source budget is computed at import** | `SOURCE_BUDGET` freezes `GROQ_TPM` at module load; changing the env var needs a restart. |
| **No streaming of the plan itself** | The `plan` SSE event fires only once the whole call returns — 20–40 seconds of silence before the first event. |
| **`_ask` retries only 429** | A 500 or a timeout fails the whole generation with no retry. |
| **Templates are unvalidated** | A malformed `templates/*.json` raises at import, taking the server down at boot. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [Deck / Presentation](../GLOSSARY.md#deck--presentation),
  [Element](../GLOSSARY.md#element), [`expand()`](../GLOSSARY.md#expand),
  [Theme token](../GLOSSARY.md#theme-token), [Slot](../GLOSSARY.md#slot),
  [Visual coverage](../GLOSSARY.md#visual-coverage)
- [`visual-coverage`](visual-coverage.md) — `deck.py`: the document model, `expand()`, the
  trust boundary, and the repair pass that runs after this one
- [`image-engine`](image-engine.md) — what `illustrate()` calls
- [`deck-assist`](deck-assist.md) — shares `_ask`, `fit` and the schema discipline
- [`agents/README.md`](README.md) — and `Agents/A1_pptx/README.md`, the app's own
  user-facing documentation
