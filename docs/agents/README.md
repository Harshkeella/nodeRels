# Agents

Index of the orchestration components in this repository. Twelve of them, in two families
that share no code, no process and no storage.

- [What counts as an agent here](#what-counts-as-an-agent-here)
- [Family A — nodeRels backend orchestration](#family-a--noderels-backend-orchestration)
- [Family B — Deck Studio](#family-b--deck-studio)
- [Reading order](#reading-order)
- [Cross-family comparison](#cross-family-comparison)
- [See also](#see-also)

---

## What counts as an agent here

A component that **makes a decision** rather than performing a fixed transformation: it
routes, plans, resolves, validates, repairs, or chooses between paths — and it has a policy
for what to do when the thing it depends on fails.

By that test, a PDF parser is not an agent (bytes in, text out) but the SQL agent is (it
decides whether the question is even about data, writes a query, and retries on rejection).

Two families answer to the name in this repository, and the split is worth knowing before you
read anything:

| | **Family A** | **Family B** |
|---|---|---|
| Where | `backend/app/` | `Agents/A1_pptx/` |
| What | The nodeRels knowledge base's orchestration layer | **Deck Studio**, a standalone deck-generation application |
| Runs as | Part of one FastAPI process on `:8000` | Its own FastAPI process, its own React editor on `:5173`, its own `.env` |
| Shares with the other | **Nothing.** No code, no storage, no process. |

Family B is documented here because it is what the `Agents/` directory literally contains.
Family A is documented here because those are the components the word "agent" describes in
this system.

---

## Family A — nodeRels backend orchestration

Eight components. All live in `backend/app/` and share one process, one LightRAG instance
and one storage directory.

### [`query-orchestrator`](query-orchestrator.md)
`api/chat.py`

Every question a user asks arrives at one function, and that function's job is routing and
assembly rather than answering. It decides whether a message is about spreadsheet data (in
which case DuckDB answers and no model touches a number) or about documents, runs multi-hop
planning when the question warrants it, streams the answer as typed SSE frames, builds the
evidence chain, runs the faithfulness check, and persists the turn. Its governing principle
is that **the cheap check goes first** — routing costs a vector query, not an LLM call.

### [`llm-router`](llm-router.md)
`services/lightrag_engine.py` + `rate_limiter.py`

Which model serves which job, and what happens when it refuses. Answering and graph building
are pointed at different models for **rate-limit** reasons, not quality ones: a free tier
enforces a per-model token budget, so sharing a model would mean a large upload stops chat
working. Contains the accumulated knowledge of what a free tier actually does to you — a
token bucket that paces rather than retries, a 429 parser that learns the real ceiling, a
per-model cooldown that distinguishes a per-minute throttle from a daily exhaustion, and two
boot checks for failures that are unreadable later.

### [`multi-hop-planner`](multi-hop-planner.md)
`services/multihop.py`

Splits a question that spans two facts into sub-questions, retrieves each with **no
generation**, and seeds the entity names those hops found into the final search. It adds no
second generation path — the answer still comes from one call; what changes is what that call
searches for. A regex gate means an ordinary question never reaches the model at all.

### [`spreadsheet-sql-agent`](spreadsheet-sql-agent.md)
`services/spreadsheet_query.py`

Natural language to validated DuckDB SQL to exact rows. **No cell value is ever answered by a
language model** — the model writes a `SELECT`, three guardrails check it (one statement, must
be `SELECT`, every name bound against the real catalog with `DESCRIBE` before a row is read),
and DuckDB does the arithmetic. Self-heals by handing a rejected query and its exact error
back to the model, three attempts.

### [`entity-extraction-agent`](entity-extraction-agent.md)
`services/gliner_extract.py` + `graph_schema.py`

Every chunk of every document passes through here, and what it produces is the knowledge
graph. A **local encoder** rather than a language model — which is the reason a 200-page book
can be ingested at all. Registered as LightRAG's `extract` role and disguised as an LLM: it
receives LightRAG's own prompt, pulls the chunk out with a regex, and returns the same JSON an
LLM would have. Its quieter half, `canonical_name()`, is the project's entity resolver.

### [`folder-ingestion-agent`](folder-ingestion-agent.md)
`services/folder_ingest.py`

Walks a directory on the server's disk and mirrors it into the graph — every folder a node,
every file a node, nothing extracted or inferred. Document leaves are additionally routed
through the normal ingestors so their *contents* are searchable. Structured as two phases
around one **durability point**: the tree and every code symbol are flushed to disk before any
slow work starts, because a client timeout once cancelled the walk and lost everything past
the first document.

### [`code-intelligence-agent`](code-intelligence-agent.md)
`services/code_intel.py`

Classes, functions, methods, imports, inheritance and who-calls-whom, hung off the code file
nodes. Defined by what it **refuses**: call resolution is by name and only when exactly one
candidate matches, because a wrong `CALLS` edge is worse than a missing one — nothing
downstream can tell it's wrong. Two parser backends behind one interface, the non-Python one
optional and degrading cleanly to "no symbols".

### [`grounding-verifier`](grounding-verifier.md)
`services/grounding.py` + `provenance.py`

What the answer was built from, and which of its sentences that evidence actually supports.
Because the answer has already streamed, it **flags rather than strips** — the verdict rides
its own SSE frame and the UI attaches it as a warning. The test is lexical rather than an LLM
judge, catching the failure that matters: a fluent sentence full of terms that appear nowhere
in what was retrieved.

---

## Family B — Deck Studio

Four components in `Agents/A1_pptx/`. Notes in, deck out, then edit it in a browser.

### [`deck-planner`](deck-planner.md)
`backend/ppt.py`

One Groq call with **strict constrained decoding** writes the whole deck plan — the model
cannot emit invalid JSON or an unknown field, which removes an entire class of parsing code. A
checker then finds the faults a JSON schema can't express ("exactly one title slide, and it
must be first"), one repair round fixes them, and a deterministic salvage pass handles what's
left by **demoting** slides rather than dropping them. The same module renders the `.pptx`,
including native PowerPoint charts whose data stays editable.

### [`deck-assist`](deck-assist.md)
`backend/assist.py`

The in-editor assistant. The model reads a structured summary of what the user selected and
returns **operations** — never prose, never state. Every operation names an id that must
already exist, goes through the document's own validator, and applies to a *copy*. An invented
id is refused and reported; an off-site image URL is stripped. A bad answer costs a message
saying so.

### [`image-engine`](image-engine.md)
`backend/image_engine/`

`plan → search → licence → rank → fetch → validate → dedupe → select`. An LLM turns a slide
into a visual brief; three stock providers are queried concurrently; anything outside the
allowed licence set is dropped **before ranking**, because a share-alike image is a legal
problem rather than a ranking one. Four scored signals, real Pillow decoding to catch error
pages served as images, and a perceptual hash so slide 4 can't reuse slide 2's photo. It never
raises for an empty result.

### [`visual-coverage`](visual-coverage.md)
`backend/deck.py`

The canonical document — a deck is a list of elements, and there is exactly one description of
it, which is why the editor cannot show you something the export will not. Also the trust
boundary every element enters through, and the pass that guarantees no content slide arrives
as nothing but text. It reads the elements a slide **actually ended up with**, never asking
the model whether it added a picture: *that is asking the thing that just failed to do it
whether it did it.*

---

## Reading order

**If you want to understand how a question gets answered:**

1. [`query-orchestrator`](query-orchestrator.md) — the decision tree
2. [`llm-router`](llm-router.md) — what actually generates
3. [`multi-hop-planner`](multi-hop-planner.md) and
   [`spreadsheet-sql-agent`](spreadsheet-sql-agent.md) — the two branches
4. [`grounding-verifier`](grounding-verifier.md) — what happens after

**If you want to understand how the knowledge graph gets built:**

1. [`entity-extraction-agent`](entity-extraction-agent.md) — prose → entities, and the name
   fold
2. [`folder-ingestion-agent`](folder-ingestion-agent.md) — directories → structure
3. [`code-intelligence-agent`](code-intelligence-agent.md) — source files → a call graph

**If you want Deck Studio:**

1. [`visual-coverage`](visual-coverage.md) — read the document model **first**; everything
   else is a loop over it
2. [`deck-planner`](deck-planner.md) — how a deck gets written
3. [`image-engine`](image-engine.md) — how it gets illustrated
4. [`deck-assist`](deck-assist.md) — how it gets edited

---

## Cross-family comparison

The two families never touch, but they solve some of the same problems, and comparing the
answers is the fastest way to understand either.

| Problem | Family A (nodeRels) | Family B (Deck Studio) |
|---|---|---|
| **Untrusted model output** | `spreadsheet_query._validate_select` — parse, bind, forbid, cap | `deck.clean_element` — coerce or drop, never raise |
| **Model failure** | Fallback chain: Groq → OpenRouter → Ollama | Fallback *behaviour*: `fallback_plan` (no LLM), `salvage` (offline) |
| **Verifying the model did the job** | `grounding.check` on the finished text | `deck.has_visual` on the finished elements |
| **When verification fails** | **Flag** — the answer already streamed | **Repair** — deterministic, offline, no second call |
| **Rate limits** | `TokenBucket` paces *before* the 429 | `ppt.fit` trims the source to one TPM window |
| **Structured LLM output** | Prompt discipline + `json_repair` (via LightRAG) | Strict constrained decoding — invalid output is impossible |
| **Refusing to guess** | `resolve_call` returns `None` on ambiguity | `_figures` returns `[]` on mixed units |

Two shared convictions run through both, and they are worth stating plainly because they
explain most of the code:

1. **The application checks the model's work; it never asks the model whether it succeeded.**
2. **A refusal that says so is better than a guess that doesn't.**

---

## See also

- [`../GLOSSARY.md`](../GLOSSARY.md) — every term used across these documents
- [`../BACKEND.md`](../BACKEND.md) — the service Family A lives inside
- [`../FRONTEND.md`](../FRONTEND.md) — what renders Family A's output
- [`../EXTENSION.md`](../EXTENSION.md) — the other client of the chat endpoint
- [`../README.md`](../README.md) — the documentation index and suggested reading order
- `Agents/A1_pptx/README.md` — Deck Studio's own user-facing documentation
- `Agents/A1_pptx/backend/image_engine/README.md` — the image engine's own documentation
