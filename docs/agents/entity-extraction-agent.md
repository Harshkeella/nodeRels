# Agent: Entity Extraction Agent

> `backend/app/services/gliner_extract.py` + `graph_schema.py` — a local encoder that finds
> entities in a chunk, and the name fold that decides whether two mentions are one node.

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

---

## 1. Overview

Every chunk of every ingested document passes through this agent, and what it produces is
the knowledge graph. It reads a chunk, finds the people, organizations, places, products,
technologies, events, concepts and dates in it, and reports them plus the relationships
between them.

It does that with a **local encoder**, not a language model. GLiNER scores text spans
against a supplied list of labels in a single forward pass — no API, no rate limit, no
quota. That swap is the reason a 200-page book can be ingested at all: the LLM path was
bound to roughly two chunks per minute against a free-tier token budget, so a large document
never finished and failed on quota instead.

The integration is a disguise. LightRAG thinks it's calling a language model — this agent is
registered as the `extract` role, receives LightRAG's own JSON extraction prompt, pulls the
chunk text back out of it with a regex, and returns JSON in exactly the shape an LLM would
have. Nothing downstream changes: LightRAG still does the parsing, merging, graph upsert and
vector writes.

The second half of the agent is quieter and matters just as much. LightRAG merges entities
by **exact string match**, so `Microsoft`, `microsoft`, `the Microsoft` and `LIONEL_MESSI`
would be four nodes for two things unless something folds them first. `canonical_name()` is
that fold, and because every write path routes through it, the split never reaches the
graph. This is the project's entity resolution.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **GLiNER** (`urchade/gliner_small-v2.1`) | Zero-shot NER: give it labels at inference time and it scores spans against them. No fine-tuning, no training data, and the label set is a config value. ~250 MB. |
| **`re`** (stdlib) | Four jobs: pulling the chunk out of LightRAG's prompt, masking URLs, splitting sentences, and the canonicalization folds. |
| **`bisect`** (stdlib) | Maps an entity's character offset back to its sentence in O(log n). |
| **`asyncio.to_thread`** | GLiNER is synchronous and CPU-bound; this keeps it off the event loop. |
| **`threading.Lock`** | Chunks extract from a thread pool, so two can race the first model load and pull the weights into memory twice. |

No spaCy, no transformers pipeline, no NER training. `EXTRACTION_BACKEND=llm` restores the
original Groq/Ollama path for machines that can't download the model.

---

## 3. Folder & File Structure

```
backend/app/services/
├── gliner_extract.py          # 311 lines
│   ├── _INPUT_TEXT_RE / _DESCRIPTION_LIST_RE   # the two prompts the role serves
│   ├── _URL_RE / _TLD / _mask_urls(text)       # URL soup suppression
│   ├── _split_sentences / _windows             # ~900-char windows for the encoder
│   ├── extract_records(text, predict)          # THE LOGIC — pure, injectable predict
│   ├── summarize_descriptions(jsonl)           # the merge prompt, without an LLM
│   ├── _get_model() / _predict(windows)        # lazy load behind a lock
│   ├── gliner_extract(prompt, …)               # the LightRAG "extract" role func
│   └── warmup()
│
├── graph_schema.py            # 368 lines — see BACKEND.md for the full reference
│   ├── canonical_name(raw)    #   THE ENTITY RESOLVER
│   ├── canonical_label(raw)
│   └── NODE_LABELS / REL_TYPES / upsert_node / upsert_edge / flush
│
└── test_gliner_extract.py     # 136 lines
```

---

## 4. How This Fits Into the Bigger Picture

```
   ingestion.ingest_text → rag.ainsert()
        │
        │  LightRAG: chunk (1024 tok) → embed → per chunk, call the "extract" role
        ▼
   gliner_extract(prompt, …)          ← registered by llm-router's get_rag()
        │
        ├─ prompt is a chunk-extraction prompt  → extract_records()
        ├─ prompt is a description-merge prompt → summarize_descriptions()
        └─ prompt is a gleaning prompt          → EMPTY_RESULT
        │
        ▼  '{"entities": [...], "relationships": [...]}'
   LightRAG parses, merges across chunks, upserts nodes and edges,
   writes entity/relationship vectors → HybridQdrantStorage
        │
        ▼
   source_graph.register() adds the Source Supernode over the result
```

**Registered by:** [`llm-router`](llm-router.md)'s `get_rag()`, as
`RoleLLMConfig(func=gliner_extract, max_async=GLINER_MAX_ASYNC, …)` when
`EXTRACTION_BACKEND=gliner`.

**Consumed by:** everything. The entities it names become graph nodes, which become the
`sources`/`evidence` a chat answer cites, the nodes the Graph Explorer draws, and the
`HAS_VALUE` targets a spreadsheet column links to. Its `canonical_name` output is also what
[`multi-hop-planner`](multi-hop-planner.md) collects as seed keywords and what
[`tabular_graph`](../BACKEND.md#tabular_graphprojectrag-workbook-doc_id) folds cell values
through to find them.

---

## 5. Core Concepts & Key Components

### The role serves three prompts, and two of them aren't extraction

Taking over LightRAG's `extract` role means taking over **everything** that role serves:

| Prompt | Detected by | Handled by |
|---|---|---|
| Chunk extraction | `_INPUT_TEXT_RE` | `extract_records` |
| Description summarization | `_DESCRIPTION_LIST_RE` | `summarize_descriptions` |
| Gleaning ("continue extraction") | neither matches | `EMPTY_RESULT` |

The middle one is the trap. LightRAG fires a summarization prompt through the *same* role
whenever an entity accumulates enough description fragments to merge. Miss it and **every
popular entity in a book gets `{"entities": [], …}` as its description** — the extractor's
own output pasted in as prose. The gleaning case is correct to return nothing: a single-pass
encoder has nothing to add on a second look.

There's a fourth case: text starting with
[`SUMMARY_HEADER`](../BACKEND.md#build_summaryworkbook) is a spreadsheet summary and returns
`EMPTY_RESULT`. Running the extractor over it produced a second, worse copy of the
structure — `VARCHAR`, `BIGINT` and `categorical` as entities, wired to column names. The
deterministic version written by `tabular_graph` is the one that's right.

### The name fold is the entity resolver

`canonical_name()` does four things, and each was worth doing:

| Fold | Example | Why |
|---|---|---|
| Separators → space | `LIONEL_MESSI` = `Lionel-Messi` = `Lionel Messi` | A caption, a URL slug and prose are one person. **35 duplicate groups in a single ingest.** |
| Leading article dropped | `the Microsoft` → `MICROSOFT` | Only a *whole* article: `Theodore` stays `THEODORE`. |
| Trailing possessive dropped | `Microsoft's` (both apostrophes) → `MICROSOFT` | |
| Uppercase | `microsoft` → `MICROSOFT` | LightRAG's own convention, and the only fold that's deterministic without a cross-chunk registry. |

It runs **before** the records reach LightRAG, which is the only place it can run — LightRAG
merges by exact string, so a split that gets past this point is permanent.

`canonical_label()` is the same idea for types: strip punctuation, remove whitespace and
underscores, lowercase. Not a style preference — LightRAG normalizes every extracted type to
`.replace(" ", "").lower()` before writing, so anything written *past* LightRAG (the tabular
and code projections) must match or the legend splits `person` from `Person`.

### URL masking is upstream of the duplicate-node bug

A reference section is URL soup, and GLiNER scores the slug in
`…/cristiano-ronaldo-becomes-first-man-to-score-in-5-world-cups` as a `person`. **One
Wikipedia article put 158 such nodes in the graph.** Worse: each carries the real surname as
a token, which makes `RONALDO` genuinely ambiguous and blocks alias resolution from ever
firing. So this is upstream of the duplicate problem, not a separate cleanup.

Two implementation details matter:

- **Masked with spaces, not deleted.** Every entity offset is an index into this string, so
  the replacement must be the same length as the match.
- **The TLD list is spelled out.** A chunk boundary can cut a URL in half, so the tail
  arrives as a bare `au/football/…/cristiano-ronaldo-conundrum`. Matching those needs the
  TLD explicitly — a generic `\w+/\S+` would eat `src/app/page.tsx` and every other real
  path in a code or docs ingest.

### Windowing, because the encoder truncates

GLiNER truncates past ~384 tokens, so a 1,024-token chunk has to be split. Sentences are
grouped into windows of ~900 characters (~150 words), comfortably under the ceiling. Bigger
windows are faster (fewer forward passes) but hit the limit. A single over-long sentence
becomes its own window and is truncated by the model rather than dropped.

Sentences keep their trailing punctuation and newline so offsets stay contiguous with the
source — which is what lets `bisect` map an entity's position back to the sentence it was
found in.

### Relationships are co-occurrence, typed honestly

Two entities in the same sentence get one `RELATED_TO` edge, with that sentence as the
description. The comment explains what this replaced: the words *between* the two mentions
used to be the edge's "type", and those words were unbounded free text — so a pair seen in
ten sentences produced ten edges with ten different types that LightRAG merged into one
comma-salad keyword. The sentence is already the edge's description, so nothing is lost by
typing the edge properly.

Per-chunk caps mirror what the LLM prompt asked for. Without them a dense sentence with 8
entities alone contributes 28 edges:

| Cap | Value |
|---|---|
| `_MAX_ENTITIES_PER_CHUNK` | 40 (kept by score) |
| `_MAX_RELATIONS_PER_CHUNK` | 60 |
| `_MAX_ENTITIES_PER_SENTENCE` | 5 |
| `_MAX_DESCRIPTION_CHARS` | 400 |

### The description is the sentence, because it has to be something

GLiNER returns no description, and **LightRAG drops any entity without one**. The sentence
the entity was found in is both non-empty and genuinely grounded context for retrieval — so
it becomes the description, and it's also what the provenance panel shows.

---

## 6. Function & Component Reference

---

### `gliner_extract(prompt, system_prompt=None, history_messages=None, keyword_extraction=False, **kwargs)`

**What it does:** The LightRAG `extract` role function. Its signature matches the LLM
functions it replaces exactly.

**Input:** `prompt: str` — LightRAG's own extraction prompt, with the chunk embedded.

**Output:** `str` — JSON.

**Example:**
```python
await gliner_extract(
    "…---Input Text---\n```\nAcme Corp hired Jane Doe in Berlin.\n```\n\n---Output---…")
# => '{"entities": [
#       {"name": "ACME CORP", "type": "organization", "description": "Acme Corp hired Jane Doe in Berlin."},
#       {"name": "JANE DOE", "type": "person", "description": "Acme Corp hired Jane Doe in Berlin."},
#       {"name": "BERLIN", "type": "location", "description": "Acme Corp hired Jane Doe in Berlin."}],
#     "relationships": [
#       {"source": "ACME CORP", "target": "JANE DOE", "keywords": "RELATED_TO", "description": "Acme Corp hired Jane Doe in Berlin."},
#       {"source": "ACME CORP", "target": "BERLIN", "keywords": "RELATED_TO", "description": "…"},
#       {"source": "BERLIN", "target": "JANE DOE", "keywords": "RELATED_TO", "description": "…"}]}'
```

**Notes:** `_INPUT_TEXT_RE` is greedy and anchored on `---Output---`, so a chunk that itself
contains ``` fences still matches to the real closing fence without running past the prompt.
Runs `extract_records` in a thread.

---

### `extract_records(text, predict)`

**What it does:** The whole extraction logic for one chunk. Pure — `predict` is injected, so
this is testable without loading a model.

**Input:** `text: str`, `predict: Callable[[list[str]], list[list[dict]]]` returning
per-window entity dicts with `start`, `end`, `text`, `label`, `score`.

**Output:** `{"entities": [...], "relationships": [...]}`.

**Example:**
```python
fake = lambda windows: [[{"start": 0, "end": 9, "text": "Acme Corp",
                          "label": "organization", "score": 0.9}]]
extract_records("Acme Corp hired Jane Doe.", fake)
# => {"entities": [{"name": "ACME CORP", "type": "organization",
#                   "description": "Acme Corp hired Jane Doe."}],
#     "relationships": []}
```

**The pipeline:** mask URLs → split sentences → group into windows → one batched `predict`
→ per hit, `canonical_name` + `canonical_label` + locate its sentence via `bisect` → keep
top 40 by score → same-sentence pairs become `RELATED_TO` edges, one per unordered pair per
chunk, first sentence as evidence.

**Notes:** The canonical name **is** the dict key, so folding happens at insertion — two
spellings in one chunk are already one record before anything downstream sees them. The
`_score` field is used for the top-40 cut and popped before returning.

---

### `canonical_name(raw)` / `canonical_label(raw)`

Fully documented in [BACKEND.md](../BACKEND.md#canonical_nameraw). Both have runnable
self-checks:

```bash
cd backend && python -m app.services.graph_schema   # prints "ok"
```

Key assertions:
```python
canonical_name("Microsoft") == canonical_name("microsoft") == "MICROSOFT"
canonical_name("LIONEL_MESSI") == canonical_name("Lionel-Messi") == "LIONEL MESSI"
canonical_name("Theodore") == "THEODORE"          # only a WHOLE article is dropped
canonical_name("Ronaldo Jr") != canonical_name("Ronaldo")   # distinct people stay distinct
canonical_name("Lionel–Messi") == "LIONEL MESSI"  # en-dash is a separator too
canonical_label("data source") == "datasource"    # what LightRAG itself writes
```

---

### `summarize_descriptions(jsonl)`

**What it does:** Merges an entity's accumulated descriptions without an LLM.

**Input:** `jsonl: str` — one JSON object per line, each with a `Description` field.

**Output:** `str`, capped at 1,200 chars on a word boundary.

**Example:**
```python
summarize_descriptions(
    '{"Description": "Acme Corp hired Jane Doe."}\n'
    '{"Description": "Acme Corp hired Jane Doe."}\n'
    '{"Description": "Acme Corp is based in Berlin."}')
# => "Acme Corp hired Jane Doe. Acme Corp is based in Berlin."
```

**Notes:** Carries a `ponytail:` marker with its own justification: these descriptions are
**verbatim source sentences**, not model prose, so de-duplicating and joining them keeps
every fact an LLM summary would have preserved — at zero tokens. A line that isn't valid
JSON is used as-is rather than dropped.

---

### `_mask_urls(text)` / `_split_sentences(text)` / `_windows(text, spans)`

| Function | Returns |
|---|---|
| `_mask_urls(text)` | Same string, URLs replaced by equal-length runs of spaces |
| `_split_sentences(text)` | `[(start, end)]` char spans, one per sentence, punctuation retained |
| `_windows(text, spans)` | `[(offset, window_text)]` under ~900 chars |

```python
_mask_urls("See https://example.com/x for details.")
# => "See                        for details."   (same length)
```

---

### `_get_model()` / `_predict(windows)` / `warmup()`

`_get_model` loads GLiNER lazily behind a `threading.Lock` (chunks extract from a thread
pool and would otherwise race the first load), calls `.eval()`, and moves to CUDA when
`GLINER_USE_GPU`. `_predict` runs `model.inference(windows, ENTITY_LABELS,
threshold=GLINER_THRESHOLD, batch_size=GLINER_BATCH_SIZE)`. `warmup()` is called from the
app lifespan so the first ingest doesn't pay the load.

---

## 7. End-to-End Walkthroughs

### 7.1 One chunk of a news article

Chunk: *"Cristiano Ronaldo joined Al Nassr in 2023. See
https://espn.com/story/4805137/cristiano-ronaldo-becomes-first-man for more."*

1. LightRAG calls `gliner_extract` with this text inside its prompt.
2. `_INPUT_TEXT_RE` matches → `extract_records(text, _predict)`.
3. `_mask_urls`: the ESPN URL becomes 58 spaces. **Every offset stays valid.** Without this,
   GLiNER would score `cristiano-ronaldo-becomes-first-man` as a `person`.
4. `_split_sentences` → two spans. `_windows` → one window (under 900 chars).
5. `_predict` → `CRISTIANO RONALDO` (person, 0.94), `AL NASSR` (organization, 0.88),
   `2023` (date, 0.71).
6. Each name through `canonical_name`, each label through `canonical_label`. Each entity's
   sentence located by `bisect` and cleaned (the masked run collapses to a single space).
7. All three in sentence 0 → three `RELATED_TO` edges, one per unordered pair.
8. Returned as JSON. LightRAG parses it, merges `CRISTIANO RONALDO` with the same name from
   forty other chunks, upserts the nodes and edges, and writes their vectors.

---

### 7.2 A popular entity's descriptions get merged

1. `CRISTIANO RONALDO` accumulates enough description fragments to trip
   `FORCE_LLM_SUMMARY_ON_MERGE`.
2. LightRAG builds a summarization prompt — `Description List:\n\n```\n{jsonl}\n```` — and
   sends it through the **`extract` role**, which is this agent.
3. `_INPUT_TEXT_RE` does **not** match. `_DESCRIPTION_LIST_RE` does.
4. `summarize_descriptions` parses each line, de-dupes, joins, truncates at 1,200 chars on a
   word boundary.
5. The node's description becomes real merged prose.

**If this branch didn't exist**, `_INPUT_TEXT_RE` would fail, the gleaning fallback would
return `EMPTY_RESULT`, and the node's description would literally be
`{"entities": [], "relationships": []}`.

---

### 7.3 A spreadsheet summary is skipped

1. `ingest_file_bytes` sees `.xlsx` → `load_spreadsheet` writes DuckDB tables →
   `build_summary` produces text starting `Spreadsheet workbook: q3.xlsx`.
2. That text goes through `ingest_text` → `rag.ainsert` → chunked → the `extract` role.
3. `_INPUT_TEXT_RE` matches, then `text.lstrip().startswith(SUMMARY_HEADER)` → return
   `EMPTY_RESULT`. **No entities.**
4. Meanwhile `tabular_graph.project` writes the Workbook, Worksheet and Column nodes
   deterministically, from what the parser actually read.

Both paths would have described the same workbook. Only one of them is guaranteed correct.

---

## 8. Configuration & Setup

| Variable | Default | Effect |
|---|---|---|
| `EXTRACTION_BACKEND` | `gliner` | `llm` restores the Groq/Ollama JSON extraction path |
| `GLINER_MODEL` | `urchade/gliner_small-v2.1` | ~250 MB |
| `GLINER_THRESHOLD` | `0.4` | Lower = more entities, more noise |
| `GLINER_BATCH_SIZE` | `8` | Windows per forward pass |
| `GLINER_USE_GPU` | `false` | |
| `GLINER_MAX_ASYNC` | `2` | Concurrent chunk extractions. torch already saturates every core inside one batch, so this is about overlapping one chunk's tokenization with another's matmul, not about cores. |
| `ENTITY_LABELS` | `person,organization,location,product,technology,event,concept,date` | **The ontology.** Editing it is how you retarget extraction. |
| `MAX_GLEANING` | `0` | Off; a single-pass encoder gains nothing from it. |

### Changing the ontology

`ENTITY_LABELS` is scored against on every chunk, so it's the single lever for what the
graph contains. Keep it to 8–10 deliberate types — that's what keeps entity types consistent
across a document instead of a model inventing "artifact" in one chunk and "tool" in the
next. Adding a label means re-ingesting; existing nodes keep their old types.

**Also add an icon** for a new label in
[`frontend/src/constants/symbols.ts`](../FRONTEND.md#symbolforentitytype-sourcetype), keyed
by its `canonical_label()` form. Without one it renders as a plain dot.

### Tests and benchmarks

```bash
cd backend
pytest app/services/test_gliner_extract.py -v
python -m app.services.graph_schema                  # canonicalization self-check
python scripts/benchmark_extraction.py --words 90000 # book-sized ingest, throwaway storage
python -m scripts.graph_duplicates                   # entities split across nodes (read-only)
```

`graph_duplicates` separates `RESOLVED` splits (folds to one name today — a re-ingest merges
them) from `AMBIGUOUS` ones (a short name that's a prefix of longer ones). It has no
`--apply` on purpose: the graph is derived data, so the fix is a re-ingest, not surgery.

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **Every prose edge is `RELATED_TO`** (`ponytail:`) | No relation model. Relationships are same-sentence co-occurrence; the sentence is the evidence but the edge carries no verb. → GLiREL, or a Tier-2 LLM pass. |
| **Descriptions are joined, not summarised** (`ponytail:`) | Verbatim source sentences de-duped and concatenated. → Route `summarize_descriptions` back to `llm_model_func`. |
| **Co-occurrence is sentence-scoped only** | Two entities in adjacent sentences of the same paragraph get no edge. A pronoun reference gets nothing at all. |
| **No coreference resolution** | "Microsoft… the company… it" produces one node and two misses. |
| **The fold can over-merge** | `Ronaldo Jr` vs `Ronaldo` stays distinct, but two genuinely different people with the same name become one node, permanently and invisibly. |
| **The fold is order-independent but not retroactive** | Improving `canonical_name` doesn't merge already-split nodes; only a re-ingest does. |
| **English-centric** | Sentence splitting is `[.!?\n]`; the article/possessive folds are English; the TLD list is Latin-script. |
| **Ontology is global** | One `ENTITY_LABELS` for every document. A codebase and a legal contract are scored against the same eight types. |
| **Windowing can split an entity** | A name spanning a ~900-char window boundary is seen by neither window. |
| **`_URL_RE` is a heuristic** | The TLD list is finite; a newer TLD, or a bare path with no TLD, isn't masked. |
| **No confidence on the output** | GLiNER's score gates the top-40 cut, then is discarded — the graph node carries no confidence. |
| **Model loading is not shared across processes** | Each uvicorn worker loads its own ~250 MB copy. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [GLiNER](../GLOSSARY.md#gliner),
  [Ontology](../GLOSSARY.md#ontology), [Entity](../GLOSSARY.md#entity),
  [`canonical_name()`](../GLOSSARY.md#canonical_name),
  [`canonical_label()`](../GLOSSARY.md#canonical_label),
  [Gleaning](../GLOSSARY.md#gleaning), [Label](../GLOSSARY.md#label),
  [Relationship type](../GLOSSARY.md#relationship-type)
- [`BACKEND.md`](../BACKEND.md) — [the graph layer](../BACKEND.md#the-graph-layer),
  [walkthrough 7.1](../BACKEND.md#71-a-user-uploads-handbookpdf),
  [`graph_duplicates`](../BACKEND.md#operational-scripts)
- [`llm-router`](llm-router.md) — registers this as the `extract` role
- [`multi-hop-planner`](multi-hop-planner.md) — consumes the entity names as seed keywords
- [`code-intelligence-agent`](code-intelligence-agent.md) — the *deterministic* counterpart,
  writing through the same `graph_schema` chokepoint
- [`FRONTEND.md`](../FRONTEND.md#symbolforentitytype-sourcetype) — where a new label needs
  an icon
- [`agents/README.md`](README.md)
