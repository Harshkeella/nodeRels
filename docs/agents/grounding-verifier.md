# Agent: Grounding Verifier

> `backend/app/services/grounding.py` + `provenance.py` — what the answer was built from, and
> which of its sentences that evidence actually supports.

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

Two small modules that together answer "can I trust this answer?"

**`provenance.py`** builds the evidence chain. After a query, LightRAG returns the entities,
relationships and chunks that survived truncation into the prompt — *not* the candidate set
that was considered. That distinction is the whole point: showing the candidates would be
showing work the answer never saw. Each item carries the `reference_id` of the source it came
from, so they group into one chain per source, ordered `Source → Chunk → Entity →
Relationship → Answer`. That's what the frontend's provenance panel renders, and it's
persisted with the message so reopening an old conversation still opens working panels.

**`grounding.py`** checks the finished text against that evidence. It splits the answer into
sentences and, for each one, measures what fraction of its content words appear anywhere in
the evidence. Below half, the sentence is flagged.

The design constraint that shapes both: **the answer has already streamed.** An unsupported
claim cannot be *removed* before the user sees it without buffering the whole response and
giving up progressive rendering. So this flags rather than strips. The check runs on the
completed text and its verdict rides out on its own SSE frame, which the UI attaches to the
message as an amber warning listing the sentences it couldn't back.

The test is lexical, not an LLM judge — deliberately. It catches the failure that actually
matters here: a fluent sentence full of names, numbers and terms that appear nowhere in what
was retrieved. Without a second model call per answer.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **`re`** (stdlib) | Sentence splitting on `(?<=[.!?])\s+`, word tokenizing on `[a-z0-9]+`. |
| **Nothing else.** | No NLI model, no embedding comparison, no second LLM call. Both modules are pure functions over dicts and strings, with no I/O and no state. |

That's the entire dependency list. `grounding.py` is 86 lines, `provenance.py` is 128.

---

## 3. Folder & File Structure

```
backend/app/services/
├── provenance.py              # 128 lines
│   ├── SNIPPET_CHARS = 240
│   ├── MAX_PER_KIND = 6
│   ├── _snippet(text) / _step(kind, node_id, label, snippet, **extra)
│   └── build_evidence(data)            # THE BUILDER
│
├── grounding.py               # 86 lines
│   ├── SUPPORT_THRESHOLD = 0.5
│   ├── MIN_CONTENT_WORDS = 4
│   ├── _SENTENCE_SPLIT / _WORD / _STOPWORDS
│   ├── _content_words(text) / _evidence_text(evidence)
│   └── check(answer, evidence)         # THE CHECK
│
├── test_provenance.py         # 87 lines
└── test_grounding.py          # 46 lines
```

Both are pure and synchronous, which is why their tests need no fixtures at all.

---

## 4. How This Fits Into the Bigger Picture

```
   rag.aquery_llm(...) → result["data"]  { references, chunks, entities, relationships }
        │                                    ↑ post-truncation: what reached the prompt
        ▼
   provenance.build_evidence(data)
        │
        ├──► SSE frame "evidence"  ──► frontend ChatMessageBubble → ProvenancePanel
        │                              extension: IGNORED
        ├──► chat_store.add_message(evidence=...)   persisted as JSON
        └──► grounding.check(answer, evidence)
                 │
                 └──► SSE frame "grounding"  (only when unsupported is non-empty)
                          └──► frontend: the amber warning
                               extension: IGNORED
```

**One producer** — [`query-orchestrator`](query-orchestrator.md), steps 9 and 11.

**Three consumers:**

| Consumer | What it does |
|---|---|
| [`ProvenancePanel`](../FRONTEND.md#provenancepanel-evidence-onclose) | Renders the chain as a vertical timeline, deep-linking entities into the graph |
| [`chat_store`](../BACKEND.md#chat-store) | Persists the chain verbatim, so an old session's panels still work |
| [`eval_retrieval.py`](../BACKEND.md#operational-scripts) | Scores **retrieval** against the evidence chain, not against prose — so the number doesn't move when the answering model changes its wording |

That last one is the underrated use. The evidence chain is the system's only machine-readable
record of what retrieval actually delivered.

---

## 5. Core Concepts & Key Components

### Post-truncation, not the candidate set

`aquery_llm` returns `data.entities` / `data.relationships` / `data.chunks` **after**
truncation — the items that survived into the prompt. The docstring is explicit that this is
the whole point of a provenance panel: showing the candidates would be showing work the
answer never saw.

The chain is therefore a claim about what the model *had*, not about what was retrieved and
discarded.

### Unattributable items are dropped, and so are empty sources

`build_evidence` seeds its buckets from `references` only. An item whose `reference_id` isn't
in that list has no source the UI is showing, so it has no panel to live in and is silently
dropped. Symmetrically, a source that contributed nothing gets no panel — *"rather than
rendered as an empty panel behind a button that looks clickable."*

That's also why the frontend only renders a source chip's provenance button when a matching
chain exists: the button is never a promise the panel can't keep.

### Caps make it glanceable

`MAX_PER_KIND = 6` per source per kind. A panel is a glanceable proof, not a transcript, and
an 80-entity source would render as a wall. `SNIPPET_CHARS = 240`, ellipsised.

### Ordering is by kind, and that's a stated ceiling

`{"chunk": 0, "entity": 1, "relationship": 2}`, with the source prepended as step one.

The comment names the limit: ordering is by kind, **not by a real derivation trace** —
LightRAG does not record which entity produced which claim. Good enough to show what the
answer rests on; it is not a proof tree.

### Flagging, not stripping

The module docstring states the constraint: the answer streams token by token, so an
unsupported claim cannot be removed before the user sees it without buffering the whole
response and giving up progressive rendering.

So the check runs on completed text, the verdict rides its own SSE frame, and the UI attaches
it to the message. The frontend comment agrees: *"Flagged, not hidden: the answer already
streamed, so the honest move is to say which parts the evidence didn't cover."*

### Three rules that stop it crying wolf

| Rule | Value | Why |
|---|---|---|
| Short sentences aren't claims | `MIN_CONTENT_WORDS = 4` | "Here's why:" is a transition, not a claim to check |
| Stopwords are stripped | 60-word frozenset | A sentence of function words has nothing to verify |
| **No evidence → nothing claimed** | `haystack` empty → `{"checked": 0, …, 1.0}` | A refusal — *"the knowledge base has no information on this"* — must not get flagged as an unsupported claim |

That last one is load-bearing. The system prompt in
[`build_query_param`](query-orchestrator.md#build_query_paramhistory) *instructs* the model to
refuse when the context is empty. Without this guard, following that instruction would be
reported as a hallucination.

### The threshold, and what it's tuned for

Half of a sentence's content words must appear somewhere in the evidence text. The haystack
is a `set` built from every source's `file_path`, and every step's `label` and `snippet` —
flat, unordered, one bag of words. So this measures *vocabulary overlap*, not entailment.

It catches the failure that matters: a fluent sentence full of names, numbers and terms that
appear nowhere in what was retrieved. It does not catch a wrong claim made entirely in words
that *are* in the evidence.

---

## 6. Function & Component Reference

---

### `build_evidence(data)`

`app/services/provenance.py`

**What it does:** Groups what actually went into the answer into one chain per source.

**Input:** `data: dict` — the `data` key of `aquery_llm`'s result, with `references`,
`chunks`, `entities`, `relationships`.

**Output:** `list[dict]` — `[{reference_id, file_path, chain: [step, …]}, …]`.

**Example** (this exact input and output are asserted in `test_provenance.py`):
```python
data = {
  "references": [
    {"reference_id": "1", "file_path": "handbook.pdf"},
    {"reference_id": "2", "file_path": "notes.md"},
    {"reference_id": "3", "file_path": "orphan.txt"},
  ],
  "chunks": [{"chunk_id": "c1", "reference_id": "1", "file_path": "handbook.pdf",
              "content": "Leave   policy\nis  20 days."}],
  "entities": [
    {"entity_name": "ACME", "entity_type": "organization",
     "description": "A company.", "reference_id": "1"},
    {"entity_name": "LEAVE POLICY", "entity_type": "concept",
     "description": "Time off rules.", "reference_id": "2"},
    {"entity_name": "GHOST", "reference_id": "99", "description": "x"},   # unattributable
  ],
  "relationships": [{"src_id": "ACME", "tgt_id": "LEAVE POLICY",
                     "keywords": "RELATED_TO", "description": "ACME defines it.",
                     "reference_id": "1"}],
}

build_evidence(data)
# => [
#   {"reference_id": "1", "file_path": "handbook.pdf", "chain": [
#      {"type": "source",       "id": "handbook.pdf", "label": "handbook.pdf", "snippet": ""},
#      {"type": "chunk",        "id": "c1", "label": "handbook.pdf",
#       "snippet": "Leave policy is 20 days."},          # whitespace collapsed
#      {"type": "entity",       "id": "ACME", "label": "ACME", "snippet": "A company.",
#       "entity_type": "organization"},
#      {"type": "relationship", "id": "ACME->LEAVE POLICY",
#       "label": "ACME → LEAVE POLICY", "snippet": "ACME defines it.",
#       "keywords": "RELATED_TO", "src_id": "ACME", "tgt_id": "LEAVE POLICY"}]},
#   {"reference_id": "2", "file_path": "notes.md", "chain": [
#      {"type": "source", "id": "notes.md", "label": "notes.md", "snippet": ""},
#      {"type": "entity", "id": "LEAVE POLICY", "label": "LEAVE POLICY",
#       "snippet": "Time off rules.", "entity_type": "concept"}]},
# ]
# orphan.txt contributed nothing -> no panel.  GHOST's reference_id 99 -> dropped.
```

**Notes:** A relationship step carries **both endpoints** as `src_id` and `tgt_id`, so the
panel can deep-link each one into the graph explorer rather than only the pair as a label.
Snippets are whitespace-collapsed so the panel doesn't render raw newlines. Defensive
throughout — `data or {}`, `.get(…) or []` — so a `None` or a missing key yields `[]` rather
than raising inside a live stream.

---

### `check(answer, evidence)`

`app/services/grounding.py`

**What it does:** Reports which sentences of a finished answer the evidence doesn't support.

**Input:** `answer: str`, `evidence: list[dict]` — the `build_evidence` output.

**Output:** `{"checked": int, "unsupported": list[str], "supported_ratio": float}`.

**Example** (all four cases are `test_grounding.py`):
```python
evidence = [{"file_path": "handbook.pdf", "chain": [
    {"type": "source", "label": "handbook.pdf", "snippet": ""},
    {"type": "chunk",  "label": "handbook.pdf",
     "snippet": "Employees accrue 20 days of annual leave per year."},
    {"type": "entity", "label": "ACME", "snippet": "ACME is the employer."}]}]

check("Employees accrue 20 days of annual leave per year.", evidence)
# => {"checked": 1, "unsupported": [], "supported_ratio": 1.0}

check("Employees accrue 20 days of annual leave per year. "
      "The chief executive resigned in Lisbon following a currency scandal.", evidence)
# => {"checked": 2,
#     "unsupported": ["The chief executive resigned in Lisbon following a currency scandal."],
#     "supported_ratio": 0.5}

check("The knowledge base has no information on this.", [])
# => {"checked": 0, "unsupported": [], "supported_ratio": 1.0}   # a refusal is not a claim

check("Here is why:", evidence)
# => {"checked": 0, "unsupported": []}   # 2 content words, below MIN_CONTENT_WORDS
```

**How:**
```
haystack = set of content words across every source's file_path, labels and snippets
if not haystack:  return {"checked": 0, "unsupported": [], "supported_ratio": 1.0}
for each sentence split on (?<=[.!?])\s+ :
    words = content words          # [a-z0-9]+ minus stopwords
    if len(words) < 4: skip        # a transition, not a claim
    checked += 1
    if sum(w in haystack for w in words) / len(words) < 0.5: unsupported.append(sentence)
```

**Notes:** Carries a `ponytail:` marker — lexical overlap, so a correct paraphrase sharing few
words with its evidence can be flagged. Swap in an entailment model or an LLM judge here if
the false-positive rate ever bites. Note that `checked` counts only sentences that met the
length floor, so `supported_ratio` is over checkable claims, not over all sentences.

---

### `_evidence_text(evidence)` / `_content_words(text)` / `_snippet(text)` / `_step(...)`

| Helper | Behaviour |
|---|---|
| `_evidence_text(evidence)` | Flattens every `file_path`, `label` and `snippet` into one string. Including `file_path` means a sentence naming its source document is credited for it. |
| `_content_words(text)` | `[a-z0-9]+` on the lowercased text, minus the 60-word stopword set. |
| `_snippet(text)` | Whitespace-collapse, truncate to 240 chars with `…`. |
| `_step(kind, node_id, label, snippet, **extra)` | Builds one chain step; `extra` keys with a `None` value are omitted, so a step never carries an empty field. |

---

### Constants

| Constant | Value | Rationale |
|---|---|---|
| `SUPPORT_THRESHOLD` | `0.5` | Below this fraction of content words present, the sentence is unsupported |
| `MIN_CONTENT_WORDS` | `4` | Shorter is a transition or lead-in, carrying no claim |
| `SNIPPET_CHARS` | `240` | Enough to read, short enough to scan |
| `MAX_PER_KIND` | `6` | Per source per kind — a proof, not a transcript |
| `_STOPWORDS` | 60 words | Function words carry no evidential weight |

---

## 7. End-to-End Walkthroughs

### 7.1 A well-grounded answer

1. `aquery_llm` returns `data` with two references, four chunks, six entities, three
   relationships.
2. `build_evidence`: buckets seeded from the two references. Every item's `reference_id`
   matches one of them. Rebuilt in panel order, capped at 6 per kind, each source prefixed
   with its own step.
3. `evidence` frame → the frontend stores it on the message. Each source chip gains a route
   button, because a matching chain exists.
4. Tokens stream. The answer is *"Employees accrue 20 days of annual leave per year. The
   policy is set out in section 4 of the handbook."*
5. `check`: haystack is every content word from both sources' paths, labels and snippets.
   Sentence 1: 7 content words, 7 present → 1.0. Sentence 2: 6 content words, 5 present →
   0.83. Both above 0.5.
6. `unsupported` is empty → **no `grounding` frame is sent.** A clean answer carries no frame
   rather than a "0 problems" one the UI would have to know to hide.
7. `_persist` writes the evidence as JSON on the assistant message.
8. The user clicks a route button → `ProvenancePanel` renders
   `Source document → Matched text → Entity → Relationship → Answer` down a continuous spine,
   with each entity deep-linking to `/dashboard/graph?focus=…`.

---

### 7.2 A hallucinated sentence, flagged

Same setup, but the model adds *"The chief executive resigned in Lisbon following a currency
scandal."*

1. `check` splits into two sentences.
2. Sentence 1 scores 1.0.
3. Sentence 2's content words: `chief, executive, resigned, lisbon, following, currency,
   scandal`. **None** are in the haystack — nothing retrieved mentions any of them. Score
   0.0 < 0.5 → flagged.
4. `{"checked": 2, "unsupported": ["The chief executive resigned…"], "supported_ratio": 0.5}`.
5. `_chat_stream` logs `Grounding: 1/2 sentences unsupported` and sends the `grounding` frame.
6. The frontend renders an amber panel: *"1 of 2 statements not backed by the retrieved
   sources"*, listing the sentence.

The answer is **not** modified. It already streamed; the honest move is to say which part
isn't backed.

---

### 7.3 A refusal

1. The question has no answer in the knowledge base. Retrieval returns nothing usable; the
   system prompt instructs the model to reply with one short sentence saying so.
2. `build_evidence(data)`: `references` is empty → `by_ref` is empty → every item is
   unattributable → returns `[]`.
3. `sources` and `evidence` frames go out carrying empty lists. The frontend renders no source
   list.
4. Answer: *"The knowledge base has no information on this."*
5. `check(answer, [])`: `_evidence_text([])` → `""` → `haystack` is empty → the guard returns
   `{"checked": 0, "unsupported": [], "supported_ratio": 1.0}`.
6. `unsupported` is empty → **no `grounding` frame.**

Without step 5's guard, the model doing exactly what it was told would be reported as a
hallucination — a false alarm on the one answer that's most obviously honest.

---

## 8. Configuration & Setup

**No environment variables.** Every tunable is a module constant, listed in
[§6](#constants).

### Tests

```bash
cd backend
pytest app/services/test_grounding.py app/services/test_provenance.py -v
```

Ten cases, no fixtures, no mocks — both modules are pure functions over dicts.

### Tuning the threshold

`SUPPORT_THRESHOLD = 0.5` is the only number likely to need adjustment. Raising it flags more
paraphrases; lowering it misses more fabrications. The retrieval eval is the place to measure
the effect:

```bash
cd backend
python -m scripts.eval_retrieval --save baseline.json
# change SUPPORT_THRESHOLD
python -m scripts.eval_retrieval --compare baseline.json
```

`eval_questions.yaml` declares, per question, substrings that must appear in the evidence
chain — so the retrieval score is stable across answering-model changes, and grounding is
reported alongside it as a separate dimension.

### Watching it

```
INFO app.api.chat: Grounding: 1/4 sentences unsupported
```

Absence of this line means everything checked was supported (or nothing was checkable).

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **Lexical, not entailment** (`ponytail:`) | A correct paraphrase sharing few words with its evidence is flagged. → an NLI model or an LLM judge, same signature. |
| **A wrong claim in the right vocabulary passes** | The reverse failure. "Employees accrue **200** days of annual leave" scores 1.0 — `200` tokenizes as a content word not in the haystack, but one miss out of seven is still above threshold. Numbers are not treated specially. |
| **The haystack is a flat bag of words** | Cross-source contamination: a sentence combining a term from source A with a term from source B is credited, even though no single source supports it. |
| **No stemming and no lemmatisation** | "accrues" vs "accrue" are different tokens, exactly as in [`sparse.py`](../BACKEND.md#sparseencode_documenttext--sparseencode_querytext). |
| **Sentence splitting is naive** | `(?<=[.!?])\s+` splits on "e.g.", "Dr.", "3.5" and decimal-heavy answers. |
| **Chain ordering is by kind, not derivation** | Stated ceiling. LightRAG doesn't record which entity produced which claim, so it isn't a proof tree. |
| **`file_path` is in the haystack** | A sentence that merely names its source document gets free credit toward its threshold. |
| **Markdown is not stripped** | `**bold**` and `` `code` `` fences are tokenized as-is, so formatting characters dilute the content-word count slightly. |
| **Spreadsheet answers are never checked** | They persist with empty evidence and skip the check entirely — correct (DuckDB rows are exact), but it means a *wrong* SQL query producing exact-but-irrelevant rows has no safety net. |
| **The extension renders neither frame** | A popup answer can present an unsupported claim the dashboard would have flagged. See [EXTENSION.md §9](../EXTENSION.md#9-known-limitations--open-todos). |
| **`MAX_PER_KIND = 6` silently truncates** | The panel gives no indication that a source contributed more than six chunks. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [Grounding](../GLOSSARY.md#grounding),
  [Evidence chain](../GLOSSARY.md#evidence-chain), [Session](../GLOSSARY.md#session),
  [SSE](../GLOSSARY.md#sse-server-sent-events)
- [`query-orchestrator`](query-orchestrator.md) — the caller; the
  [`user_prompt`](query-orchestrator.md#build_query_paramhistory) that produces the refusal
  this agent must not flag
- [`BACKEND.md`](../BACKEND.md) —
  [the SSE frame table](../BACKEND.md#post-apiv1chatstream),
  [`chat_store`](../BACKEND.md#chat-store) (persistence),
  [`eval_retrieval`](../BACKEND.md#operational-scripts)
- [`FRONTEND.md`](../FRONTEND.md#provenancepanel-evidence-onclose) — the panel, and
  [why it's a chain and not a graph](../FRONTEND.md#provenancepanel-evidence-onclose)
- [`spreadsheet-sql-agent`](spreadsheet-sql-agent.md) — the path that bypasses both modules
- [`agents/README.md`](README.md)
