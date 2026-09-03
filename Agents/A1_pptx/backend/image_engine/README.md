# image_engine

Finds a licensed, correctly-shaped, non-duplicate photograph for one slide.

```
plan → search → licence filter → rank → fetch → validate → dedupe → select
```

One public coroutine, `engine.select_for_slide()`, plus `select_for_slide_sync()` for
callers that are not async. It never raises for an empty result: a slide with no usable
image returns zero selections and a reason, and the deck still renders.

## The stages

| Stage | File | What it does |
|---|---|---|
| Plan | `plan.py` | One LLM call turns a slide into a visual brief *and* the search queries. No key, rate limit or bad JSON — `fallback_plan()` builds queries from the slide text alone, so the engine never goes dark because a model did. |
| Route | `plan.py` | `config.ROUTES[image_type]` orders the providers. A dict lookup, not an agent. |
| Search | `providers.py` | Unsplash, Pexels, Pixabay, normalised to `ImageCandidate`. All queried concurrently; a provider that raises is logged and skipped. |
| Licence | `engine.py` | Anything outside `IMG_ALLOWED_LICENSES` never reaches the ranker. Decks get shipped to customers; a share-alike image is a legal problem, not a ranking problem. |
| Rank | `rank.py` | `semantic · 0.50 + quality · 0.22 + aspect · 0.18 + diversity · 0.10`, every weight tunable by env var. |
| Validate | `engine.py` | Real decode via pillow, format and minimum pixels per slot role. Catches truncation and error pages served as images. |
| Dedupe | `rank.py` | dHash within a Hamming radius. Slide 4 cannot reuse slide 2's photo. |

## Adding a provider

Write one function, add one line to `providers.PROVIDERS`, add its key to `config.KEYS`:

```python
provider(query: str, per_page: int, orientation: str | None, key: str) -> list[ImageCandidate]
```

Nothing downstream ever sees a provider's own JSON shape.

## Using it

From the deck renderer — the caller owns the layout, so the caller supplies the slots:

```python
ppt.illustrate_slide(deck, index, presentation_id)   # one slide
ppt.illustrate(deck)                                 # all of them
```

From the command line, to tune queries against a single slide:

```bash
python -m backend.image_engine "AI-Powered Medical Diagnosis" \
    --topic "AI in Healthcare" \
    --content "AI helps doctors analyze medical images." --debug
```

Over HTTP — both routes are mounted into the main app:

```
POST /images/search    plan, search and rank. Downloads nothing.
POST /images/select    the whole pipeline, one image per slot.
GET  /health           which providers actually have keys, and the weights in force.
```

## Configuration

Every tunable lives in `config.py` and reads from the environment — see `.env.example` at
the repo root. Nothing is hardcoded anywhere else in the engine.

`presentation_id` is the dedupe scope. Reusing one across runs makes every good image
look "already used", so the server mints a fresh id per deck.

## Tests

```bash
python -m backend.image_engine.test_engine
```

13 checks, no framework, no network. Includes the `ppt.py` seam — the two agree on dict
keys nobody type-checks, and drift there loses every photo silently.
