# Agent: Visual Coverage & the Document Model

> `Agents/A1_pptx/backend/deck.py` — the canonical presentation document, the trust boundary
> every element enters through, and the deterministic pass that makes sure no content slide
> arrives as nothing but text.

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

> **Different application.** Part of Deck Studio (`Agents/A1_pptx/`), not the nodeRels
> knowledge base.

---

## 1. Overview

`deck.py` is the file everything else in Deck Studio reads. It defines the document — a deck
is slides, a slide is elements, an element is a box with content and style — and it holds
three jobs that all follow from that one decision.

**`expand()`** turns the planner's authored slide kinds (`title`, `bullets`, `stat`,
`two_col`, `chart`, `table`, `closing`) into flat element lists, **once, on the way in**.
That's what lets both renderers be dumb loops that don't know what a "stat slide" is — and
why the editor cannot show you something the export will not.

**`clean_element()`** is the trust boundary. Every element that enters the document — from
the AI assistant, from a hand-edited JSON, from a stale client — passes through it. Unknown
types, out-of-range geometry, NaN, unknown style keys and off-site image URLs are coerced or
dropped. It never raises: a bad field falls back, it does not lose the slide.

**`ensure_visuals()`** is the coverage pass. It reads the elements a slide **actually ended up
with** and decides whether it communicates visually. It never asks the model whether it added
a picture — *that is asking the thing that just failed to do it whether it did it.* A slide
that fails gets one repair pass: deterministic, offline, no second model call, in the order a
designer reaches for. Three or more comparable numbers become a bar chart. Two columns level
with each other become a comparison table. A list becomes a numbered composition. One
sentence gets nothing, **and it says so** — nothing here invents a picture to satisfy a rule.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **`copy`, `re`, `os`, `time`** (stdlib) | The entire import list. `deck.py` has no third-party dependency at all. |

That's deliberate. This is the file both renderers, the exporter, the API and the assistant
depend on, so it depends on nothing.

---

## 3. Folder & File Structure

```
Agents/A1_pptx/backend/
└── deck.py                    # 741 lines
    ├── W, H = 13.333, 7.5     # 16:9 in inches
    ├── TOKENS / TYPES / SHAPES / CHARTS / HEX
    ├── CAP                    # authored character budgets per field
    ├── IMAGE_X / IMAGE_W / IMAGE_H / IMAGE_RATIO / IMAGE_KINDS
    ├── clip / new_id / color / el / text_el
    │
    ├── ── authored slide -> elements ──
    │   expand_slide(s, num, total)
    │   expand(deck)
    │
    ├── ── visual coverage ──
    │   VISUAL_TYPES / NO_VISUAL_NEEDED / MIN_VISUAL / FIGURE
    │   has_visual(slide)      # reads what the slide ENDED UP WITH
    │   _body(slide)           # the biggest text box below the heading
    │   _figures(lines)        # (label, value) per line — one unit or nothing
    │   add_visual(slide)      # the repair, in a designer's order
    │   ensure_visuals(deck)   # THE PASS
    │
    ├── ── validation: the trust boundary ──
    │   num(v, lo, hi, default)   # also catches NaN
    │   _safe_url(u)
    │   clean_element(e, existing)   # THE BOUNDARY
    │   clean_slide(s, existing) / clean(deck, existing)
    │
    ├── ── AI operations ──
    │   OPS / ALIAS / _merge(element, changes)
    │   apply_ops(deck, ops)   # -> (new_deck, applied, rejected)
    │
    └── demo()                 # python -m backend.deck
```

---

## 4. How This Fits Into the Bigger Picture

Everything reads this file:

```
   ppt.build()  → authored plan
        │
        ▼
   deck.expand(deck)              ← ONCE, on the way in
        │
        ├──► ppt.render()         a loop over elements → .pptx
        ├──► Slide.jsx            the SAME loop in the browser
        │
        ├──► image-engine fills image elements
        │
        ▼
   deck.ensure_visuals(deck)      ← AFTER the photos, so only unfilled slides are repaired
        │
        ▼
   main.save()  →  outputs/<id>.json

   Writes from the browser:
     PUT /api/deck/{id}     → deck.clean(body, existing)      body is untrusted
     POST /api/deck/{id}/ai → assist.edit → deck.apply_ops → deck.clean_element
     POST /api/upload       → main.store()  (magic-number sniffing, SVG refused)
```

**The one decision everything follows from**, in the app's own words: *a deck is a list of
elements, and there is exactly one description of it.*

Before, `ppt.py` drew five hardcoded slide kinds in python-pptx and `Slide.jsx` drew the same
five by hand in the browser, kept in step by shipping the geometry over `/api/meta`. **Two
implementations of one layout is a drift bug waiting for a deadline.** Now `expand()` runs
once and both renderers are loops.

---

## 5. Core Concepts & Key Components

### The document

```
Presentation { id, deck_title, template, w, h, slides[], created, updated }
  Slide      { id, name, kind, background, elements[], notes, hidden }
    Element  { id, type, x, y, w, h, rotation, locked, hidden, content, style }
```

`type` ∈ `text · image · shape · line · table · chart`. Geometry in **inches** (the slide is
13.333 × 7.5), type size in **points** — exactly what python-pptx wants and what the
browser's `cqw` maths already converts, so neither renderer does arithmetic the other might
get wrong.

**Array order is z-order.** There is no separate `zIndex` to fall out of step with it.

### Theme tokens are the whole theme system

A colour is either one of `primary · accent · text · muted · bg` or a literal `#rrggbb`.
Change the template and every token element follows; anything set to a specific colour stays
put. Fonts work the same way — a role (`display` / `body`) the template resolves, or a literal
family you picked.

### `has_visual` reads outcomes, never intentions

```python
big = w * h >= MIN_VISUAL * W * H          # 6% of the slide
image counts only if content.url is set AND big
chart / table count if big
shapes: len >= 3, OR len >= 2 with a repeat
```

Three things worth noting:

- **An image counts only once it has a URL to draw.** An empty image element is not a visual.
- **Anything that counts has to be big enough to be seen.** *"A thumbnail in a corner is not
  a visual, and letting one pass would make the whole check theatre."*
- **A repeated shape is a composition** — chips down a list, nodes on a timeline, steps in a
  flow. *"A single bar under a heading is a rule, and a rule is decoration. Three of anything
  is a diagram either way."*

`NO_VISUAL_NEEDED = ("title", "closing", "stat")`: an opener is a title, and a stat slide is
one number at 90pt — **that number *is* the statistical visualisation.**

### `_body` measures boxes, not characters

The slide's body copy is the **biggest text box below the heading**, by area. The comment
explains why not by character count: *a "3 / 12" page number is five characters in four tenths
of a square inch, and measuring the words picks it over the bullets.*

It reads elements rather than authored fields, so it works on a slide somebody built by hand
as well as on a generated one.

### `_figures` refuses a chart that would lie

```python
return out if len(out) >= 3 and len(units) == 1 else []
```

*"Mixed units on one axis is a chart that lies: '412 ms' against '1.2 s' plotted as 412 and
1.2 is worse than the bullet list it replaced. One unit throughout, or no chart."* Any line
without a number aborts the whole extraction — a partial series is not a series.

### The repair order is a designer's order

`add_visual` tries three things, and the order is decided by what the slide has:

| The slide has | It gets |
|---|---|
| Three or more comparable numbers, **and no second column** | A bar chart of them |
| Two columns level with each other | A comparison table |
| A list of two or more points | A numbered composition |
| One sentence, or nothing to work with | **Nothing — and it says so** |

The two-column check runs **first**, because it decides which repair is even available:
*"there is no free half of the slide to put a chart in."* Elements within 0.3 inches of the
body's `y` count as a second column.

The chart takes the half the words are **not** in, and the words give up the rest. The table
**replaces** both columns. The composition replaces the body with numbered ellipse chips plus
the text beside each.

*"One sentence is a sentence. Numbering it would be theatre, and theatre is what the whole
check exists to keep off the slide."*

### `ensure_visuals` runs after the photos, and is idempotent

*"Runs once, after images have landed, so a slide the photo engine filled is left alone and
only the ones it could not fill are repaired. The repairs are deterministic, so a second pass
reaches the same answer — there is no loop here to bound."*

It returns a **report**, one entry per content slide: `{slide, id, visual: "present" |
"chart" | "table" | "composition" | "none", repaired}`. Nothing is hidden — a slide that got
nothing says `"none"`.

### `clean_element` is where the trust boundary sits

*"Coerce anything — an AI response, a hand-edited JSON, a stale client — into a legal element.
Never raises: a bad field falls back, it does not lose the slide. Every element enters the
document through here, so this is where the trust boundary sits. Nothing downstream re-checks,
and nothing downstream has to."*

| Guard | Behaviour |
|---|---|
| Unknown `type` | Falls back to the existing type, else `"text"` |
| Geometry | `num(v, lo, hi, default)` clamps and catches **NaN** via `f != f` |
| Style keys | A small closed vocabulary; anything else is dropped *"rather than passed to two renderers that would each have to guess what it meant"* |
| Colours | Must be a theme token or a `#rrggbb` literal, else `None` |
| Font family | Must match `[\w \-'.,]+`, capped at 60 chars |
| **Image `url`** | Must start `/media/`, `/uploads/` or `data:image/` — *"a remote src would let a saved deck fetch from anywhere the moment it opens"* |
| Table rows | Cells clipped to 200 chars, 12 columns, 24 rows, **padded to a rectangle** |
| Chart series | Values coerced to numbers; **padded/truncated to the category count**, because *"ragged rows break python-pptx outright"* |

`clean(deck, existing)` adds the server-owned-field rule: `id`, `created` and `prompt` come
from `existing` and are **never taken from the request body**, so a request cannot rewrite
them or move a deck.

### `apply_ops` never mutates the input

*"The model never touches the document. It proposes operations against ids that must already
exist, every element goes through `clean_element`, and anything unrecognised is rejected with
a reason rather than silently dropped or silently obeyed. The input deck is not mutated, so a
rejected batch leaves the user's work exactly as it was."*

`_merge` maps flat keys through `ALIAS` — *"a kindness to the model: it may send
`{"text": …}` or `{"fontSize": 44}` rather than the nested shape, and both land in the right
slot."*

---

## 6. Function & Component Reference

---

### `ensure_visuals(deck)`

**What it does:** Guarantees every content slide communicates visually, or records why it
doesn't.

**Input:** `deck: dict` — expanded, with elements, after images have landed.

**Output:** `list[dict]` — one entry per content slide. Mutates slides in place.

**Example:**
```python
ensure_visuals(deck)
# => [{"slide": 2, "id": "slide_…", "visual": "present"},              # the engine filled it
#     {"slide": 3, "id": "slide_…", "visual": "chart",  "repaired": True},
#     {"slide": 4, "id": "slide_…", "visual": "table",  "repaired": True},
#     {"slide": 5, "id": "slide_…", "visual": "none",   "repaired": False}]
# slides 1 and 6 (title, closing) are skipped entirely
```

**Notes:** Skips `NO_VISUAL_NEEDED` kinds. Deterministic and idempotent — a second pass
reaches the same answer, so there is no loop to bound.

---

### `has_visual(slide)`

**What it does:** Does this slide already carry something that communicates visually?

**Input:** `slide: dict`. **Output:** `bool`.

**Example:**
```python
has_visual({"elements": [
    {"type": "image", "w": 6.0, "h": 7.5, "content": {"url": "/media/x.jpg"}}]})
# => True    36% of the slide, and it has a URL

has_visual({"elements": [
    {"type": "image", "w": 6.0, "h": 7.5, "content": {"url": None}}]})
# => False   an empty image element is not a visual

has_visual({"elements": [
    {"type": "chart", "w": 1.0, "h": 0.5, "content": {...}}]})
# => False   0.5 sq in of a 100 sq in slide — below MIN_VISUAL, so decoration

has_visual({"elements": [
    {"type": "shape", "w": 0.46, "h": 0.46, "content": {"shape": "ellipse"}},
    {"type": "shape", "w": 0.46, "h": 0.46, "content": {"shape": "ellipse"}},
    {"type": "shape", "w": 0.46, "h": 0.46, "content": {"shape": "ellipse"}}]})
# => True    three shapes: a composition

has_visual({"elements": [
    {"type": "shape", "w": 4.0, "h": 0.06, "content": {"shape": "rect"}}]})
# => False   one bar under a heading is a rule, and a rule is decoration
```

**Notes:** Hidden elements are skipped. `MIN_VISUAL = 0.06` of the slide's area
(≈6 square inches of 100).

---

### `add_visual(slide)`

**What it does:** Repairs one slide that has no visual. Mutates its element list in place.

**Input:** `slide: dict`. **Output:** `"chart" | "table" | "composition" | None`.

**Example:**
```python
# three comparable figures, one unit
add_visual({"elements": [
    {"type": "text", "x": 0.9, "y": 0.5, "w": 11.5, "h": 1.0,
     "content": {"text": "Latency"}, "style": {}},
    {"type": "text", "x": 0.9, "y": 2.0, "w": 5.4, "h": 3.0, "style": {"size": 18},
     "content": {"text": "API 412 ms\nWorker 380 ms\nQueue 95 ms"}}]})
# => "chart"    a bar chart with categories ["API", "Worker", "Queue"]
#               and values [412.0, 380.0, 95.0], placed in the free half

# mixed units
# "API 412 ms\nWorker 1.2 s\nQueue 95 ms"   =>  falls through to "composition"

# one sentence
# "Retention is the problem."               =>  None
```

**Notes:** Returns `None` when there's no body, no lines, or fewer than two lines with nothing
else applicable. The chart's `at` position is computed from which half the body occupies, and
the body is narrowed to 5.4 inches to make room. The composition caps at 5 lines and sizes its
gap from the body's height.

---

### `_body(slide)` / `_figures(lines)`

```python
_figures(["API 412 ms", "Worker 380 ms", "Queue 95 ms"])
# => [("API", 412.0), ("Worker", 380.0), ("Queue", 95.0)]

_figures(["API 412 ms", "Worker 1.2 s", "Queue 95 ms"])
# => []     two units — a chart here would lie

_figures(["Fast", "Slow"])
# => []     no numbers, and fewer than three lines

_figures(["A 1", "B 2"])
# => []     fewer than three
```

`_body` returns the largest-area text element **excluding the topmost one** (the heading),
sorted by `y`.

---

### `expand(deck)` / `expand_slide(s, num, total)`

**What it does:** Turns authored slides into flat element lists. Runs **once**, on the way in.

**Input:** `deck: dict` — the planner's output. **Output:** the deck with `slides[].elements`.

**Example:**
```python
expand({"deck_title": "Q3", "template": "corporate", "slides": [
    {"kind": "stat", "title": "Churn doubled", "stat": "2.4x", "label": "vs Q2",
     "notes": "…"}]})
# => slides[0]["elements"] ==
#    [text_el(...) for the title,
#     text_el(..., size=90, ...) for "2.4x",
#     text_el(...) for "vs Q2"]
#    — three plain elements. Neither renderer knows what a "stat slide" is.
```

**Notes:** `clip()` enforces the authored `CAP` budgets here — *"the authored caps are the
overflow guarantee: a generated deck must not arrive already spilling out of the box
`expand()` sizes for it. Once it is elements the user owns the box, so nothing downstream
clips again."*

---

### `clean_element(e, existing=None)`

**What it does:** The trust boundary. Coerces anything into a legal element. **Never raises.**

**Input:** `e: dict` (untrusted), `existing: dict | None` (for a partial update).

**Output:** `dict | None` — `None` only when `e` isn't a dict and there's no `existing`.

**Example:**
```python
clean_element({"type": "video", "x": float("nan"), "w": 999,
               "content": {"url": "https://tracker.example/pixel.png"},
               "style": {"size": 9999, "color": "chartreuse", "wobble": True}})
# => {"id": "text_…", "type": "text",          # "video" is not a TYPE
#     "x": 0.0,                                 # NaN -> default
#     "y": 0.0, "w": 26.666, "h": 0.5,          # 999 clamped to W*2
#     "rotation": 0, "locked": False, "hidden": False,
#     "content": {"text": ""},                  # coerced to the text shape
#     "style": {"size": 400,                    # clamped to the 4..400 range
#               "color": None,                  # not a token, not #rrggbb
#               "opacity": 1, "radius": 0, "align": "left", "valign": "top",
#               "font": "body", "family": None, "bold": False, …}}
#     "wobble" dropped entirely; the tracker URL never survives — the type changed to text,
#     and even as an image the url guard would have nulled it.
```

**Notes:** **SVG is refused on purpose** at the upload boundary (`main.store`) — *"it is a
document with script in it."* Uploads are accepted only if they *are* a PNG, JPEG, GIF or
WebP **by magic number**. An image pasted as a URL goes through the same door: the backend
fetches it, checks the bytes and stores a copy, *"because a URL ending `.jpg` is not proof
that it returns a JPEG, and a deck that points at somebody else's CDN is a deck that breaks
when the link expires."*

---

### `clean_slide(s, existing=None)` / `clean(deck, existing=None)`

```python
clean(body_from_browser, existing=read(deck_id))
# id, created and prompt come from `existing` — a request body cannot rewrite them
# or move a deck. slides capped at 200; elements capped at 200 per slide.
# Raises ValueError("a deck needs at least one slide") on an empty list.
```

---

### `deck.apply_ops(deck, ops)`

**What it does:** Applies structured operations from the AI assistant. Works on a **copy**.

**Input:** `deck: dict`, `ops: list[dict]`.

**Output:** `(new_deck, applied, rejected)`.

**Example:** (from `assist.demo()`)
```python
new, applied, rejected = apply_ops(deck, [
  {"action": "update_element", "element_id": title_id,
   "changes": {"fontSize": 52, "color": "accent"}},
  {"action": "add_element", "slide_id": slide_id,
   "element": {"type": "shape", "x": 1, "y": 6, "w": 3, "h": 0.1,
               "content": {"shape": "rect"}, "style": {"fill": "accent"}}},
  {"action": "update_element", "element_id": "hallucinated_id",
   "changes": {"text": "nope"}},
  {"action": "add_element", "slide_id": slide_id,
   "element": {"type": "image", "x": 0, "y": 0, "w": 4, "h": 4,
               "content": {"url": "https://tracker.example/pixel.png"}}},
])
# applied  => ["update_element", "add_element", "add_element"]
# rejected => ["update_element: no element 'hallucinated_id'"]
# new["slides"][0]["elements"][0]["style"]["size"]  == 52
# new["slides"][0]["elements"][-1]["content"]["url"] is None   # off-site URL stripped
# deck["slides"][0]["elements"][0]["style"]["size"] == 40      # the original is untouched
```

**Guards:** at most 200 ops; each wrapped in `try/except` so one failure doesn't abort the
batch; a reorder must list **every** slide id exactly once; the last slide can't be deleted;
`add_element` with no `slide_id` falls back to slide 1.

---

### `num(v, lo, hi, default=0.0)` / `color(value, template, fallback)` / `clip(s, n)` / `new_id(prefix)`

```python
num("4.2", 0, 10)            # => 4.2
num(float("nan"), 0, 10)     # => 0.0     f != f catches NaN
num(999, 0, 10)              # => 10.0    clamped
num(None, 0, 10, default=2)  # => 2

color("accent", template)    # => "#e0522c"     a token resolves through the template
color("#ff0000", template)   # => "#ff0000"     a literal stays put
color("chartreuse", template)# => the fallback token's colour

clip("a very long title…", 20)   # => "a very long title…"  with a trailing ellipsis
```

---

### Constants

| Constant | Value |
|---|---|
| `W, H` | `13.333, 7.5` — 16:9 in inches. 4:3 is 10 × 7.5; custom is any pair. |
| `TOKENS` | `primary, accent, text, muted, bg` |
| `TYPES` | `text, image, shape, line, table, chart` |
| `SHAPES` / `CHARTS` | `rect, ellipse, triangle, arrow, line` / `bar, line, pie, donut, area, scatter` |
| `CAP` | Authored budgets: title 60, subtitle 120, bullet 90, stat 12, label 40, left/right 220, notes 300 |
| `VISUAL_TYPES` | `image, chart, table` |
| `NO_VISUAL_NEEDED` | `title, closing, stat` |
| `MIN_VISUAL` | `0.06` of the slide's area |
| `OPS` | The nine allowed AI actions |
| `ALIAS` | 20 flat keys → nested slots |

---

## 7. End-to-End Walkthroughs

### 7.1 A bullets slide with numbers, no photo available

1. The planner writes a `bullets` slide: *"API 412 ms / Worker 380 ms / Queue 95 ms"*.
2. `expand_slide` produces a heading text element and a body text element with those three
   lines, plus an empty image element (its kind is in `IMAGE_KINDS`).
3. [`image-engine`](image-engine.md) finds nothing — no provider keys. The image element's
   `content.url` stays `None`.
4. `ensure_visuals`: kind is `bullets`, not in `NO_VISUAL_NEEDED`.
5. `has_visual`: the image has no URL → doesn't count. No chart, no table, one shape at most
   → **`False`**.
6. `add_visual`:
   - `_body` → the three-line box (biggest by area, below the heading).
   - `sides` → nothing within 0.3 inches of its `y` → not a comparison.
   - `_figures` → three lines, all with numbers, **one unit** (`ms`) →
     `[("API", 412.0), ("Worker", 380.0), ("Queue", 95.0)]`.
   - The body sits in the left half, so the chart goes at `x = 7.0`; the body narrows to 5.4.
   - A `chart` element is appended, through `clean_element`.
   - Returns `"chart"`.
7. Report entry: `{"slide": 3, "visual": "chart", "repaired": True}`.
8. `ppt.render` draws it as a **native PowerPoint chart** with editable data — the numbers
   were already on the slide as prose; this plots them.

---

### 7.2 A two-column slide becomes a table

1. A `two_col` slide expands to a heading plus two text boxes, side by side at the same `y`.
2. No image (`two_col` isn't in `IMAGE_KINDS`).
3. `has_visual` → `False`.
4. `add_visual`:
   - `_body` → the larger of the two columns.
   - `sides` → the other column, within 0.3 inches of the body's `y` → **found**.
   - Because `sides` is non-empty, `_figures` is **skipped entirely** — there is no free half
     of the slide to put a chart in.
   - The two columns are paired left-first (whichever the body turned out to be), split into
     lines, and zipped into rows (capped at 8, padded).
   - **Both text elements are removed** and one `table` element replaces them, spanning
     `TEXT_W`.
   - Returns `"table"`.

---

### 7.3 A single-sentence slide is left alone

1. A `bullets` slide whose body is *"Retention is the problem."*
2. `has_visual` → `False`.
3. `add_visual`: `_body` found; `lines` has one entry; `sides` empty; `_figures` returns `[]`
   (one line, fewer than three); `len(lines) < 2` → **`return None`**.
4. Report: `{"slide": 5, "visual": "none", "repaired": False}`.

Nothing is added. *"One sentence is a sentence. Numbering it would be theatre, and theatre is
what the whole check exists to keep off the slide."* The report is honest about it rather than
padding the slide to satisfy a rule.

---

## 8. Configuration & Setup

**No environment variables.** Every tunable is a module constant — see
[§6](#constants). `deck.py` reads nothing from the environment and imports nothing outside the
standard library.

### Self-check

```bash
cd Agents/A1_pptx
python -m backend.deck
```

No API call. Covers `expand`, the validation rules, AI ops and visual coverage.

The four other self-checks in the app, none of which spend an API call:

```bash
python -m backend.assist                    # selection scoping, refused operations
python -m backend.ppt --demo                # every template, every element type -> .pptx
python -m backend.main --selftest           # routes, guards, and the whole lifecycle
python -m backend.image_engine.test_engine  # 13 checks across the image pipeline
cd frontend && npm test                     # 15 checks: the renderer and the reducer
```

`--selftest` runs the acceptance test end to end — generate → editor → hand edit → AI edit →
insert an asset → reorder → autosave → reload → export — then **reads the `.pptx` back** and
checks the position, size and colour it finds there are the ones the editor was showing.
That's the test that proves the one-document claim.

### Adding an element type

Add it to `TYPES`, give it a branch in `clean_element`, add a drawer to `ppt.DRAW`, and add a
case to `Slide.jsx`. Four places, because there are exactly two renderers and one boundary.

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **The repair pass draws, it does not illustrate** | With no photo key set, a bullets slide falls back to a numbered composition. That is an honest visual and it is not a photograph; set a provider key and the engine's picture wins, because the pass only touches slides it could not fill. |
| **`_figures` requires exactly one unit** | Correct — but it means a genuinely comparable series expressed in mixed units ("1.2 s" and "412 ms") is never charted, even though converting them would be trivial. |
| **`_body` picks by area** | A slide whose largest text box is a pull-quote rather than the bullets gets the quote repaired. |
| **The comparison detector is a 0.3-inch `y` tolerance** | Two columns deliberately offset vertically are not seen as a comparison. |
| **The composition caps at 5 lines** | A six-point list loses its sixth point when repaired. |
| **`add_visual` mutates in place and removes elements** | The table repair **deletes** both text elements. Undo is a document-level snapshot so it's recoverable, but the repair itself is not reversible in isolation. |
| **Rotate-then-resize grows along the slide's axes**, not the element's | |
| **No PPTX import** | The pipeline is obvious and nothing in the model blocks it; it isn't written. |
| **No PDF or PNG export** | Both need a headless browser or LibreOffice. |
| **Single editor per deck** | Stable ids and UI state kept out of the document mean the model doesn't assume otherwise, but there's no presence, locking or merge — two tabs last-write-wins each other. |
| **Desktop-first** | Below 860px the editor chrome hides itself and says so; presenting and exporting still work. |
| **`clean_element` silently drops unknown style keys** | Correct for safety, but a client sending a typo'd key gets no feedback. |
| **200-element and 200-slide caps are silent** | Beyond them, content is truncated without a message. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [Element](../GLOSSARY.md#element),
  [Deck / Presentation](../GLOSSARY.md#deck--presentation), [`expand()`](../GLOSSARY.md#expand),
  [Theme token](../GLOSSARY.md#theme-token),
  [Visual coverage](../GLOSSARY.md#visual-coverage), [Slot](../GLOSSARY.md#slot)
- [`deck-planner`](deck-planner.md) — produces the authored plan `expand()` consumes, and
  renders the elements this file defines
- [`image-engine`](image-engine.md) — runs **before** `ensure_visuals`, so only unfilled
  slides are repaired
- [`deck-assist`](deck-assist.md) — the caller of `apply_ops`, and why `clean_element` is the
  trust boundary
- [`agents/README.md`](README.md) — and `Agents/A1_pptx/README.md` §"The one decision
  everything else follows from" and §"Every slide has something to look at"
