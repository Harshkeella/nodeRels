# Agent: Query Orchestrator

> `backend/app/api/chat.py` — the function that decides how a question gets answered, and
> assembles everything the user sees while it happens.

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

Every question a user asks arrives at one function, `_chat_stream`, and that function's job
is routing and assembly rather than answering. It first decides **what kind of question
this is**: if retrieval says the question is about a spreadsheet, the answer comes from
DuckDB as exact rows and the language model never touches a number. Otherwise it takes the
document path, where it may first decompose the question into sub-questions, run those as
retrievals, and feed what they discovered into the real retrieval as search terms.

Once an answer starts generating, the orchestrator streams it out as a sequence of typed
events — sources first, then the evidence chain behind those sources, then the answer text
token by token — so the interface can render each piece the moment it exists rather than
waiting for the whole response. After the text finishes it runs a faithfulness check and,
only if something looks unsupported, sends a warning frame. Finally it persists the turn.

The design principle running through all of it: **the cheap check goes first**. Deciding
whether a question is about spreadsheets costs a vector query, not an LLM call. Deciding
whether a question is multi-hop costs a regex match. Neither of the expensive paths runs
unless something cheap said it should.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **FastAPI `StreamingResponse`** | The answer is produced incrementally by an async generator. FastAPI streams whatever the generator yields, with no buffering of its own. |
| **Server-Sent Events**, hand-rolled | `f"data: {json.dumps(payload)}\n\n"`. Six lines of code, no library. SSE rather than WebSockets because the stream is one-way and a plain HTTP response reconnects, proxies and debugs like anything else. |
| **LightRAG `QueryParam`** | The retrieval configuration object. Two are built per request — one for the final answer, one factory for sub-question retrievals. |
| **`lightrag.aquery_llm`** | Returns a dict containing both the retrieval data (references, chunks, entities, relationships **after truncation**) and the generation handle, which is what makes provenance possible at all. |

No queue, no worker, no background task. One request, one generator, one connection.

---

## 3. Folder & File Structure

```
backend/app/api/
└── chat.py                    # 285 lines — this agent, plus the session CRUD routes
    ├── _sse(payload)                     # dict -> one SSE frame
    ├── build_retrieval_param()           # QueryParam for a multi-hop sub-question
    ├── build_query_param(history)        # QueryParam for the real answer
    ├── _describe_table_result(result)    # one-line summary of a DuckDB answer
    ├── _spreadsheet_answer(message, tables)
    ├── _persist(session_id, message, answer, evidence)
    ├── _chat_stream(message, history, session_id)   # THE ORCHESTRATOR
    └── @router.post("/stream") + 5 session routes
```

Services it calls, each documented separately:

```
backend/app/services/
├── spreadsheet_query.py   -> agents/spreadsheet-sql-agent.md
├── multihop.py            -> agents/multi-hop-planner.md
├── provenance.py          -> agents/grounding-verifier.md
├── grounding.py           -> agents/grounding-verifier.md
├── lightrag_engine.py     -> agents/llm-router.md
└── chat_store.py          -> BACKEND.md §6
```

---

## 4. How This Fits Into the Bigger Picture

**Upstream** — two clients, one endpoint:

| Client | Sends | Handles |
|---|---|---|
| [Frontend chat page](../FRONTEND.md#5-core-concepts--key-components) | `message`, `history`, `session_id` | every frame type |
| [Extension Chat tab](../EXTENSION.md#the-chat-panel) | `message`, `history`, **no** `session_id` | `sources`, `token`, `error`, `done` only |

**Downstream** — five services and one storage layer:

```
                        _chat_stream(message, history, session_id)
                                        │
              ┌─────────────────────────┴──────────────────────────┐
              ▼                                                    ▼
   spreadsheet_query.relevant_tables()                    (no tables matched)
              │  tables found                                      │
              ▼                                                    ▼
   spreadsheet_query.answer()                        multihop.gather()
              │                                                    │  seed keywords
              ▼                                                    ▼
      frames: table, token                            rag.aquery_llm()  ← llm-router
              │                                                    │
              │                                          provenance.build_evidence()
              │                                                    │
              │                                       frames: sources, evidence, token…
              │                                                    │
              │                                          grounding.check()
              │                                                    │
              └──────────────────► chat_store (_persist) ◄─────────┘
```

The **contract this agent owns** is the SSE frame vocabulary. It's documented in full at
[BACKEND.md `POST /api/v1/chat/stream`](../BACKEND.md#post-apiv1chatstream). Adding a frame
type is a non-breaking change because both clients ignore unknown types; renaming or
removing one is not.

---

## 5. Core Concepts & Key Components

### The routing decision, and why it's free

The first thing `_chat_stream` does is ask `spreadsheet_query.relevant_tables(rag, message)`
whether this question is about tabular data. That function does **not** ask a model — it
queries the entity vector store with the raw message and keeps only hits whose graph node
carries a `workbook`/`worksheet`/`column` label.

That works because [`tabular_graph`](../BACKEND.md#tabular_graphprojectrag-workbook-doc_id)
writes a [schema card](../GLOSSARY.md#schema-card) as each column node's description, and
that description is indexed. Naming a column in a question hits its card exactly on the
sparse half of hybrid search. So the router is a vector query the system was going to be
capable of anyway, and the common case — a question that isn't about data — costs one
retrieval and no LLM round trip.

The comment in the source is explicit that this was **not** always the case: previously any
existing spreadsheet sent every message through the SQL router first, which was a coin flip
that cost an LLM call every time.

### Two `QueryParam` objects, and why they can't be one

`build_query_param` configures the real answer: streaming on, references on, reranking on,
the full context budget, and a long `user_prompt` that constrains the answer's shape.

`build_retrieval_param` is a **factory**, and the docstring says why: LightRAG writes
resolved keywords *back onto* the `QueryParam` it was given. A shared instance would leak
hop 1's keywords into hop 2 and the second retrieval would be searching for the first
one's terms. Each sub-question gets a fresh object.

The sub-question params also differ in kind, not just in size: `stream=False`, and half the
token budget — because a hop's job is to **name entities** for the final retrieval, not to
assemble a full answer context.

### The derived token budgets

`build_query_param` sets four budgets, all derived from one setting:

```python
max_total_tokens    = QUERY_CONTEXT_TOKEN_BUDGET       # 8000
chunk_top_k         = RERANK_TOP_N                     # 10
max_entity_tokens   = QUERY_CONTEXT_TOKEN_BUDGET // 4  # 2000
max_relation_tokens = QUERY_CONTEXT_TOKEN_BUDGET // 3  # 2666
```

The comment explains the bug this fixes: `max_total_tokens` only budgets the **chunk** half.
The knowledge-graph half is capped separately, and LightRAG's defaults there are 6,000
entity + 8,000 relation tokens. Leaving those at their defaults let the assembled context
blow past the budget on entities alone and **starve chunks to zero** — a retrieval-augmented
answer with no retrieved text in it. Deriving all three from one knob is what stops them
drifting apart again.

### Ordering of the frames, and why it matters

`sources` goes out before `evidence`, and both go out before the first `token`. That's
deliberate: the provenance button has to be live as soon as its source chip renders,
otherwise the user sees a chip appear and then a button appear next to it a moment later.

`grounding` goes out **after** the answer, because the check runs on the finished text — and
it's only sent when something was actually unsupported, so a clean answer carries no frame
at all rather than a "0 problems" one the UI would have to know to hide.

### Failure is never silent, and never fatal to the turn

Three different failure policies, chosen per concern:

| Failure | Policy | Why |
|---|---|---|
| `aquery_llm` raises | `error` frame, generator returns | Nothing was produced; say so. |
| Streaming raises mid-answer | `error` frame, generator returns | Partial tokens already reached the user and are kept. |
| `SpreadsheetError` | `error` frame + `done` | An invalid-SQL failure is a real answer to give. |
| `_persist` raises | **logged, not raised** | The answer already streamed. A store failure must not turn a successful turn into a failed one. |
| Session doesn't exist | logged warning, turn not persisted | Same reasoning. |

---

## 6. Function & Component Reference

---

### `_chat_stream(message, history, session_id=None)`

**What it does:** The orchestrator. An async generator that yields SSE frames as strings.

**Input:**

| Param | Type | Example |
|---|---|---|
| `message` | `str` | `"What is the leave policy?"` |
| `history` | `list[dict]` | `[{"role": "user", "content": "Hi"}]` |
| `session_id` | `str \| None` | `"3f2a…"`, or `None` to answer without persisting |

**Output:** `AsyncIterator[str]` — each yielded string is one complete SSE frame including
its trailing blank line.

**Example** (the sequence of yields for a document question):
```python
'data: {"type": "sources", "sources": [{"reference_id": "1", "file_path": "handbook.pdf"}]}\n\n'
'data: {"type": "evidence", "evidence": [{"reference_id": "1", …}]}\n\n'
'data: {"type": "token", "text": "Employees accrue "}\n\n'
'data: {"type": "token", "text": "20 days of annual leave per year."}\n\n'
'data: {"type": "done"}\n\n'
```

**The full decision tree:**

```
1.  rag = await get_rag()
2.  tables = await spreadsheet_query.relevant_tables(rag, message)
3.  if tables:
        result = await _spreadsheet_answer(message, tables)
        if result and result["error"]:  yield error, done; RETURN
        if result is not None:
            yield table, token(summary); _persist(…, evidence=[]); yield done; RETURN
        # result is None -> not a data question after all, fall through
4.  param = build_query_param(history)
5.  seeds = await multihop.gather(rag, message, build_retrieval_param)
    if seeds: param.ll_keywords = seeds
6.  result = await rag.aquery_llm(message, param=param)     # may raise -> error, RETURN
7.  if result["status"] == "failure":  yield error; RETURN
8.  yield sources   (reference_id + file_path per reference)
9.  yield evidence  (provenance.build_evidence(data))
10. stream tokens from llm_response["response_iterator"], accumulating `answer`
11. verdict = grounding.check(answer, evidence)
    if verdict["unsupported"]: yield grounding
12. await _persist(session_id, message, answer, evidence)
13. yield done
```

**Notes:** Step 3's `result is None` branch is the subtle one — `relevant_tables` can match
a schema card while the SQL writer then replies `NO_SQL` because the question isn't really
about the data. That returns `None` and the document path takes over, which is exactly what
should happen. A spreadsheet answer persists with **empty evidence** and runs no grounding
check: DuckDB rows are exact, not retrieved prose, so there's no evidence chain to show and
nothing for the check to do.

---

### `build_query_param(history)`

**What it does:** Builds the `QueryParam` for the real, answer-producing retrieval.

**Input:** `history: list[dict]` — the prior turns.

**Output:** `QueryParam`.

**Example:**
```python
param = build_query_param([{"role": "user", "content": "Hi"}])
# QueryParam(
#   mode="mix", stream=True, conversation_history=[…], include_references=True,
#   enable_rerank=True, chunk_top_k=10,
#   max_total_tokens=8000, max_entity_tokens=2000, max_relation_tokens=2666,
#   response_type="the shortest possible answer that fully answers the question — …",
#   user_prompt="Be precise and to the point. …")
```

**Notes:** The `user_prompt` is doing real work and is worth reading in full in the source.
Its most important clause is the grounding instruction: *"Use ONLY the retrieved knowledge
base context; never fall back on outside/general knowledge, **even for a follow-up or
comparison question that continues the conversation history** — re-ground every answer in
the current retrieved context."* That parenthetical exists because a conversational
follow-up is exactly where a model reaches for what it already "knows" instead of what was
retrieved. The prompt also specifies the refusal: one short sentence saying the knowledge
base has no information, and stop — which is why
[`grounding.check`](grounding-verifier.md) has to special-case a refusal as not-a-claim.

---

### `build_retrieval_param()`

**What it does:** Builds a `QueryParam` for one multi-hop sub-question — retrieval only, no
generation.

**Input:** none. **Output:** `QueryParam`.

**Example:**
```python
build_retrieval_param()
# QueryParam(mode="mix", stream=False, enable_rerank=True, chunk_top_k=10,
#            max_total_tokens=4000,      # budget // 2
#            max_entity_tokens=1000,     # budget // 8
#            max_relation_tokens=1333)   # budget // 6
```

**Notes:** Passed to `multihop.gather` as a **factory**, not as an instance — see
[§5](#two-queryparam-objects-and-why-they-cant-be-one). The budgets are smaller than the
final call's and weighted differently: a hop wants entity names, so proportionally more of
its (smaller) budget goes to entities and relations than in the answer call.

---

### `_describe_table_result(result)`

**What it does:** Turns a DuckDB result into the one-line summary that streams as the
answer's text.

**Input:** `result: dict` from `spreadsheet_query.answer`.

**Output:** `str`.

**Example:**
```python
_describe_table_result({"total_row_count": 2, "truncated": False, …})
# => "2 rows."

_describe_table_result({"total_row_count": 500, "truncated": True, …})
# => "500 rows (capped at 500)."

_describe_table_result({"added_column": "margin", "table": "workbook_a1b2__sales", …})
# => "Added the **margin** column to `workbook_a1b2__sales`."
```

**Notes:** The rows themselves go in the `table` frame; this is only the prose beside them.
It's also what gets persisted as the assistant message content, which is why an old session
reopens showing "2 rows." rather than a blank bubble — the table itself is **not**
persisted. See [§9](#9-known-limitations--open-todos).

---

### `_spreadsheet_answer(message, tables)`

**What it does:** Wraps `spreadsheet_query.answer` so a `SpreadsheetError` becomes a
returnable value instead of an exception crossing the generator.

**Input:** `message: str`, `tables: list[str]`.

**Output:** `dict | None` — the result, `{"error": "…"}`, or `None` for "not a spreadsheet
question after all".

**Example:**
```python
await _spreadsheet_answer("How many rows in sales?", ["workbook_a1b2__sales"])
# => {"columns": ["count_star()"], "rows": [[120]], "total_row_count": 1,
#     "truncated": False, "sql": "SELECT COUNT(*) FROM workbook_a1b2__sales"}

await _spreadsheet_answer("Who wrote the handbook?", ["workbook_a1b2__sales"])
# => None   (the model replied NO_SQL; the document path takes over)
```

**Notes:** Carries a `ponytail:` marker — a spreadsheet answer does **not** also run
document retrieval. The two paths are exclusive. Run both if cross-referencing ever needs
the prose alongside the rows.

---

### `_persist(session_id, message, answer, evidence)`

**What it does:** Writes the finished turn to the session store, and names the thread on the
first exchange.

**Input:** `session_id: str | None`, `message: str`, `answer: str`, `evidence: list[dict]`.

**Output:** `None`.

**Example:**
```python
await _persist("3f2a…", "What is the leave policy?", "Employees accrue 20 days…", evidence)
# writes a user row and an assistant row (with evidence as JSON), and — if the
# session's title is still "New chat" — replaces it with chat_store.generate_title(message)
```

**Notes:** Returns immediately when `session_id` is `None`, which is how the extension and
an unsaved dashboard chat work. **Every** failure inside is caught and logged: the answer
has already streamed by the time this runs, so a store failure is a logging concern, not a
user-facing one. An unknown `session_id` logs a warning and drops the turn rather than
creating a session the client didn't ask for.

---

### `_sse(payload)`

```python
_sse({"type": "token", "text": "hello"})
# => 'data: {"type": "token", "text": "hello"}\n\n'
```

The whole SSE implementation.

---

### `chat_stream(payload)` — the route

```python
@router.post("/stream")
async def chat_stream(payload: ChatRequest):
    return StreamingResponse(
        _chat_stream(payload.message, [h.model_dump() for h in payload.history],
                     payload.session_id),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
```

**Notes:** Both headers matter. `X-Accel-Buffering: no` stops nginx (and several other
proxies) buffering the whole response before forwarding it — without it, streaming works
in development and silently stops working behind a reverse proxy, arriving in one lump.

---

## 7. End-to-End Walkthroughs

### 7.1 A document question with a multi-hop shape

**"How does the leave policy relate to the contract terms?"**

1. `relevant_tables` runs a vector query. No workbook/worksheet/column node scores high
   enough. Returns `[]`. **Cost: one vector query, no LLM call.**
2. `build_query_param(history)`.
3. `multihop.gather(rag, message, build_retrieval_param)`:
   - `is_multi_hop` matches `\bhow\s+.*\brelates?\s+to\b` → `True`.
   - `decompose` makes one `llm_model_func` call → `["What is the leave policy?", "What are
     the contract terms?"]`.
   - Two `rag.aquery_data` calls, each with a **fresh** `QueryParam` from the factory.
   - `seed_keywords` counts entity names across both results and returns up to 12,
     most-frequent first — e.g. `["ACME CORP", "LEAVE POLICY", "EMPLOYMENT CONTRACT"]`.
4. `param.ll_keywords = seeds`. LightRAG will now skip its own keyword extraction.
5. `rag.aquery_llm(message, param=param)` → hybrid retrieval, rerank, context assembly,
   generation via [`query_llm_func`](llm-router.md).
6. `sources` frame from `data["references"]`.
7. `evidence` frame from `provenance.build_evidence(data)` — what survived truncation.
8. `token` frames as the iterator yields.
9. `grounding.check(answer, evidence)` — suppose every sentence is supported, so **no
   `grounding` frame is sent**.
10. `_persist` writes both turns and, if this was the first exchange, generates the title.
11. `done`.

---

### 7.2 A spreadsheet question

**"What's the total revenue by quarter?"**

1. `relevant_tables` queries the entity vector store. The `Revenue` column's
   [schema card](../GLOSSARY.md#schema-card) — *"Column revenue (header 'Revenue', currency,
   DOUBLE) of worksheet 'Sales'… queryable as workbook_a1b2__sales.revenue"* — matches on
   the sparse half. Its graph node has label `column`, so its `table` property is collected.
   Returns `["workbook_a1b2__sales"]`.
2. `_spreadsheet_answer` → `spreadsheet_query.answer(message, tables)`:
   - `schema_context(tables)` renders **only that table's** DDL.
   - One `query_llm_func` call → `SELECT quarter, SUM(revenue) AS total FROM
     workbook_a1b2__sales GROUP BY quarter`.
   - `run_select` validates (one statement, `SELECT`, `DESCRIBE`-bound) and executes with a
     row cap.
3. `_describe_table_result` → `"4 rows."`.
4. Frames: `table` (columns + rows + the SQL), then `token` with the summary.
5. `_persist(session_id, message, "4 rows.", [])` — **empty evidence**, deliberately.
6. `done`. The document path, `multihop`, `provenance` and `grounding` **never run**.

---

### 7.3 A question about spreadsheets that isn't a data question

**"Who sent me the sales spreadsheet?"**

1. `relevant_tables` matches — the word "spreadsheet" and "sales" hit the worksheet card.
   Returns `["workbook_a1b2__sales"]`.
2. `spreadsheet_query.answer` builds the schema context and asks the model for SQL. The
   model correctly replies `NO_SQL` — the question is about provenance, not about the rows.
   `answer` returns `None`.
3. `result is None`, so the `if result is not None` guard fails and **execution falls
   through** to the document path.
4. From here it's walkthrough 7.1 without the multi-hop branch: `gather` returns `[]`
   (no pattern matched, no LLM call), and the answer comes from document retrieval.

This fall-through is the reason `answer()` distinguishes `None` from `{"error": …}`. A
question the SQL writer declines is not a failure; it's a routing correction.

---

## 8. Configuration & Setup

This agent has no settings of its own. It reads three:

| Setting | Default | Effect here |
|---|---|---|
| `QUERY_CONTEXT_TOKEN_BUDGET` | `8000` | Sets all four budgets on both `QueryParam`s |
| `RERANK_ENABLED` | `true` | `enable_rerank` on both params |
| `RERANK_TOP_N` | `10` | `chunk_top_k` on both params |

### Watching it work

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Relevant log lines, in order of usefulness:

```
INFO app.services.multihop: Multi-hop: 2 sub-retrievals for 'How does the leave policy…'
INFO app.services.spreadsheet_query: Attempt 1 rejected (Query references something that
     does not exist: Referenced column "revenu" not found)
INFO app.api.chat: Grounding: 1/4 sentences unsupported
WARNING app.api.chat: Persisting chat turn failed
```

The absence of the multi-hop line means the heuristic gate declined — which for an ordinary
question is correct and costs nothing.

### Testing the stream by hand

```bash
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message": "What is the leave policy?", "history": []}'
```

`-N` disables curl's own buffering, without which you'll see the whole response at once and
conclude streaming is broken.

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **The two answer paths are exclusive** (`ponytail:` in source) | A spreadsheet answer runs no document retrieval, so "what does the contract say about the customers in this sheet?" gets rows or prose, never both. |
| **The `table` payload is not persisted** | `_persist` stores the summary string (`"4 rows."`) but not the rows. Reopening an old session shows the sentence and an empty space where the table was. The evidence chain *is* persisted, so document answers reopen intact — the asymmetry is unintentional. |
| **`history` is passed verbatim, unbounded** | A long conversation sends its whole history into `conversation_history` on every turn. Nothing truncates it, so a fifty-turn thread eats the context budget the retrieved chunks need. |
| **Sub-retrievals are sequential** | `multihop.gather` awaits each `aquery_data` in a loop. Three hops is three round trips where `asyncio.gather` would be one. |
| **No cancellation propagation** | When a client aborts, the generator is closed but an in-flight `aquery_llm` keeps running to completion — the LLM call is paid for either way. |
| **Retrieval runs before routing, twice** | `relevant_tables` performs its own `entities_vdb.query`, and then the document path performs the full retrieval again. The first query's results are discarded. |
| **No per-request timeout** | A slow provider fallback chain (Groq 429 → wait → OpenRouter queue → Ollama on CPU) can leave a request open for minutes with only `X-Accel-Buffering` keeping the connection alive. |
| **`grounding` is the only quality signal** and it's lexical | See [`grounding-verifier`](grounding-verifier.md) §9. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [SSE](../GLOSSARY.md#sse-server-sent-events),
  [Mix mode](../GLOSSARY.md#mix-mode),
  [Query context token budget](../GLOSSARY.md#query-context-token-budget),
  [Seed keywords](../GLOSSARY.md#seed-keywords),
  [Evidence chain](../GLOSSARY.md#evidence-chain)
- [`BACKEND.md`](../BACKEND.md) —
  [the endpoint contract](../BACKEND.md#post-apiv1chatstream),
  [walkthrough 7.2](../BACKEND.md#72-a-user-asks-compare-the-q3-and-q4-revenue-figures)
- **The services it calls:**
  - [`multi-hop-planner`](multi-hop-planner.md) — step 5
  - [`spreadsheet-sql-agent`](spreadsheet-sql-agent.md) — steps 2–3
  - [`grounding-verifier`](grounding-verifier.md) — steps 9 and 11
  - [`llm-router`](llm-router.md) — what actually generates the answer
- **Its clients:**
  - [`FRONTEND.md`](../FRONTEND.md#streamchatmessage-history-handlers-signal-sessionid)
  - [`EXTENSION.md`](../EXTENSION.md#streamchatmessage-history-handlers-signal)
- [`agents/README.md`](README.md) — the other agents
