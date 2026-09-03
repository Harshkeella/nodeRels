# Agent: Deck Assist ("Ask AI")

> `Agents/A1_pptx/backend/assist.py` — the in-editor assistant. The model reads the document
> and proposes **operations**; it never writes to it.

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

Select an element, a slide, or nothing, type what you want, and the deck changes. The rule
this module exists to enforce is stated in its own first line: **an LLM response is untrusted
input.**

So the model never receives the document to rewrite. It receives a *structured summary* of
what the user selected and returns a list of **operations** — `update_element`,
`add_element`, `delete_slide` and six others — each naming an id that must already exist.
Those operations go through `deck.apply_ops`, which validates every one against the document,
runs every element through `deck.clean_element`, and works on a **copy**. What comes back is
the resulting document plus an honest list of what was refused.

The consequences are concrete. An invented element id is refused and reported. An off-site
image URL is stripped. An unknown action is rejected. And because `apply_ops` copies first, a
bad answer costs you nothing but a message saying so — the deck you were working on is not
mutated until a batch succeeds.

Because it's the same document the editor writes, AI edits and hand edits compose: move a
box, ask AI to rebalance the slide, then nudge it again.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **`ppt._ask`** | Shared with [`deck-planner`](deck-planner.md), passing the operations schema instead of the deck schema. One Groq integration, one retry policy, one place the key is read. |
| **Strict constrained decoding** | The model cannot emit an action outside `deck.OPS` or a field outside the schema. |
| **`deck.apply_ops` / `deck.clean_element`** | The actual trust boundary. This module produces operations; `deck.py` decides what lands. |
| **`copy`, `json`** (stdlib) | That's the whole import list. |

218 lines, including its self-check.

---

## 3. Folder & File Structure

```
Agents/A1_pptx/backend/
├── assist.py                  # 218 lines
│   ├── SCHEMA                 # {summary, operations[{action, element_id, slide_id, changes}]}
│   ├── PROMPT                 # geometry, tokens, selection scope, the rules
│   ├── _slim(e)               # what the model sees of an element
│   ├── context(deck, selection)   # SCOPE + STATE
│   ├── edit(deck, ask, selection, template)   # THE ENTRY POINT
│   ├── _unpack(op)            # schema shape -> apply_ops shape
│   └── demo()                 # self-check, no API call
│
├── deck.py                    # OPS, ALIAS, _merge, apply_ops, clean_element
│                              #   -> visual-coverage.md
└── main.py                    # POST /api/deck/{id}/ai
```

---

## 4. How This Fits Into the Bigger Picture

```
   Editor: user selects, types "make the title bigger and add a rule under it"
        │
        ▼
   POST /api/deck/{id}/ai  {ask, selection: {elements: [...], slides: [...]}, template?}
        │
        ▼
   assist.edit(deck, ask, selection, template)
        │
        │  context(deck, selection)  → (scope sentence, structured state)
        │  ppt.fit(json(state), len(prompt))     trim to one TPM window
        │  ppt._ask([...], SCHEMA, "edit")       ← ONE Groq call, strict
        │  [_unpack(o) for o in out["operations"]]
        │
        ▼
   deck.apply_ops(deck, ops)     ← THE TRUST BOUNDARY, on a COPY
        │  every element through deck.clean_element
        │  returns (new_deck, applied, rejected)
        ▼
   {deck, report: {summary, applied, rejected, actions}}
        │
        └──► main.save(new_deck)  →  the editor re-renders; refusals are shown
```

The model's output never reaches the editor's state without passing through
`deck.clean_element`. There is no other path.

---

## 5. Core Concepts & Key Components

### Operations, not state

The prompt's first line: *"Return operations that change it, not prose."* The nine allowed
actions are `deck.OPS`:

```
update_element  add_element  delete_element
update_slide    add_slide    delete_slide
reorder_slides  set_theme
```

A model that returns the whole deck would be handing back a document that has to be trusted
wholesale. A model that returns `{"action": "update_element", "element_id": "text_123",
"changes": {"fontSize": 44}}` is making a proposal against an id that either exists or
doesn't.

### Selection scoping is what makes "this" mean anything

`context()` narrows in three tiers:

| Selection | `owners` | `scope` sentence |
|---|---|---|
| Elements | the slides containing them | `"2 element(s) on slide Growth: text_1, text_2"` |
| Slides | those slides | `"1 whole slide(s): Growth"` |
| Nothing | every slide | `"nothing -- operate on the whole deck (9 slides)"` |

**A selected element still ships its whole slide** — the model cannot balance a layout it
cannot see. And when the scope is narrower than the deck, `state["deck_outline"]` is added so
the model knows where this sits; with nothing selected it's omitted, because there's nothing
to orient against.

### `_slim()` is a prompt budget decision

Full geometry — the model has to place things — but text truncated to 280 characters, and
style keys that are `None`, `False` or `0` dropped. The comment: *the state block is the whole
prompt budget on a big slide, and an untouched default teaches the model nothing.*

Per type it sends what matters: `text` for text, `alt` for an image (never the URL), rows for
a table, the full chart spec for a chart, the shape name for a shape.

### Flat or nested changes both work

`deck.ALIAS` maps 20 flat keys onto their nested slots:

```python
{"fontSize": ("style", "size"), "text": ("content", "text"),
 "color": ("style", "color"), "rows": ("content", "rows"), …}
```

The comment calls it *a kindness to the model*: it may send `{"text": "…"}` or
`{"fontSize": 44}` rather than `{"style": {"size": 44}}`, and both land in the right slot.
The prompt says both work, so this is a documented affordance rather than a silent fallback.

### `_unpack` exists because strict mode forces every key

Strict decoding requires every property to be `required`, so `add_element` arrives with its
element in `changes` rather than in `element`. `_unpack` moves it to where `apply_ops` looks —
same for `add_slide` (`slide`), `reorder_slides` (`order`) and `set_theme` (`template`). A
schema constraint leaking into a shape adapter, handled in eight lines.

### The prompt tells the model what it *cannot* do

> *"You cannot create an image from nothing: there is no URL you can invent. To ask for a
> picture, leave the existing image element alone or reposition it."*

That's the interesting one. The model **will** try to invent an image URL, and
`clean_element` will strip it — so the prompt says so up front, turning a silent refusal into
a constraint the model can work within. The self-check asserts the strip happens anyway.

Other rules worth noting: preserve the user's facts; keep elements inside the slide bounds;
don't overlap text with an image (move the text, not the photo); prefer editing to
delete-and-re-add so ids the user has selected or moved survive; **no operation at all is a
valid answer.**

### Refusals are reported, not swallowed

`apply_ops` returns `(new_deck, applied, rejected)` where `rejected` is a list of strings
naming the action and the reason. `edit` passes them straight through to the browser. A user
whose request was partly refused sees which part and why, rather than a deck that quietly
didn't change.

---

## 6. Function & Component Reference

---

### `edit(deck, ask, selection=None, template=None)`

**What it does:** Runs one AI edit.

**Input:**

| Param | Type | Example |
|---|---|---|
| `deck` | `dict` | the current document |
| `ask` | `str` | `"make the title bigger and add a rule under it"` (clipped to 2000) |
| `selection` | `dict \| None` | `{"elements": ["text_123"]}` or `{"slides": [...]}` |
| `template` | `str \| None` | force a theme alongside the edit |

**Output:** `(new_deck, report)` where `report` is
`{summary, applied, rejected, actions}`.

**Example:**
```python
new, report = edit(deck, "make the title bigger and add a rule under it",
                   {"elements": ["text_1755012345001"]})
# report => {"summary": "Enlarged the title and added an accent rule beneath it.",
#            "applied": 2, "rejected": [], 
#            "actions": ["update_element", "add_element"]}
```

**Notes:** *"Never raises on a bad model response and never returns a half-applied document:
`apply_ops` works on a copy."* The prompt is built **twice** — once with an empty `state` to
measure the scaffold, then again with `ppt.fit(body, len(prompt))` — the same TPM-budget
trick [`deck-planner`](deck-planner.md#fittext-used) uses.

---

### `context(deck, selection)`

**What it does:** Builds the structured state for the ask, and a sentence describing what's
selected.

**Input:** `deck: dict`, `selection: dict` — `{"elements": [...], "slides": [...]}`.

**Output:** `(scope: str, state: dict)`.

**Example:** (asserted in the module's `demo()`)
```python
scope, state = context(deck, {})
# scope => "nothing -- operate on the whole deck (5 slides)"
# state => {"deck_title": …, "theme": "corporate", "slides": [all 5]}
#          no "deck_outline" — nothing selected, so nothing to orient against

scope, state = context(deck, {"slides": [slide_2_id]})
# scope => "1 whole slide(s): Growth"
# state => {..., "slides": [just that one], "deck_outline": ["1. …", "2. Growth", …]}

scope, state = context(deck, {"elements": [title_element_id]})
# scope => "1 element(s) on slide Title: text_1755012345001"
# state["slides"] => [the WHOLE slide containing it]  — layout needs context
```

**Notes:** Slide notes are truncated to 200 characters. Elements go through `_slim`.

---

### `_slim(element)`

**What it does:** What the model sees of one element.

**Input:** `e: dict` — a cleaned element. **Output:** `dict`.

**Example:**
```python
_slim({"id": "text_1", "type": "text", "x": 0.9, "y": 1.2, "w": 11.5, "h": 1.4,
       "rotation": 0, "locked": False, "hidden": False,
       "content": {"text": "Revenue grew 34% but churn doubled"},
       "style": {"size": 40, "color": "primary", "bold": True, "align": "left",
                 "valign": "top", "fit": "cover", "opacity": 1, "italic": False}})
# => {"id": "text_1", "type": "text", "box": [0.9, 1.2, 11.5, 1.4],
#     "style": {"size": 40, "color": "primary", "bold": True, "align": "left",
#               "opacity": 1},
#     "text": "Revenue grew 34% but churn doubled"}
#     dropped: rotation/locked/hidden (falsy), valign+fit (never useful to the model),
#              italic (False)
```

**Notes:** An image sends its `alt` (or `"photo"`), **never its URL** — the model can't
usefully act on a `/media/` path and sending it invites invention. `rotation` and `hidden`
appear only when truthy.

---

### `_unpack(op)`

```python
_unpack({"action": "add_element", "changes": {"type": "shape", …}})
# => {..., "element": {"type": "shape", …}}          apply_ops looks for `element`

_unpack({"action": "reorder_slides", "changes": {"order": ["a", "b"]}})
# => {..., "order": ["a", "b"]}

_unpack({"action": "set_theme", "changes": {"template": "startup"}})
# => {..., "template": "startup"}
```

---

### `deck.apply_ops(deck, ops)`

`Agents/A1_pptx/backend/deck.py` — the trust boundary. Documented in full in
[`visual-coverage`](visual-coverage.md#deckapply_opsdeck-ops); summarised here because it is
what makes this agent safe.

**Output:** `(new_deck, applied, rejected)`.

**Guarantees:**

| Guarantee | How |
|---|---|
| The input deck is never mutated | `deck = copy.deepcopy(deck)` first |
| An unknown id is refused with a reason | `find(eid)` returns `(None, None)` → `ValueError("no element 'x'")` |
| An unknown action is refused | `else: raise ValueError("unknown action %r")` |
| Every element is cleaned | `clean_element(_merge(existing, changes), existing)` |
| A reorder must be a permutation | `sorted(order) != sorted(index)` → rejected |
| The last slide can't be deleted | `len(deck["slides"]) < 2` → rejected |
| At most 200 operations | `for op in (ops or [])[:200]` |
| One bad op doesn't abort the batch | Each is wrapped in `try/except`; the reason is appended to `rejected` |

---

## 7. End-to-End Walkthroughs

### 7.1 A successful edit with one refusal

This is the module's own `demo()`, run as `python -m backend.assist` — a **real** model
response shape, applied without any model involved.

1. User selects the title element on slide 1 and asks for a bigger title plus a rule.
2. `context(deck, {"elements": [title_id]})` → scope names the element and its slide; state
   carries the whole slide, slimmed, plus a `deck_outline`.
3. `ppt._ask(..., SCHEMA, "edit")` returns four operations:
   ```json
   [{"action": "update_element", "element_id": "<title>",  "changes": {"fontSize": 52, "color": "accent"}},
    {"action": "add_element",    "slide_id": "<slide 1>",  "changes": {"type": "shape", "x": 1, "y": 6, "w": 3, "h": 0.1, "content": {"shape": "rect"}, "style": {"fill": "accent"}}},
    {"action": "update_element", "element_id": "hallucinated_id", "changes": {"text": "nope"}},
    {"action": "add_element",    "slide_id": "<slide 1>",  "changes": {"type": "image", "x": 0, "y": 0, "w": 4, "h": 4, "content": {"url": "https://tracker.example/pixel.png"}}}]
   ```
4. `_unpack` moves each `add_element`'s payload from `changes` to `element`.
5. `apply_ops` on a **copy**:
   - Op 1: id found. `_merge` maps `fontSize → style.size` and `color → style.color` via
     `ALIAS`. `clean_element` validates. **Applied.**
   - Op 2: shape added. **Applied.**
   - Op 3: `hallucinated_id` not found → `rejected: ["update_element: no element
     'hallucinated_id'"]`.
   - Op 4: element added, but `clean_element` sees a `content.url` that isn't `/media/`,
     `/uploads/` or `data:image/` → **`url` becomes `None`**. The element lands as an empty
     image box; the tracker URL does not survive.
6. `applied == ["update_element", "add_element", "add_element"]`, one rejection.
7. The self-check asserts the title's size is now 52 and its colour `accent`, that the image's
   url is `None`, and that **the original deck's title is still size 40** — `apply_ops` worked
   on a copy.
8. The browser gets the new deck, the summary, and the one refusal.

---

### 7.2 An edit with nothing selected

1. User asks *"make the whole deck more concise"* with no selection.
2. `context(deck, {})` → scope is `"nothing -- operate on the whole deck (9 slides)"`, state
   carries **every** slide, and `deck_outline` is omitted (the outline would duplicate what's
   already there).
3. `ppt.fit` trims the serialised state to what's left of the TPM window after the prompt
   scaffold. **On a large deck this is where content is lost** — see
   [§9](#9-known-limitations--open-todos).
4. The model returns a batch of `update_element` operations shortening text.
5. Every one is validated against real ids; text is clipped to 4000 chars by `clean_element`.
6. The editor re-renders. **Undo is one step**, because the whole batch is one document
   swap.

---

### 7.3 A refused batch costs nothing

1. The model returns three operations, all naming ids from a *different* deck — a stale
   context, or a confabulation.
2. `apply_ops` deep-copies, then rejects all three with reasons.
3. `new_deck` is returned — a copy identical to the original, since nothing applied.
4. `report` reads `{"applied": 0, "rejected": ["update_element: no element 'x'", …]}`.
5. `main.py` saves the (unchanged) deck and returns the report. The user sees three refusals
   and their work is exactly as it was.

---

## 8. Configuration & Setup

Shares Deck Studio's configuration — see
[`deck-planner` §8](deck-planner.md#8-configuration--setup). No variables of its own; it uses
`GROQ_API_KEY`, `GROQ_MODEL` and `GROQ_TPM` through `ppt._ask` and `ppt.fit`.

### Self-check

```bash
cd Agents/A1_pptx
python -m backend.assist
# ok - selection scoping, 3 ops applied, hallucinated id and off-site URL both refused,
#      source deck unchanged
```

**No API call.** It builds a real model-response shape by hand and runs it through the real
`apply_ops`, asserting selection scoping at all three tiers, that a hallucinated id is
refused, that an off-site image URL doesn't survive, and that the source deck is untouched.

Related checks:

```bash
python -m backend.deck            # the model: expand, validation, AI ops, visual coverage
python -m backend.main --selftest # the whole lifecycle incl. an AI edit
```

`--selftest` runs the acceptance test end to end — generate → editor → hand edit → **AI edit**
→ insert an asset → reorder → autosave → reload → export — then reads the `.pptx` back and
checks the position, size and colour it finds there are the ones the editor was showing.

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **`ppt.fit` truncates the state JSON mid-string** | On a large deck with nothing selected, the state block is cut at a character boundary and the model receives malformed JSON as its context. It usually still works; it is not guaranteed to. |
| **No conversation history** | Every ask is independent. "Now make it blue" after "make the title bigger" has no idea what "it" was. |
| **No preview or confirmation** | Operations apply immediately. Undo is the only safety net — one step, which is correct, but there's no "show me what this would do". |
| **The model never sees image URLs** | Correct for safety, but it means the assistant cannot reason about which photo is on a slide beyond its `alt` text. |
| **200-operation cap is silent** | Beyond it, extra operations are dropped without a rejection message. |
| **`ALIAS` is a fixed 20-key map** | A flat key outside it is silently ignored by `_merge` — no rejection, no effect. |
| **`summary` is not validated** | Clipped to 400 chars and displayed as-is. It's prose from a model, shown to a user. |
| **Rejections are strings, not structured** | `"update_element: no element 'x'"`. The UI can display them but can't group or act on them. |
| **Single editor per deck** | The document model doesn't assume otherwise — stable ids, UI state kept out of the document — but there's no presence, locking or merge, so two tabs on one deck last-write-wins each other. An AI edit in one tab can be silently overwritten by the other's autosave. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [Ask AI](../GLOSSARY.md#ask-ai),
  [Element](../GLOSSARY.md#element), [Deck / Presentation](../GLOSSARY.md#deck--presentation),
  [Theme token](../GLOSSARY.md#theme-token)
- [`visual-coverage`](visual-coverage.md) — `deck.py`: `apply_ops`, `clean_element`, `OPS`,
  `ALIAS`, and the rest of the trust boundary
- [`deck-planner`](deck-planner.md) — shares `_ask` and `fit`; the schema discipline is the
  same idea applied to generation
- [`image-engine`](image-engine.md) — the other producer of elements the assistant can move
- [`agents/README.md`](README.md) — and `Agents/A1_pptx/README.md` §"Ask AI" and §"Security"
