# Agent: Multi-Hop Planner

> `backend/app/services/multihop.py` — splits a question that spans two facts into
> sub-questions, retrieves each, and feeds what they found into the real search.

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

Some questions can't be answered by one search. *"Which columns feed the metric that the Q3
report calls out?"* needs the metric identified first, and only then can you look for its
columns. Embedding that whole sentence and searching once lands somewhere between the two
ideas — it tends to surface the report and miss the columns entirely.

This module fixes that, and the shape of the fix is the interesting part. It does **not**
add a second generation path, chain answers together, or introduce an agent loop. The
answer still comes from exactly one `aquery_llm` call, so nothing about the chat contract
changes. What changes is **what that call searches for**.

The sequence: a regex gate decides whether the question even looks multi-hop. If it does,
one LLM call splits it into at most three sub-questions. Each sub-question runs a
*retrieval-only* query — no generation, no answer, no tokens spent on prose. The entity
names those retrievals actually surfaced are counted, ranked by how many hops found them,
and handed to the final query as pre-supplied keywords. LightRAG skips its own keyword
extraction when keywords are supplied, so the last search looks for hop 1's discoveries **by
name**.

The cost discipline is deliberate: an ordinary one-hop question never reaches the LLM at
all. The gate is a regex match, and a `False` returns an empty list before anything
expensive happens.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **`re`** (stdlib) | The heuristic gate is eleven precompiled patterns. No classifier, no model, no training data — a false positive costs one extra retrieval and a false negative costs the answer, so an over-inclusive regex is the right trade. |
| **`lightrag_engine.llm_model_func`** | The decomposition call. Uses the *extraction* role, not the answering one, so decomposition doesn't compete with chat for the answering model's rate-limit bucket. Imported inside the function to avoid a circular import. |
| **`rag.aquery_data`** | LightRAG's retrieval-only entry point. Returns the retrieved entities, relationships and chunks **without generating anything** — which is what makes a hop cheap. |
| **`ll_keywords`** | LightRAG's low-level keyword field. Setting it makes LightRAG skip its own keyword-extraction call, so seeding is not just a hint — it replaces a step. |

No graph library, no planner framework, no state machine. 155 lines.

---

## 3. Folder & File Structure

```
backend/app/services/
├── multihop.py                # 155 lines
│   ├── MAX_SUBQUESTIONS = 3
│   ├── MAX_SEED_KEYWORDS = 12
│   ├── _MULTI_HOP_PATTERNS    # 11 regexes -> _COMPILED
│   │
│   ├── is_multi_hop(question)          # the gate — pure, no I/O
│   ├── parse_subquestions(raw, original)  # model reply -> list — pure, no I/O
│   ├── decompose(question)             # gate + one LLM call
│   ├── seed_keywords(datas)            # retrieval results -> ranked names — pure
│   └── gather(rag, question, param_factory)   # THE ENTRY POINT
│
└── test_multihop.py           # 136 lines — parametrised gate tests + parser tests
```

Three of the five functions are pure and synchronous, which is why the test file needs no
mocks for the parts that matter.

---

## 4. How This Fits Into the Bigger Picture

One caller, one call site:

```python
# backend/app/api/chat.py::_chat_stream
seeds = await multihop.gather(rag, message, build_retrieval_param)
if seeds:
    param.ll_keywords = seeds
```

That's the entire integration surface. It sits between building the `QueryParam` and issuing
`aquery_llm`, and a single-hop question makes it a no-op.

```
   query-orchestrator
        │
        │  gather(rag, question, param_factory)
        ▼
   is_multi_hop(question)  ──── False ────► return []   (no LLM call, no retrieval)
        │ True
        ▼
   decompose(question)
        │  llm_model_func ──────────────────────► llm-router (extract role)
        │  parse_subquestions(raw, original)
        │
        │  [] (SINGLE, or only one sub-question) ► return []
        ▼
   for each sub-question:
        rag.aquery_data(sub, param=param_factory())   ← a FRESH QueryParam each time
        │
        ▼
   seed_keywords(datas)  ──► ["ACME CORP", "LEAVE POLICY", …]
        │
        ▼
   param.ll_keywords = seeds   ──► the one aquery_llm call searches for these by name
```

**Why `param_factory` is a factory** and not a `QueryParam`: LightRAG writes the resolved
keywords *back onto* the object it was given. Reusing one instance would leak hop 1's
keywords into hop 2, and the second retrieval would search for the first one's terms. The
orchestrator passes [`build_retrieval_param`](query-orchestrator.md#build_retrieval_param)
itself, uncalled.

**Why `ll_keywords` is the seeding channel**: LightRAG normally spends an LLM call
extracting keywords from the question. Supplying them skips that call *and* substitutes the
hops' discoveries for it. The seeding is therefore net-cheaper than it looks — one call is
saved on the way in.

---

## 5. Core Concepts & Key Components

### The gate is deliberately over-inclusive

Eleven patterns, in three groups:

| Group | Patterns | Catches |
|---|---|---|
| Comparison | `compare[ds]?`, `comparison`, `differences? between`, `versus`, `vs\.?`, `both`, `each of` | "Compare the Q3 and Q4 revenue figures." |
| Relation | `relationships? between`, `how .* relates? to` | "How does the leave policy relate to the contract terms?" |
| Chained reference | `that (the\|a\|an) .* (mentions?\|describes?\|defines?\|uses?\|calls?)`, `who .* that .*\?`, `which .* of the .* that` | "Which columns feed the metric that the report mentions?" |

Plus one non-regex rule: **more than one `?`** in the message means two genuine questions.

The docstring states the asymmetry outright: *a false positive costs one extra retrieval, a
false negative costs the answer.* So the gate errs toward firing. The chained-reference
group is the one that pays for the module — those are the questions where a flat retrieval
is most reliably wrong, because the sentence's real subject is never named directly.

### The decomposition prompt has an explicit escape hatch

```
You split a question into the minimum sequence of simpler lookups needed to answer
it, at most 3. Reply with one sub-question per line, numbered. No preamble, no
explanation. If the question only needs a single lookup, reply with exactly: SINGLE
```

`SINGLE` is the model's veto over the regex. The gate is over-inclusive by design, so
something has to be able to say "actually, no" — and the check is `"SINGLE" in raw.upper()`,
tolerant of a model that wraps it in punctuation or a sentence.

### The parser assumes the model won't follow instructions

`parse_subquestions` is written for what models actually return rather than what they were
asked for:

- Strips `-`, `*`, `•`, `1.` and `1)` prefixes with one regex.
- Drops any line ending in `:` — that's a preamble ("Here are the sub-questions:"), not a
  question.
- Drops a line that restates the original question.
- Truncates at `MAX_SUBQUESTIONS`.
- **Returns `[]` if fewer than two survive.**

That last rule is the important one, and its comment says why: one sub-question is just the
original reworded — no hop is gained, so it isn't multi-hop after all.

### Seeds are ranked by agreement, not by order

```python
counts[name] += 1   # across every hop's entities
ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
return [name for name, _ in ranked[:MAX_SEED_KEYWORDS]]
```

Frequency ordering matters because the list is truncated at 12. An entity that **two hops
both found** is far more likely to be the bridge between them than one that appeared once,
so it should survive the cut. The secondary sort on the name itself is there so the output
is deterministic when counts tie — which is what makes the behaviour reproducible in the
retrieval eval.

Twelve is a stated compromise: enough to steer the final retrieval, not so many that the
seeded keyword list *becomes* the query and drowns out the user's own words.

### Every failure degrades to the behaviour that would have happened anyway

| Failure | Result |
|---|---|
| `llm_model_func` raises | Warning logged, `decompose` returns `[]` → single flat retrieval |
| Model returns a non-string | `[]` |
| Model says `SINGLE` | `[]` |
| Parsing yields fewer than 2 | `[]` |
| One `aquery_data` raises | Warning logged, that hop is skipped; the others still contribute |
| Every `aquery_data` raises | `seed_keywords([])` → `[]` → the orchestrator doesn't set `ll_keywords` |

The docstring for `decompose` puts it plainly: *decomposition is an optimisation. A failure
falls back to the single flat retrieval, which is what would have happened anyway.* There is
no path through this module that can fail a chat turn.

---

## 6. Function & Component Reference

---

### `gather(rag, question, param_factory)`

**What it does:** The entry point. Runs the early hops and returns the keywords to seed the
final retrieval.

**Input:**

| Param | Type | Example |
|---|---|---|
| `rag` | `LightRAG` | the engine singleton |
| `question` | `str` | `"Compare the Q3 and Q4 revenue figures."` |
| `param_factory` | `Callable[[], QueryParam]` | `build_retrieval_param` — **uncalled** |

**Output:** `list[str]` — up to 12 entity names, most-frequent first. `[]` for a single-hop
question, and `[]` on any failure.

**Example:**
```python
seeds = await multihop.gather(rag, "Compare the Q3 and Q4 revenue figures.",
                              build_retrieval_param)
# => ["REVENUE", "Q3 REPORT", "Q4 REPORT", "ACME CORP"]

seeds = await multihop.gather(rag, "What is the leave policy?", build_retrieval_param)
# => []      no LLM call, no retrieval — the gate declined
```

**Notes:** Logs `Multi-hop: %d sub-retrievals for %r` when it fires, which is the one line to
grep for when checking whether it engaged. The hops run **sequentially** in a `for` loop —
see [§9](#9-known-limitations--open-todos). Each iteration calls `param_factory()` afresh;
passing a `QueryParam` instead of a factory is the bug this signature exists to prevent.

---

### `is_multi_hop(question)`

**What it does:** Decides whether a question looks like it spans more than one retrieval.
Pure, synchronous, no I/O.

**Input:** `question: str`. **Output:** `bool`.

**Example:** (every case below is asserted in `test_multihop.py`)
```python
is_multi_hop("Compare the Q3 and Q4 revenue figures.")              # => True
is_multi_hop("What is the difference between the two policies?")    # => True
is_multi_hop("Show me revenue vs cost.")                            # => True
is_multi_hop("Which columns feed the metric that the report mentions?")  # => True
is_multi_hop("Who is the author? What did they write?")             # => True  (two "?")
is_multi_hop("How does the leave policy relate to the contract terms?")  # => True

is_multi_hop("What is the leave policy?")                           # => False
is_multi_hop("Summarise the Q3 report.")                            # => False
is_multi_hop("How many rows are in the sales sheet?")               # => False
is_multi_hop("")                                                    # => False
```

**Notes:** All patterns are `re.IGNORECASE` and precompiled at import. Empty or
whitespace-only input short-circuits to `False`.

---

### `decompose(question)`

**What it does:** The question's hops, or `[]` if it only has one. Gate first, then at most
one LLM call.

**Input:** `question: str`. **Output:** `list[str]`, length 0 or 2–3.

**Example:**
```python
await multihop.decompose("Compare the Q3 and Q4 revenue figures.")
# => ["What were the Q3 revenue figures?", "What were the Q4 revenue figures?"]

await multihop.decompose("What is the leave policy?")
# => []      returned before the LLM call — is_multi_hop said no
```

**Notes:** Imports `llm_model_func` **inside the function body**, because
`lightrag_engine` imports from `app.services` and a module-level import would be circular.
Uses the *extraction* role deliberately — decomposition is cheap structural work, and
putting it on the answering model would make it compete with chat for the same
per-model rate-limit bucket. Every exception is caught and logged at `warning`.

---

### `parse_subquestions(raw, original)`

**What it does:** Extracts sub-questions from the model's reply, tolerating the formats
models reach for whether or not they were asked to. Pure, synchronous.

**Input:** `raw: str` (the model's reply), `original: str` (the user's question).

**Output:** `list[str]` — 2 to `MAX_SUBQUESTIONS` entries, or `[]`.

**Example:** (from `test_multihop.py`)
```python
parse_subquestions("1. What is the metric?\n2. Which columns feed it?", "original")
# => ["What is the metric?", "Which columns feed it?"]

parse_subquestions("Here are the sub-questions:\n- First lookup?\n* Second lookup?",
                   "original")
# => ["First lookup?", "Second lookup?"]      the preamble line ends in ":" and is dropped

parse_subquestions("1. Only one thing?", "original")
# => []      one sub-question is the original reworded — no hop gained

parse_subquestions("\n".join(f"{i}. Question {i}?" for i in range(1, 10)), "x")
# => 3 entries      capped at MAX_SUBQUESTIONS
```

**Notes:** The `original` parameter exists solely so a line identical to the user's own
question (case-insensitively) can be dropped — models restate the prompt surprisingly often
and it would otherwise become a wasted hop.

---

### `seed_keywords(datas)`

**What it does:** Collects the entity names the early hops actually retrieved, most frequent
first. Pure, synchronous.

**Input:** `datas: list[dict]` — the raw `aquery_data` results.

**Output:** `list[str]`, at most `MAX_SEED_KEYWORDS`.

**Example:**
```python
hop1 = {"data": {"entities": [{"entity_name": "ACME CORP"},
                              {"entity_name": "Q3 REPORT"}]}}
hop2 = {"data": {"entities": [{"entity_name": "ACME CORP"},
                              {"entity_name": "REVENUE"}]}}

seed_keywords([hop1, hop2])
# => ["ACME CORP", "Q3 REPORT", "REVENUE"]
#     ^ found by both hops, so it ranks first — it is the bridge between them
```

**Notes:** Defensive at every level — `(data or {}).get("data", {}).get("entities", []) or
[]` survives a `None` result, a missing `data` key and a `null` entities list. Blank names
are dropped after stripping. Names arrive already folded by
[`canonical_name`](entity-extraction-agent.md#the-name-fold-is-the-entity-resolver), so
`ACME CORP` and `Acme Corp` were already one entity before they got here.

---

### Constants

| Constant | Value | Rationale |
|---|---|---|
| `MAX_SUBQUESTIONS` | `3` | Also stated in the prompt, so the model is asked for at most 3 *and* the parser enforces it. Each hop is a full retrieval. |
| `MAX_SEED_KEYWORDS` | `12` | Enough to steer the final retrieval; not so many that the seed list becomes the query. |
| `_MULTI_HOP_PATTERNS` | 11 regexes | See [§5](#the-gate-is-deliberately-over-inclusive). |

---

## 7. End-to-End Walkthroughs

### 7.1 A comparison question — the fires-and-helps case

**"Compare the Q3 and Q4 revenue figures."**

1. `gather` → `decompose` → `is_multi_hop` matches `\bcompare[ds]?\b` → `True`.
2. One `llm_model_func` call with the splitting system prompt. Reply:
   ```
   1. What were the Q3 revenue figures?
   2. What were the Q4 revenue figures?
   ```
3. `parse_subquestions` strips `1.` / `2.`, finds neither line is a preamble or a restatement,
   returns both. Two entries → not filtered out.
4. Logs `Multi-hop: 2 sub-retrievals for 'Compare the Q3 and Q4 revenue figures.'`
5. Hop 1: `param_factory()` builds a fresh `QueryParam` (`stream=False`, half budget).
   `rag.aquery_data("What were the Q3 revenue figures?", param=…)` → entities including
   `Q3 REPORT`, `REVENUE`, `ACME CORP`.
6. Hop 2: **another fresh** `QueryParam` — hop 1's resolved keywords have been written onto
   the first object and must not carry over. → `Q4 REPORT`, `REVENUE`, `ACME CORP`.
7. `seed_keywords`: `REVENUE` 2, `ACME CORP` 2, `Q3 REPORT` 1, `Q4 REPORT` 1. Sorted by
   `(-count, name)` → `["ACME CORP", "REVENUE", "Q3 REPORT", "Q4 REPORT"]`.
8. Back in the orchestrator: `param.ll_keywords = seeds`.
9. `rag.aquery_llm(message, param=param)` — LightRAG **skips its own keyword extraction**
   and searches the graph and vectors for those four names directly, so both quarters'
   material is retrieved rather than a blend that landed between them.

---

### 7.2 A single-hop question — the free case

**"What is the leave policy?"**

1. `gather` → `decompose` → `is_multi_hop`: no pattern matches, one `?`. → `False`.
2. `decompose` returns `[]` **immediately** — no LLM call.
3. `gather` returns `[]`. No log line.
4. The orchestrator's `if seeds:` is false; `ll_keywords` is left unset and LightRAG does its
   own keyword extraction as normal.

**Total cost of this module for this question: one regex sweep.** That's the design goal.

---

### 7.3 A false positive — the gate fires and the model overrules it

**"Summarise both the introduction and the conclusion."**

1. `is_multi_hop` matches `\bboth\b` → `True`. This is a false positive: both sections
   live in the same document and one retrieval finds them.
2. `decompose` makes the LLM call. The model recognises a single lookup and replies
   `SINGLE`.
3. `"SINGLE" in raw.upper()` → `decompose` returns `[]`.
4. `gather` returns `[]`. The retrieval proceeds exactly as a single-hop question would.

**Cost of the false positive: one LLM call.** That's the price the over-inclusive gate pays,
and the reason the escape hatch is in the prompt.

---

### 7.4 Everything fails

1. `is_multi_hop` → `True`.
2. `llm_model_func` raises — Groq is rate-limited, OpenRouter is queued, Ollama isn't
   pulled. `decompose` logs `Query decomposition failed` with the traceback and returns `[]`.
3. `gather` returns `[]`.
4. The chat turn proceeds with a normal single retrieval and **the user sees a normal
   answer**. The only trace is the warning in the log.

If instead the decomposition succeeded but hop 1's `aquery_data` raised, that hop is skipped
with a `Sub-retrieval failed for %r` warning and hop 2 still contributes its entities. A
partial seed list is better than none.

---

## 8. Configuration & Setup

**This agent has no environment variables.** Its two tunables are module constants:

```python
MAX_SUBQUESTIONS = 3      # multihop.py
MAX_SEED_KEYWORDS = 12
```

It indirectly depends on:

| Setting | Effect |
|---|---|
| `GROQ_EXTRACT_MODEL` | Which model decomposes (via `llm_model_func`) |
| `QUERY_CONTEXT_TOKEN_BUDGET` | The sub-question budgets are derived from it in `build_retrieval_param` |
| `RERANK_ENABLED`, `RERANK_TOP_N` | Applied to sub-retrievals too |

### Tests

```bash
cd backend
pytest app/services/test_multihop.py -v
```

Ten cases. The gate is parametrised over six multi-hop and four single-hop questions; the
parser is tested against numbered lists, bullets with a preamble, the single-sub-question
rule and the cap. `seed_keywords` and `gather` are not directly tested — `gather` needs a
`rag`, and `seed_keywords` is exercised through the retrieval eval instead.

### Checking whether it fired

```bash
grep "Multi-hop:" backend/uvicorn.log
# INFO app.services.multihop: Multi-hop: 2 sub-retrievals for 'Compare the Q3 and Q4…'
```

The retrieval eval measures this as a scored dimension: `scripts/eval_questions.yaml` marks
questions as multi-hop and `scripts/eval_retrieval.py` reports **whether a question marked
multi-hop actually decomposed**, alongside retrieval and grounding. See
[BACKEND.md §6](../BACKEND.md#operational-scripts).

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **The gate is English-only and lexical.** | Eleven hardcoded English patterns. A multi-hop question in another language, or one phrased without any of these markers ("What did the author of the Q3 report previously publish?"), never fires. |
| **Hops run sequentially.** | `for sub in subquestions: datas.append(await rag.aquery_data(...))`. Three hops are three round trips where `asyncio.gather` would be one. They're independent — this is latency left on the table. |
| **Only one round of decomposition.** | A genuinely three-level question ("who wrote the paper that the report that won the award cites?") gets one flat split, not a recursive one. |
| **Sub-question results are discarded except for entity names.** | `aquery_data` returns chunks and relationships too. Only `entities[].entity_name` is used; the retrieved *text* is thrown away and re-retrieved by the final call. |
| **Seeding replaces LightRAG's keyword extraction rather than augmenting it.** | Setting `ll_keywords` makes LightRAG skip its own extraction entirely, so a term in the user's question that no hop surfaced is *not* in the final keyword set. Usually fine; occasionally it drops a term the flat search would have used. |
| **Only `ll_keywords` is seeded, not `hl_keywords`.** | LightRAG distinguishes low-level (specific entities) from high-level (themes) keywords. Only the low-level channel is used. |
| **No cap on total hop cost.** | Three sub-retrievals at half the context budget each is up to 1.5× the final call's retrieval work, and nothing bounds the wall-clock time. |
| **`MAX_SEED_KEYWORDS = 12` is untuned.** | No experiment established 12; it's a stated judgement call. |
| **Entity names only — no relationship seeding.** | The bridge between two hops is sometimes a relationship rather than an entity, and those aren't collected. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) —
  [Multi-hop question](../GLOSSARY.md#multi-hop-question),
  [Seed keywords](../GLOSSARY.md#seed-keywords), [Mix mode](../GLOSSARY.md#mix-mode),
  [Entity](../GLOSSARY.md#entity)
- [`query-orchestrator`](query-orchestrator.md) — the only caller;
  [why `param_factory` is a factory](query-orchestrator.md#two-queryparam-objects-and-why-they-cant-be-one)
- [`llm-router`](llm-router.md) — [`llm_model_func`](llm-router.md#llm_model_funcprompt-system_promptnone-history_messagesnone-keyword_extractionfalse-kwargs),
  which serves the decomposition call
- [`entity-extraction-agent`](entity-extraction-agent.md) — where the entity names the seeds
  are drawn from come from, and why they're already canonicalised
- [`BACKEND.md`](../BACKEND.md) —
  [walkthrough 7.2](../BACKEND.md#72-a-user-asks-compare-the-q3-and-q4-revenue-figures),
  [`eval_retrieval`](../BACKEND.md#operational-scripts)
- [`agents/README.md`](README.md)
