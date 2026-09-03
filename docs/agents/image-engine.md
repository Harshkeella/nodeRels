# Agent: Image Engine

> `Agents/A1_pptx/backend/image_engine/` — finds a licensed, correctly-shaped, non-duplicate
> photograph for one slide.

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

Eight stages, one public coroutine:

```
plan → search → licence filter → rank → fetch → validate → dedupe → select
```

An LLM call turns a slide into a **visual brief** and the search queries to satisfy it. Three
stock providers are queried concurrently and their results normalised to one candidate shape.
Anything outside the allowed licence set is dropped before ranking — decks get shipped to
customers, so a share-alike image is a legal problem, not a ranking problem. What survives is
scored on four signals, downloaded in score order, and each download is really decoded by
Pillow to catch truncation and error pages served as images. A perceptual hash stops slide 4
reusing slide 2's photo.

The property that matters most: **it never raises for an empty result.** A slide with no
usable image returns zero selections and a reason, and the deck still renders. Every stage is
allowed to fail — no API keys, a rate-limited provider, an unreachable model, a 404 on a
download — and each failure narrows the pool rather than breaking the pipeline.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **`urllib.request`** (stdlib) | Provider APIs and the Groq planning call. *"Groq and the stock providers are plain stdlib urllib — no SDK, no http client."* |
| **`asyncio`** | Providers are queried concurrently; blocking calls go through `to_thread`. |
| **Pillow** ≥10.0 | Decode, validate, quality-score, perceptual-hash. `ImageStat` and `ImageFilter` supply every quality signal — no ML model. |
| **`pydantic`** ≥2.0 | The candidate/plan/selection schemas. *Nothing downstream ever sees a provider's own JSON shape.* |
| **FastAPI** | Two routes mounted into the main app. Having a FastAPI surface already is why the whole app's API layer cost one file. |

**Providers:** Unsplash, Pexels, Pixabay — all free tiers, no card. Optional and independent:
with none set you get text decks, with any one set you get illustrated ones.

---

## 3. Folder & File Structure

```
Agents/A1_pptx/backend/image_engine/
├── __init__.py / __main__.py    # CLI: python -m backend.image_engine "Slide title" --debug
├── config.py                    # 75 lines — EVERY tunable, all from the environment
│   ├── KEYS / ROUTES / DEFAULT_ROUTE / ALLOWED_LICENSES
│   ├── PER_QUERY / MAX_CANDIDATES / HTTP_TIMEOUT / SLIDE_BUDGET
│   ├── MIN_BYTES / MAX_BYTES / FORMATS / MIN_PX
│   ├── FINAL_WEIGHTS / QUALITY_WEIGHTS / ASPECT_GAMMA / DUP_HAMMING
│   └── CACHE / MODEL / GROQ_URL / PLAN_TIMEOUT
│
├── schemas.py                   # 133 lines
│   └── ImageSlot · SlideRequest · Query · VisualPlan · ImageCandidate
│       · SelectedImage · SlideImages
│
├── plan.py                      # 184 lines
│   ├── SCHEMA / PROMPT / _ask
│   ├── STOP                     # ~90 stopwords for the fallback
│   ├── fallback_plan(slide)     # NO LLM — deterministic, must never fail
│   ├── make_plan(slide)         # LLM, degrading to fallback on ANY failure
│   └── route(plan, available)   # a dict lookup, not an agent
│
├── providers.py                 # 140 lines
│   ├── unsplash / pexels / pixabay      # each -> list[ImageCandidate]
│   ├── PROVIDERS / available()
│   └── search_all(queries, providers, …)   # concurrent, failure-contained
│
├── rank.py                      # 155 lines
│   ├── semantic_score(cand, plan)       # weighted token overlap vs the BRIEF
│   ├── aspect_score(ratio, target, γ)   # centre-crop survival, sharpened
│   ├── quality_score(img)               # resolution · sharpness · exposure · colour
│   ├── dhash(img) / is_duplicate(h, seen, threshold)
│   ├── diversity_score(cand, chosen)    # metadata-level, pre-download
│   └── final_score(scores)              # the weighted blend
│
├── engine.py                    # 317 lines
│   ├── fetch / validate / _dedupe / _licensed / _trigger
│   ├── deck_state / _save_deck_state    # per-presentation dedupe scope
│   ├── collect(slide, log)              # plan → search → rank (nothing downloaded)
│   ├── select_for_slide(slide)          # THE PUBLIC COROUTINE
│   ├── select_for_slide_sync(slide)
│   └── from_deck(deck, index, pid, slots)
│
├── api.py                       # POST /images/search · POST /images/select · GET /health
├── test_engine.py               # 353 lines, 13 checks, no network
├── README.md                    # the engine's own documentation
└── cache/                       # images/ and presentations/<pid>.json
```

---

## 4. How This Fits Into the Bigger Picture

```
   ppt.illustrate(deck, pid)              ← THE CALLER OWNS THE LAYOUT
        │   for each slide whose kind ∈ IMAGE_KINDS ("title", "closing", "bullets")
        │   builds an ImageSlot: role, aspect_ratio "4:5", min pixels
        ▼
   engine.from_deck(deck, i, pid, slots) → SlideRequest
        ▼
   engine.select_for_slide_sync(request)
        │
        │  collect():  plan.make_plan → plan.route → providers.search_all
        │              → _dedupe → _licensed → rank (pre-download) → cap at MAX_CANDIDATES
        │
        │  per slot, in score order:  fetch → validate → dhash dedupe
        │              → re-score with REAL pixels → select, break
        ▼
   SlideImages{selected_images, metadata{log, queries, candidates_*}}
        │
        └─► ppt sets the image element's content.url = "/media/<path>"
                │
                └─► SSE "image" event per slide
                    then deck.ensure_visuals() repairs only what this could NOT fill
```

**The caller owns the layout, so the caller supplies the slots.** The engine has no opinion
about where a photo goes — `ppt.py` decides which slide kinds get one and at what ratio, and
the engine answers the question it was asked.

**`presentation_id` is the dedupe scope.** Reusing one across runs makes every good image look
"already used", so the server mints a fresh id per deck (`ppt.deck_pid`).

**Two HTTP routes** are mounted into the main app: `POST /images/search` (plan, search and
rank — **downloads nothing**) and `POST /images/select` (the whole pipeline). `GET /health`
reports which providers actually have keys and the weights in force.

---

## 5. Core Concepts & Key Components

### The fallback plan must never fail

`make_plan` tries one LLM call and falls back on **any** exception — no key, rate limit, bad
JSON, timeout, an empty query list. `fallback_plan` is *"deliberately dumb and
deterministic: this runs when the model is unavailable, so it must never itself fail. Worse
queries beat no images."*

It pulls concrete words out of the slide (regex, ~90 stopwords, de-duped in order) and builds
four queries from them: the top three concepts, two concepts plus the topic, concepts 3–5,
and `"<topic> professional photography"`. No model, no network.

`plan.source` records which path ran (`"llm"` or `"fallback"`) and surfaces in the metadata,
so a deck of mediocre images has a traceable cause.

### Routing is a dict lookup, not an agent

`config.ROUTES[plan.image_type]` orders the providers for that kind of picture, then
`route()` filters to those with keys and appends any remaining available ones. The README is
explicit: *"A dict lookup, not an agent."*

### Licence filtering happens before ranking

`_licensed(cands, log)` drops anything outside `IMG_ALLOWED_LICENSES` (default
`unsplash, pexels, pixabay, cc0, pdm`) **before the ranker sees it**. The stated reason:
*decks get shipped to customers; a share-alike image is a legal problem, not a ranking
problem.* Ranking it at all would risk it winning.

### Four signals, every weight tunable

```
final = semantic·0.50 + quality·0.22 + aspect·0.18 + diversity·0.10
```

| Signal | Measured how |
|---|---|
| **semantic** | Weighted token overlap between the provider's own description and `plan.ranking_text()` — the **whole brief**, not the bare query. That's the point of having a brief. Saturation-spread (`1 − e^(−4·overlap)`) because raw overlap saturates low; ×0.7 if any `avoid` term appears; a small bonus for the provider's own ranking, which is *"a relevance ranking someone already paid for."* |
| **aspect** | Fraction surviving a centre crop to the slot, raised to `ASPECT_GAMMA`. 16:10 in a 16:9 slot ≈ 0.86; 4:3 ≈ 0.65. **A penalty, not a filter** — nothing is rejected for its shape. Unknown ratio scores 0.5: neutral, neither rewarded nor punished. |
| **quality** | From the decoded pixels: resolution, edge energy (sharpness), exposure, contrast — `ImageStat` and `ImageFilter`, no model. Downscaled to 512px first, because scoring a 6000px photo full-size is pure waste. `aesthetic` is a declared slot at weight 0, so a model plugs in without touching the caller. |
| **diversity** | Metadata-level, **pre-download**: same query ×0.65, same photographer ×0.55, Jaccard caption overlap ×(1 − 0.8j). Takes the *worst* penalty against anything already chosen. |

`final_score` gives a **missing** signal 0.5, not 0 — *"an unmeasured signal must not look
like a bad one."* That's what lets pre-download and post-download scoring share one function.

### Two-pass scoring, and the second pass overwrites the first

Before download, `quality` is the *declared* resolution only. After download it's the real
pixel measurement, and `aspect` is recomputed from the decoded size — the provider's declared
dimensions are sometimes wrong. Selection order comes from the pre-download score; the
recorded score is the post-download one.

### Validation catches what a status code doesn't

`validate(blob, role)` really decodes with Pillow and checks format (`JPEG/PNG/WEBP`) and
minimum pixels for the slot's role — `background` needs 1600×900, `hero` 1200×700,
`supporting` 800×600. That's what catches **truncation and error pages served as images**: a
200 response with an HTML body has a valid content-type and fails to decode.

`MIN_BYTES = 8000` catches the placeholder-or-error-page case before decoding is even
attempted.

### Two kinds of duplicate

| Kind | Detected by | When |
|---|---|---|
| Exact same asset | `(provider, id)` in `state["ids"]` | Before download — free |
| Same picture, different crop/size/quality | `dhash` within `DUP_HAMMING` Hamming distance | After download |

`dhash` is a 64-bit difference hash over a 9×8 greyscale resize. `state` persists per
presentation in `cache/presentations/<pid>.json`, which is why the dedupe scope is a deck.

### The slide budget is a wall clock

`SLIDE_BUDGET = 45` seconds. The loop checks `time.monotonic() > deadline` before each
download and stops, logging where it stopped. **Returns what it has** — a deck that renders
with three of five photos beats one that never finishes.

### Every stage logs

`SlideImages.metadata["log"]` is a list of stage lines:

```
[Visual Planner] llm | conceptual_realistic | concepts: revenue, growth, chart
[Query Generator] 4 queries: business growth chart | revenue analytics | …
[Router] conceptual_realistic -> unsplash, pexels, pixabay
[Collector] 63 hits -> 41 unique candidates
[Selected] hero unsplash/AbC123 score 0.812 (sem 0.88 qual 0.74 aspect 0.86 div 1.00)
[Validation] 3 candidate(s) rejected on inspection
```

Every rejection is attributed. `--debug` additionally returns the full candidate list with
each one's `rejected` reason.

---

## 6. Function & Component Reference

---

### `select_for_slide(slide)`

`engine.py` — **the one public coroutine.**

**What it does:** Finds, ranks and downloads one image per slot on a slide.

**Input:** `slide: SlideRequest` — `slide_number`, `slide_title`, `slide_content`,
`presentation_topic`, `presentation_id`, `image_slots`, `debug`.

**Output:** `SlideImages` — `selected_images`, `metadata`, optionally `candidates`.

**Example:**
```python
result = await select_for_slide(SlideRequest(
    slide_number=3, slide_title="Revenue grew 34% but churn doubled",
    slide_content="Growth is real. Retention is the problem.",
    presentation_topic="Q3 Board Update", presentation_id="q3-board-update",
    image_slots=[ImageSlot(slot_id="hero", role="hero", aspect_ratio="4:5")]))

# result.selected_images[0]
# => SelectedImage(slot_id="hero",
#      local_path=".../cache/images/unsplash_AbC123.jpg",
#      source="unsplash", source_url="https://unsplash.com/photos/AbC123",
#      photographer="Jane Doe", attribution="Photo by Jane Doe on Unsplash",
#      license="unsplash", search_query="business growth chart",
#      width=1600, height=2000,
#      relevance_score=0.88, quality_score=0.74,
#      aspect_ratio_score=0.86, final_score=0.812)

# result.metadata
# => {"queries_generated": [...], "plan_source": "llm",
#     "image_type": "conceptual_realistic", "visual_intent": "...",
#     "providers_used": ["unsplash", "pexels", "pixabay"],
#     "candidates_found": 63, "candidates_ranked": 41,
#     "candidates_rejected": 3, "log": [...]}
```

**Notes:** *"Never raises for an empty result."* No slots → an immediate empty result with a
log line. No provider keys → a log line and zero selections. Every download failure and
validation rejection is attributed on the candidate and counted in the metadata. `state` is
saved after every slide, so the dedupe scope persists across the deck.

---

### `select_for_slide_sync(slide)`

`asyncio.run(select_for_slide(slide))` — for `ppt.py` and the CLI, which aren't async.

---

### `collect(slide, log)`

**What it does:** Plan, search and rank — **everything before a byte is downloaded.** Shared
by `/images/search` (which stops here) and `/images/select` (which carries on).

**Output:** `(brief, order, queries, cands, found)`.

**Notes:** The candidate pool is capped **by score, not arrival order**, so the cut never
drops the best. Returns early with empty lists when no provider has a key.

---

### `make_plan(slide)` / `fallback_plan(slide)` / `route(plan, available)`

**Example:**
```python
make_plan(slide)
# => VisualPlan(visual_intent="A photograph of a modern analytics dashboard…",
#               image_type="conceptual_realistic",
#               primary_concepts=["revenue", "growth", "analytics"],
#               secondary_concepts=["dashboard", "chart"],
#               avoid=["cartoon", "text inside image", "watermark"],
#               queries=[Query(query="business growth chart", priority=1, intent="literal"),
#                        Query(query="revenue analytics dashboard", priority=2, …)],
#               source="llm")

fallback_plan(slide)     # no network, no key
# => VisualPlan(visual_intent="A professional photograph illustrating: Revenue grew 34%…",
#               image_type="conceptual_realistic",
#               primary_concepts=["revenue", "grew", "churn", "doubled", "growth"],
#               queries=[Query(query="revenue grew churn", priority=1, intent="fallback"), …],
#               source="fallback")

route(plan, ["pexels", "unsplash"])
# => ["unsplash", "pexels"]     ROUTES order, filtered to what has keys
```

**Notes:** `make_plan` truncates to 8 queries and treats an empty query list as a failure,
falling back. `fallback_plan` is a `ponytail:`-free but explicitly "deliberately dumb"
function — its whole contract is that it cannot fail.

---

### `search_all(queries, providers, per_page=None, orientation=None, log=None)`

`providers.py`

**What it does:** Queries every provider for every query, **concurrently**, and normalises the
results.

**Output:** `list[ImageCandidate]`.

**Notes:** *"A provider that raises is logged and skipped"* — one dead key or a rate-limited
provider narrows the pool, it doesn't break the search. `provider_rank` is preserved from the
provider's own ordering and feeds `semantic_score`. Adding a provider is: write one function
matching `(query, per_page, orientation, key) -> list[ImageCandidate]`, add one line to
`PROVIDERS`, add its key to `config.KEYS`. *Nothing downstream ever sees a provider's own JSON
shape.*

---

### `rank.semantic_score` / `aspect_score` / `quality_score` / `dhash` / `is_duplicate` / `diversity_score` / `final_score`

**Examples** (shapes asserted by `test_engine.py`):
```python
aspect_score(1.6, 1.777)          # 16:10 photo in a 16:9 slot  => ~0.86
aspect_score(1.333, 1.777)        # 4:3 photo in a 16:9 slot    => ~0.65
aspect_score(None, 1.777)         # unknown shape               => 0.5

quality_score(img)
# => {"resolution": 0.94, "sharpness": 0.71, "exposure": 0.88,
#     "colour": 0.62, "aesthetic": 0.0, "quality": 0.79}

h1, h2 = dhash(img), dhash(img.resize((800, 600)))
is_duplicate(h1, [h2])            # => True — same picture, different size

diversity_score(cand, [])         # => 1.0  nothing chosen yet
final_score({"semantic": 0.8})    # missing signals score 0.5, not 0
```

**Notes:** `semantic_score` carries a `ponytail:` marker naming its own upgrade: *"Phase 1 is
weighted token overlap over provider metadata. It is real signal (Unsplash alt_description
and Pixabay tags are human/curated) and costs nothing. Phase 2 replaces the body of this
function with a CLIP/SigLIP cosine between `plan.ranking_text()` and the image itself; the
signature and the 0–1 range do not move."* `diversity_score` carries the parallel one:
metadata-level now, embedding-space MMR later.

---

### `validate(blob, role)` / `fetch(cand)` / `_dedupe` / `_licensed` / `deck_state(pid)`

| Function | Behaviour |
|---|---|
| `fetch(cand)` | Downloads to `cache/images/`, fires the provider's download-trigger endpoint where required (Unsplash's API terms), returns `(path, blob)` |
| `validate(blob, role)` | `(Image \| None, reason \| None)`. Checks byte bounds, real decode, format, and `MIN_PX[role]` |
| `_dedupe(cands)` | Drops exact `(provider, id)` repeats within one search |
| `_licensed(cands, log)` | Drops anything outside `ALLOWED_LICENSES`, logging the count |
| `deck_state(pid)` | Loads/creates `cache/presentations/<pid>.json` → `{"ids": [...], "hashes": [...]}` |

---

### `from_deck(deck, index, presentation_id="", slots=None)`

Turns one slide of a `ppt.py` deck plan into a `SlideRequest`. The seam between the two
modules — and `test_engine.py` tests it explicitly, because *"the two agree on dict keys
nobody type-checks, and drift there loses every photo silently."*

---

### The schemas

| Model | Key fields |
|---|---|
| `ImageSlot` | `slot_id`, `role` (`background`/`hero`/`supporting`), `aspect_ratio`, `target_ratio`, `orientation` |
| `SlideRequest` | `slide_number`, `slide_title`, `slide_content`, `presentation_topic`, `presentation_id`, `image_slots`, `debug` |
| `VisualPlan` | `visual_intent`, `image_type`, `primary_concepts`, `secondary_concepts`, `avoid`, `queries`, `source`; `.ranking_text()` |
| `ImageCandidate` | `provider`, `id`, `url`, `width`, `height`, `ratio`, `license`, `photographer`, `query`, `provider_rank`, `scores`, `rejected`; `.text()`, `.attribution()` |
| `SelectedImage` | `slot_id`, `local_path`, `source`, `source_url`, `photographer`, `attribution`, `license`, `search_query`, dimensions, four scores |
| `SlideImages` | `slide_number`, `selected_images`, `metadata`, `candidates` |

---

## 7. End-to-End Walkthroughs

### 7.1 A slide gets its photo

1. `ppt.illustrate_slide(deck, 3, pid)` builds one `ImageSlot(role="hero",
   aspect_ratio="4:5")` — **the caller decided the layout**.
2. `from_deck` → `SlideRequest`.
3. `collect`:
   - `make_plan` → one Groq call → brief with 4 queries, `image_type =
     "conceptual_realistic"`, `source = "llm"`.
   - `route` → `["unsplash", "pexels", "pixabay"]` (those with keys, in `ROUTES` order).
   - `search_all` → 3 providers × 4 queries **concurrently** → 63 candidates.
   - `_dedupe` → 52. `_licensed` → 41.
   - Pre-download scoring on all 41; sorted; capped at `MAX_CANDIDATES`.
4. Per slot, in score order:
   - Candidate 1: already in `state["ids"]` from slide 1 → `rejected = "already used in this
     deck"`, skipped.
   - Candidate 2: `fetch` → 404 → `rejected = "download failed: HTTP 404"`, logged.
   - Candidate 3: fetched. `validate` → decodes, JPEG, 1600×2000 ≥ 1200×700 ✓.
     `dhash` not within `DUP_HAMMING` of anything used ✓. `quality_score` on real pixels;
     `aspect_score` recomputed from the decoded size; `final_score` updated. **Selected**,
     loop breaks.
5. `state` saved with the new id and hash — slide 4 can't reuse it.
6. `ppt` sets the image element's `content.url = "/media/<path>"`. **SSE `image`.**
7. `deck.ensure_visuals` later sees `has_visual` → `True` for this slide and leaves it alone.

---

### 7.2 No API keys at all

1. `collect` → `make_plan` → the Groq call may or may not succeed; suppose it does.
2. `providers.available()` → `[]`.
3. Log: `[Providers] no API keys set -- see .env.example`. `collect` returns
   `(brief, [], [], [], 0)` **immediately** — no search, no download.
4. `select_for_slide` finds no candidates; the per-slot loop iterates over nothing.
   `picked is None` → `[Selected] hero -- nothing usable found`.
5. Returns `SlideImages` with zero selections and the full log. **No exception.**
6. `ppt` leaves the image element with a `None` url.
7. `deck.ensure_visuals` → `has_visual` is `False` (an image only counts once it has a URL to
   draw) → [`visual-coverage`](visual-coverage.md) repairs the slide offline: a chart, a
   table, or a numbered composition.

**The deck still renders, and every slide still communicates visually.** That's the fallback
chain working end to end.

---

### 7.3 The slide budget runs out

1. Two slots on one slide. Slot 1 takes 30 seconds — two failed downloads and a slow third.
2. Slot 2's loop checks `time.monotonic() > deadline` (45s) before each download. After two
   more attempts the deadline passes.
3. `[Engine] slide budget spent, stopping at slot supporting`, `break`.
4. `picked is None` for slot 2 → `[Selected] supporting -- nothing usable found`.
5. Returns **one** selection. `state` is still saved, so the next slide won't reuse slot 1's
   photo.
6. Slot 2's element keeps a `None` url; `ensure_visuals` handles it if the slide now has no
   visual at all.

---

## 8. Configuration & Setup

Everything lives in `config.py` and reads from the environment — *"Nothing is hardcoded
anywhere else in the engine."* See `Agents/A1_pptx/.env.example`.

| Group | Variables | Default |
|---|---|---|
| **Providers** | `UNSPLASH_ACCESS_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY` | all blank — optional and independent |
| **Retrieval** | `IMG_PER_QUERY` / `IMG_MAX_CANDIDATES` / `IMG_HTTP_TIMEOUT` / `IMG_SLIDE_BUDGET` | 8 / 100 / 12 / 45 |
| **Licensing** | `IMG_ALLOWED_LICENSES` | `unsplash,pexels,pixabay,cc0,pdm` |
| **Final weights** | `W_SEMANTIC` / `W_QUALITY` / `W_ASPECT` / `W_DIVERSITY` | 0.50 / 0.22 / 0.18 / 0.10 — normalised, need not sum to 1 |
| **Quality weights** | `WQ_RESOLUTION` / `WQ_SHARPNESS` / `WQ_EXPOSURE` / `WQ_COLOUR` / `WQ_AESTHETIC` | 0.40 / 0.25 / 0.20 / 0.15 / **0.0** |
| **Tuning** | `IMG_ASPECT_GAMMA` / `IMG_DUP_HAMMING` / `IMG_MIN_BYTES` | 1.5 / 8 / 8000 |
| **Cache** | `IMG_CACHE` | `backend/image_engine/cache` |
| **Planner** | `GROQ_API_KEY` / `GROQ_MODEL` / `IMG_PLAN_TIMEOUT` | shared / `openai/gpt-oss-120b` / 40 |

Free tiers: Unsplash 50 req/hr demo (5000/hr production), Pexels 200 req/hr, Pixabay 100
req/min.

### CLI

```bash
python -m backend.image_engine "AI-Powered Medical Diagnosis" \
    --topic "AI in Healthcare" \
    --content "AI helps doctors analyze medical images." --debug
```

`--debug` returns every candidate with its scores and its `rejected` reason — the fastest way
to tune queries against a single slide.

### HTTP

```
POST /images/search    plan, search and rank. Downloads nothing.
POST /images/select    the whole pipeline, one image per slot.
GET  /health           which providers actually have keys, and the weights in force.
```

### Tests

```bash
python -m backend.image_engine.test_engine     # 13 checks, no framework, no network
```

Covers aspect, quality, dhash, semantic, diversity, weight normalisation, the fallback plan,
provider normalisation, **provider failure containment**, candidate hygiene, validation, the
full pipeline against a fake stack, and the `ppt.py` seam.

### Adding a provider

```python
def myprovider(query: str, per_page: int, orientation: str | None, key: str) -> list[ImageCandidate]:
    ...
PROVIDERS["myprovider"] = myprovider     # providers.py
config.KEYS["myprovider"] = "MYPROVIDER_API_KEY"
```

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **Semantic scoring is token overlap over metadata** (`ponytail:`) | Real signal — Unsplash `alt_description` and Pixabay tags are human-curated — and free. → CLIP/SigLIP cosine between the brief and the image; signature and 0–1 range don't move. |
| **Diversity is metadata-level** (`ponytail:`) | dHash catches identical images after download; near-duplicates that share no words get through. → embedding-space MMR. |
| **No aesthetic model** | `WQ_AESTHETIC` is a declared slot at weight 0, waiting for one. |
| **Quality is technical, not compositional** | Sharpness, exposure and contrast say nothing about whether the subject is centred, cropped badly, or has text baked into it. |
| **`avoid` is a soft ×0.7 penalty** | A watermarked image with a strong semantic match can still win. |
| **The image cache never evicts** | `cache/images/` grows without bound; nothing prunes it. |
| **`presentation_id` state is a JSON file** | Two concurrent generations on one id would race the read-modify-write. |
| **The whole blob is held in memory** | Up to `MAX_BYTES` = 25 MB per candidate, per download attempt. |
| **No retry on a transient download failure** | One 503 and the candidate is rejected permanently for this slide. |
| **The slide budget is wall-clock, not per-stage** | A slow planning call eats the download budget. |
| **Three hardcoded providers** | Adding one is easy but is a code change, not configuration. |
| **`fetch` is sync, run in a thread** | Downloads within a slot are sequential, not concurrent — the loop needs the previous result to decide whether to continue. |
| **One planning call per slide** (`ponytail:`) | On a rate-limited free tier the engine waits the TPM window out once rather than silently dropping to keyword queries for every slide after the third. → one batched call planning every slide at once removes the wait entirely; *"worth doing when decks routinely carry more than ~5 images."* |
| **Provider search is sync urllib in a thread pool** (`ponytail:`) | Real concurrency, and the repo ships with one dependency. Ceiling: a thread per in-flight request, fine at ~15–40 searches per slide. → swap in `httpx` behind `search_all()` above ~100 concurrent. |
| **`plan._ask` duplicates `ppt._ask`** (`ponytail:`) | Deliberate: reusing it would make the image engine import the renderer — *"wrong direction for a subsystem meant to be reusable. 25 lines of urllib is cheaper than that inversion."* The cost is that the two 429-handling paths must be kept in step by hand. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [Slot](../GLOSSARY.md#slot),
  [Element](../GLOSSARY.md#element), [Deck / Presentation](../GLOSSARY.md#deck--presentation),
  [Visual coverage](../GLOSSARY.md#visual-coverage)
- [`deck-planner`](deck-planner.md) — `illustrate()`/`illustrate_slide()`, which own the slots
- [`visual-coverage`](visual-coverage.md) — what runs **after** this, repairing only the
  slides it could not fill
- [`deck-assist`](deck-assist.md) — why the assistant never sees an image URL
- [`agents/README.md`](README.md) — and `Agents/A1_pptx/backend/image_engine/README.md`, the
  engine's own documentation
