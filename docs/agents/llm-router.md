# Agent: LLM Router

> `backend/app/services/lightrag_engine.py` + `rate_limiter.py` — which model serves which
> job, what happens when it refuses, and how the system stays inside a free tier.

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

Everything in this system that talks to a language model goes through one of two functions,
and this module owns both. It is called a router because that is what it does: it decides
**which model** handles a request based on what the request is *for*, and it decides **what
to do when that model says no**.

The two jobs are answering a user's question and building the knowledge graph. They're
pointed at different models on purpose. Not for quality reasons — for rate-limit reasons.
A free-tier provider enforces a per-model tokens-per-minute ceiling, so if answering and
ingestion shared a model they would share a bucket, and a large upload would make chat stop
working. Splitting them gives each its own budget.

Beyond routing, this module contains the accumulated knowledge of what a free tier actually
does to you. It paces extraction calls with a token bucket so the limit is never hit rather
than hit-and-retried. It reads the provider's own 429 body to learn the real ceiling and
resizes the bucket. It distinguishes a per-minute throttle (wait it out, ~60 seconds) from
a per-day exhaustion (don't wait, put the model on a cooldown so the other forty in-flight
chunks skip it too). And it checks at boot that the local fallback is actually usable,
because discovering that mid-extraction turns a recoverable quota problem into a 404
traceback and a failed document.

It also owns construction of the single `LightRAG` instance the whole process shares.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **`lightrag.RoleLLMConfig`** | LightRAG's mechanism for pointing different internal jobs at different callables. It's what makes routing possible without forking LightRAG. |
| **`lightrag.llm.openai.openai_complete_if_cache`** | One OpenAI-compatible client used for all three providers. Groq, OpenRouter and Ollama all speak the OpenAI API, so `base_url` + `api_key` is the entire difference between them. |
| **`openai.RateLimitError`** | The typed exception that carries the provider's 429 body — which is where the real TPM ceiling and the retry-after are parsed from. |
| **`sentence-transformers`** | The local embedding model. Loaded lazily into a module global and run through `asyncio.to_thread` so it never blocks the event loop. |
| **`httpx`** | One use only: the boot-time Ollama `/api/tags` check. |
| **`opik`** | `@opik.track` on every provider function, so a trace shows which provider actually served a call. |

No LangChain, no provider SDKs, no abstraction layer. Three functions with different
`base_url`s.

---

## 3. Folder & File Structure

```
backend/app/services/
├── lightrag_engine.py         # 424 lines
│   ├── _get_sentence_transformer() / _embed(texts)      # local embeddings
│   │
│   ├── _ollama_complete(...)          @opik.track       # provider 3
│   ├── _openrouter_complete(...)      @opik.track       # provider 2
│   ├── _groq_complete_with_rate_limit_retry(model, …)   # provider 1 + its 429 policy
│   │
│   ├── _TPM_LIMIT_RE / _RETRY_AFTER_RE                  # parse Groq's 429 body
│   ├── _learn_tpm_limit(exc) / _retry_after_seconds(exc, default)
│   ├── _is_long_window_rate_limit(exc)                  # TPM vs TPD
│   ├── _groq_model_cooldown_until / _groq_model_on_cooldown(model)
│   │
│   ├── llm_model_func(...)    @opik.track   # ROLE: extract + keyword + default
│   ├── query_llm_func(...)    @opik.track   # ROLE: query (answering)
│   │
│   ├── check_ollama_fallback()                          # boot check
│   ├── _warn_if_context_budget_exceeds_tpm()            # boot check
│   ├── get_rag() / shutdown_rag()                       # the singleton
│   └── _rag: LightRAG | None                            # module global
│
└── rate_limiter.py            # 47 lines
    ├── TokenBucket(tokens_per_minute)
    │   ├── acquire(tokens)          # wait, then spend
    │   └── resize(tokens_per_minute)
    └── estimate_tokens(prompt, system_prompt, output_allowance=1000)
```

---

## 4. How This Fits Into the Bigger Picture

This module sits **below** everything and is called **by** everything.

```
   get_rag()  ← app.main lifespan, every service, every endpoint
       │
       └── constructs the one LightRAG with:
             vector_storage   = qdrant_store.register()      → HybridQdrantStorage
             embedding_func   = _embed                       → local MiniLM
             rerank_model_func= reranker.rerank              → local cross-encoder
             llm_model_func   = llm_model_func               → the default role
             role_llm_configs = {
                 "query":   query_llm_func       ← chat answers
                 "extract": gliner_extract       ← entity-extraction-agent  (or Groq)
                 "keyword": (metadata only)      ← served by llm_model_func
                 "vlm":     (metadata only)      ← declared for the boot log
             }

   Direct callers of the role functions, bypassing LightRAG:
     multihop.decompose()          → llm_model_func      (question decomposition)
     chat_store.generate_title()   → llm_model_func      (session titles)
     spreadsheet_query._generate() → query_llm_func      (SQL writing)
```

That last group is worth noticing: three services import the role functions directly
because they need a model but aren't part of LightRAG's own pipeline. They inherit the full
fallback chain for free.

### The fallback chain

Both roles use the same three providers in the same order, for the same reason — Groq is
the low-latency path, OpenRouter's free-tier auto-router **queues badly under load** so it
sits behind rather than in front, and Ollama is local and CPU-bound so it's last.

```
  llm_model_func  (extract / keyword / default)
    │
    ├─ Groq  GROQ_EXTRACT_MODEL   ← paced by the TPM token bucket
    │        ├─ 429 (TPM) → wait the header's retry-after, retry up to N times
    │        └─ 429 (TPD) → put the model on cooldown, raise immediately
    ├─ OpenRouter  OPENROUTER_MODEL
    └─ Ollama  OLLAMA_MODEL       ← raises a useful message if not pulled

  query_llm_func  (answering)
    │
    ├─ Groq  GROQ_MODEL           ← NOT paced: chat is one call at a time
    ├─ OpenRouter
    └─ Ollama
```

---

## 5. Core Concepts & Key Components

### Why two models rather than one

`GROQ_MODEL` and `GROQ_EXTRACT_MODEL` both default to `llama-3.3-70b-versatile`, so it
looks like the split does nothing. It doesn't, *today* — but the split is structural and
the config comments explain why it exists: they must be *able* to differ, because a Groq
rate limit is **per model**. If chat and ingest shared a model they would share a bucket,
and a large upload would 429 every chat message for the duration.

The choice of 70b for *both* is itself a rate-limit decision, not a quality one, and the
comments are blunt about it:

- **For answering:** a chat call sends the whole assembled context in one request.
  `llama-3.1-8b-instant` has a 6,000 TPM ceiling, which cannot fit LightRAG's default
  30,000-token context — so every chat call 429'd and silently fell back to OpenRouter.
  70b-versatile gets 12,000.
- **For extraction:** ingest speed is capped by tokens-per-minute, not model speed. So
  extraction goes on whichever model has the **larger** budget, which is again the 70b.

The intuition that a smaller model is faster is exactly backwards here.

### Proactive pacing beats reactive backoff

`_extract_budget = TokenBucket(GROQ_TPM_LIMIT)` paces extraction calls. Before each Groq
extraction call, `llm_model_func` awaits `acquire(estimate_tokens(prompt, system_prompt))`
— it waits until the budget has refilled enough, then spends it.

The `rate_limiter` docstring states the case: reactive 429-then-backoff wastes a round trip
*and* the retry wait on every burst; spending the budget at the rate it refills avoids the
429 entirely.

**Chat is deliberately not paced.** It's one call at a time, so pacing it would only add
latency to a request that was never going to exhaust a bucket.

### The bucket learns its own size

`GROQ_TPM_LIMIT` is a guess, and it will be wrong — the limit differs per model and changes
with your tier. So when a 429 does arrive, `_learn_tpm_limit` parses the real ceiling out of
the body:

```
Rate limit reached for model `llama-3.3-70b-versatile` … (TPM): Limit 6000,
Used 3286, Requested 2757. Please try again in 430ms.
              ^^^^                                        ^^^^^
        _TPM_LIMIT_RE                              _RETRY_AFTER_RE
```

and calls `_extract_budget.resize(6000)`. It also logs the value to put in `.env` so the
next boot skips the discovery. `_retry_after_seconds` reads the second number so the wait is
the provider's own figure rather than a flat guess — and handles both `ms` and `s`.

### TPM and TPD need opposite responses

This is the distinction the module is built around.

| | Per-minute (TPM) | Per-day (TPD) |
|---|---|---|
| Clears in | ~60 seconds | tens of minutes |
| Right response | **Wait it out.** Far faster than a CPU-bound Ollama call for the rest of a large document. | **Don't wait.** Fall back immediately. |
| Implemented by | `GROQ_RATE_LIMIT_RETRIES` extra attempts, sleeping the retry-after | `_groq_model_cooldown_until[model] = now + GROQ_DAILY_QUOTA_COOLDOWN_SECONDS`, then re-raise |

The cooldown is the important half. Without it, every one of the remaining chunks in a
document makes the same doomed Groq call — and each one first pays
`openai_complete_if_cache`'s own internal 3× retry backoff before failing. Remembering the
exhausted model for five minutes turns forty wasted retry cycles into forty immediate
fallbacks.

The 300-second default is deliberately **shorter** than the ~30–60 minute reset a busy day
actually sees, so quota freed by usage dropping off gets picked back up rather than being
locked out for the full window.

### Two boot-time checks, both about failures that are unreadable later

**`check_ollama_fallback()`** — Ollama is the last resort for both roles. Calling it with a
model that was never pulled 404s *per chunk* and fails the whole document. Worse, it fails
as a `NotFoundError` traceback, which hides the real cause (Groq quota) under an unrelated
error. So the check runs once at boot, sets `_ollama_ready`, and `_ollama_complete` raises
a message that names the actual fix:

```
No LLM available: Groq failed and the Ollama fallback model 'llama3.2' is not
pulled. Run `ollama pull llama3.2` or set OPENROUTER_API_KEY.
```

A known-bad fallback is worse than no fallback.

**`_warn_if_context_budget_exceeds_tpm()`** — a chat call sends its whole context in one
request, so a context budget above the query model's per-minute ceiling **can never
succeed**. No amount of retrying or backoff fits a 30,000-token request through a 6,000 TPM
window. Saying so once at boot beats discovering it on every request.

### One instance, built lazily, torn down explicitly

`_rag` is a module global built on the first `get_rag()`. `shutdown_rag()` finalizes
LightRAG's storages **and** closes the Qdrant client — embedded Qdrant holds an exclusive
lock on its directory until closed, so a `--reload` that skipped this couldn't reopen the
store.

The `role_llm_configs` also carry `metadata={"binding": …, "model": …}` on every role. That
metadata does nothing functional; it's what LightRAG's boot-time "Role LLM Configuration"
log prints. Without it every role reports `None/None` and the log can't answer "which model
is actually serving chat?"

---

## 6. Function & Component Reference

---

### `llm_model_func(prompt, system_prompt=None, history_messages=None, keyword_extraction=False, **kwargs)`

**What it does:** The graph-building and keyword-extraction model call. Paced, then
Groq → OpenRouter → Ollama.

**Input:**

| Param | Type | Example |
|---|---|---|
| `prompt` | `str` | `"Question: Compare Q3 and Q4.\n\nSub-questions:"` |
| `system_prompt` | `str \| None` | `"You split a question into the minimum sequence…"` |
| `history_messages` | `list \| None` | `None` |
| `keyword_extraction` | `bool` | `False` |

**Output:** `str` (or an `AsyncIterator[str]` when the caller asked to stream).

**Example:**
```python
raw = await llm_model_func(
    "Question: Compare the Q3 and Q4 revenue figures.\n\nSub-questions:",
    system_prompt="You split a question into the minimum sequence of simpler lookups…",
)
# => "1. What were the Q3 revenue figures?\n2. What were the Q4 revenue figures?"
```

**Notes:** Bound as LightRAG's default `llm_model_func`, so it serves the `keyword` role and
(when `EXTRACTION_BACKEND=llm`) the `extract` role. Also called **directly** by
[`multihop.decompose`](multi-hop-planner.md) and
[`chat_store.generate_title`](../BACKEND.md#chat-store). The Groq branch is skipped entirely
if the model is on cooldown or no key is set. Each provider failure is logged at `warning`
with the exception text, so the log shows the chain being walked.

---

### `query_llm_func(prompt, system_prompt=None, history_messages=None, keyword_extraction=False, **kwargs)`

**What it does:** The answering call. Same chain, different model, **no pacing**.

**Output:** `str | AsyncIterator[str]`.

**Example:**
```python
sql = await query_llm_func(
    "Schema:\nTABLE workbook_a1b2__sales  -- worksheet \"Sales\" of q3.xlsx\n"
    "  revenue DOUBLE  -- currency\n  quarter VARCHAR  -- categorical\n\n"
    "Question: What's the total revenue by quarter?",
    system_prompt="You translate questions into DuckDB SQL over spreadsheet tables…",
)
# => "SELECT quarter, SUM(revenue) AS total FROM workbook_a1b2__sales GROUP BY quarter"
```

**Notes:** Bound as the `query` role, and called directly by
[`spreadsheet_query._generate`](spreadsheet-sql-agent.md). Not paced because chat is one
call at a time and pacing it would add latency for no benefit.

---

### `_groq_complete_with_rate_limit_retry(model, prompt, system_prompt, history_messages, keyword_extraction, **kwargs)`

**What it does:** One Groq call, riding out per-minute 429s.

**Input:** `model: str` plus the standard LLM-function arguments.

**Output:** `str | AsyncIterator[str]`. Raises `RateLimitError` when the attempts are spent
or when the limit is a daily one.

**Behaviour:**

```
for attempt in range(GROQ_RATE_LIMIT_RETRIES + 1):
    try:  return openai_complete_if_cache(model, …, base_url="https://api.groq.com/openai/v1")
    except RateLimitError as e:
        if _is_long_window_rate_limit(e):        # "tokens per day" / "(tpd)"
            _groq_model_cooldown_until[model] = now + GROQ_DAILY_QUOTA_COOLDOWN_SECONDS
            raise
        _learn_tpm_limit(e)                      # resize the bucket from the body
        if last attempt: raise
        sleep(_retry_after_seconds(e, GROQ_RATE_LIMIT_WAIT_SECONDS))
```

**Notes:** These retries are **on top of** `openai_complete_if_cache`'s own three internal
attempts with 4–10s backoff. The docstring justifies the extra rounds: waiting out Groq's
~60-second window is far faster than falling back to a CPU-bound Ollama call for the rest
of a large document.

---

### `check_ollama_fallback()`

**What it does:** Verifies at boot that the Ollama fallback model is actually pulled, and
sets the module flag that gates `_ollama_complete`.

**Input:** none. **Output:** `bool`. Side effect: sets `_ollama_ready`.

**Example:**
```python
await check_ollama_fallback()
# => True, and logs:  Ollama fallback ready: llama3.2

# Or, when the model isn't pulled:
# => False, and logs:
#    OLLAMA_MODEL 'llama3.2' is not pulled (available: mistral) — Groq failures
#    will fail extraction instead of falling back. Fix with: ollama pull llama3.2

# Or, when Ollama isn't running:
# => False, and logs:
#    Ollama unreachable at http://localhost:11434 (…) — Groq failures will have no fallback
```

**Notes:** Strips the `/v1` suffix off `OLLAMA_BASE_URL` because `/api/tags` is Ollama's own
API, not the OpenAI-compatible one. The name match is tolerant of tagging: Ollama reports
`llama3.2:latest` while `OLLAMA_MODEL` is usually untagged, so both the full name and the
part before the colon are compared. Every failure path is a **warning**, never an exception
— being offline must not stop the server booting.

---

### `get_rag()` / `shutdown_rag()`

**What they do:** Build (once) and tear down the process-wide LightRAG instance.

**Output:** `LightRAG` / `None`.

**Example:**
```python
rag = await get_rag()   # first call: boot checks, then construction
rag2 = await get_rag()  # same object
await shutdown_rag()    # finalize storages, then qdrant_store.close_client()
```

**What `get_rag()` configures**, in order:

1. `check_ollama_fallback()` and `_warn_if_context_budget_exceeds_tpm()` — both boot checks.
2. `role_llm_configs` for `query`, `keyword` and `vlm`.
3. The `extract` role: `gliner_extract` with `max_async=GLINER_MAX_ASYNC` when
   `EXTRACTION_BACKEND=gliner`, otherwise metadata pointing at Groq.
4. `rerank_model_func` — imported only if `RERANK_ENABLED`.
5. `LightRAG(...)` with `vector_storage=qdrant_store.register()`, the local `_embed`,
   the chunk sizes, `entity_extract_max_gleaning`, `llm_model_max_async`, and
   `entity_extraction_use_json=True`.
6. `await _rag.initialize_storages()`.

**Notes on two of those:** `vector_storage` is a **string** (`"HybridQdrantStorage"`) that
`register()` returns after adding the class to LightRAG's three registries — that's the
whole integration. And `entity_extraction_use_json=True` is there because JSON-structured
extraction output is far more robust than LightRAG's default delimiter format on weaker
models: a malformed delimiter mid-response drops the rest of that chunk's entities, whereas
`json_repair` can recover slightly-off JSON.

---

### `_embed(texts)`

```python
await _embed(["Employees accrue 20 days of annual leave."])
# => ndarray, shape (1, 384), L2-normalised
```

Loads the SentenceTransformer lazily into a module global; runs `model.encode` in a thread
with `normalize_embeddings=True`. Normalising at encode time is what lets Qdrant's cosine
distance be a plain dot product.

---

### `TokenBucket(tokens_per_minute)`

`backend/app/services/rate_limiter.py`

**What it is:** An async token bucket. `capacity` is the per-minute budget; `rate` is
`capacity / 60` tokens per second.

**Methods:**

| Method | Behaviour |
|---|---|
| `await acquire(tokens)` | Refill by elapsed time, then wait in a loop until `tokens` are available and spend them. A request larger than the whole bucket is clamped to `capacity` so it drains the bucket rather than waiting forever. |
| `resize(tokens_per_minute)` | Change capacity and rate; clamp the current balance down if it now exceeds capacity. |

**Example:**
```python
budget = TokenBucket(12000)
await budget.acquire(5000)   # returns immediately — the bucket starts full
await budget.acquire(5000)   # returns immediately
await budget.acquire(5000)   # waits ~10s for the third 5000 to refill

budget.resize(6000)          # Groq's 429 said the real ceiling is 6000
```

**Notes:** Carries a `ponytail:` marker — one `asyncio.Lock` serializes waiters, so callers
go **FIFO, not fair-share**. Fine at `MAX_ASYNC_LLM=2`; revisit if many roles ever share one
bucket.

---

### `estimate_tokens(prompt, system_prompt, output_allowance=1000)`

```python
estimate_tokens("a" * 4000, "b" * 400)   # => 1100
```

`(len(prompt) + len(system_prompt or "")) // 4 + output_allowance`. Four characters per
token is a rough English average, and the allowance is there because **Groq's TPM counts
the response too** — budgeting only the request would consistently under-spend and still
429.

---

## 7. End-to-End Walkthroughs

### 7.1 Ingesting a large document that exhausts the per-minute budget

`EXTRACTION_BACKEND=llm`, `GROQ_TPM_LIMIT=12000`, `MAX_ASYNC_LLM=2`, a 200-chunk document.

1. LightRAG's pipeline starts two chunk extractions concurrently. Each calls
   `llm_model_func`.
2. `estimate_tokens` says ~5,000 each. `_extract_budget.acquire(5000)` — the bucket started
   at 12,000, so both return immediately. Balance: ~2,000.
3. Chunk 3 asks for 5,000. The bucket has ~2,000 and refills at 200/second, so `acquire`
   sleeps ~15 seconds. **No 429 is generated.** This is the pacing doing its job.
4. Suppose the real ceiling is 6,000, not the configured 12,000. A call gets through before
   the bucket knows better and Groq returns 429 with
   `(TPM): Limit 6000 … try again in 430ms`.
5. `_is_long_window_rate_limit` → `False` (no "tokens per day"). `_learn_tpm_limit` parses
   `6000`, logs a warning naming the env var to set, and calls `resize(6000)`. The bucket's
   rate halves and every subsequent `acquire` paces to the true limit.
6. `_retry_after_seconds` reads `430ms` → sleeps 0.43s, retries, succeeds.
7. Ingest continues, now correctly paced, with no further 429s.

---

### 7.2 The daily quota runs out mid-document

1. Chunk 140's call returns 429 with `Rate limit reached … tokens per day (TPD)`.
2. `_is_long_window_rate_limit` → `True`. `_groq_model_cooldown_until["llama-3.3-70b-versatile"]
   = now + 300`, and the exception is re-raised **without retrying** — waiting is pointless.
3. `llm_model_func` catches it, logs `Groq extraction failed (…); falling back`, and tries
   OpenRouter.
4. Chunks 141–200 call `llm_model_func`. Each checks `_groq_model_on_cooldown` **first** and
   skips the Groq branch entirely — no `acquire`, no request, no 3× internal backoff. They
   go straight to OpenRouter.
5. Five minutes later the cooldown expires. The next chunk tries Groq again; if the quota
   has partially freed, it succeeds and the cheap path resumes.

Without the cooldown, steps 4–5 would be sixty chunks each paying a full retry cycle before
failing. That is the entire reason the dict exists.

---

### 7.3 First boot on a machine with no keys at all

1. `get_rag()` runs `check_ollama_fallback()`. `httpx` can't reach `localhost:11434` →
   logs *"Ollama unreachable … Groq failures will have no fallback"*, returns `False`,
   `_ollama_ready` stays `False`.
2. `_warn_if_context_budget_exceeds_tpm()` returns immediately — no `GROQ_API_KEY`, so
   there's nothing to compare against.
3. LightRAG is constructed. `EXTRACTION_BACKEND=gliner` (the default), so the `extract` role
   is `gliner_extract` — **local, needing no provider at all**.
4. The user uploads a PDF. Chunking, embedding and extraction all run locally and the
   document indexes completely. The knowledge graph is built.
5. The user asks a question. `query_llm_func`: no Groq key, no OpenRouter key → falls to
   `_ollama_complete`, which sees `_ollama_ready is False` and raises the actionable
   message. `_chat_stream` catches it and sends an `error` frame.

The point of the walkthrough: with the default local extractor, **ingestion works with zero
API keys**. Only answering needs a provider, and when it's missing the error names the fix.

---

## 8. Configuration & Setup

| Variable | Default | What it does |
|---|---|---|
| `GROQ_API_KEY` | — | Blank skips Groq entirely for both roles. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Answering. A **TPM decision** — must exceed `QUERY_CONTEXT_TOKEN_BUDGET` + ~2,000. |
| `GROQ_EXTRACT_MODEL` | `llama-3.3-70b-versatile` | Graph building and keywords. Wants the *larger* TPM budget. |
| `GROQ_TPM_LIMIT` | `12000` | Initial bucket size. Self-corrects on the first 429. |
| `GROQ_RATE_LIMIT_RETRIES` | `2` | Extra Groq attempts on a per-minute 429, beyond LightRAG's own 3. |
| `GROQ_RATE_LIMIT_WAIT_SECONDS` | `20` | Wait used only when the 429 body carries no retry-after. |
| `GROQ_DAILY_QUOTA_COOLDOWN_SECONDS` | `300` | How long a TPD-exhausted model is skipped. |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | — / `openrouter/free` | Second provider. Blank skips it. |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434/v1` / `llama3.2` | Last resort. |
| `MAX_ASYNC_LLM` | `2` | Concurrent extraction calls. Sized so ~5,000-token calls fit a 12,000 TPM bucket. |
| `EXTRACTION_BACKEND` | `gliner` | `gliner` routes the `extract` role to the local encoder; `llm` keeps it on Groq. |
| `QUERY_CONTEXT_TOKEN_BUDGET` | `8000` | Read only by the boot-time warning here. |

### Diagnosing it from the log

```
INFO  app.lightrag_engine: Ollama fallback ready: llama3.2
WARN  app.lightrag_engine: Query role llama-3.1-8b-instant has a 6000 TPM ceiling but a
      chat call needs ~10000 tokens … Every chat query will 429 and fall back.
WARN  app.lightrag_engine: Groq reports a 6000 TPM ceiling; resizing the extraction
      budget (was 12000). Set GROQ_TPM_LIMIT=6000 to skip this on the next boot.
WARN  app.lightrag_engine: Groq rate-limited on llama-3.3-70b-versatile (attempt 1/3);
      waiting 0.4s
WARN  app.lightrag_engine: Groq extraction failed (…); falling back
```

LightRAG's own boot log prints a "Role LLM Configuration" table showing binding and model
per role — that's what the `metadata` dicts in `role_llm_configs` are for.

### Running with no provider at all

Set `EXTRACTION_BACKEND=gliner` (the default) and leave every key blank. Ingestion works
completely. Chat will fail with the Ollama message until you set a key or `ollama pull` the
model.

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **FIFO, not fair-share** (`ponytail:`) | One lock serializes bucket waiters. At `MAX_ASYNC_LLM=2` it's invisible; with many roles on one bucket a slow caller would head-of-line block. |
| **Only the extract role is paced** | Chat, multi-hop decomposition, SQL writing and title generation all call the model unpaced. They're low-volume, but a burst of concurrent chats has nothing throttling it. |
| **The cooldown is per model, not per key** | Two models on the same exhausted account are tracked separately, so the second one rediscovers the same TPD 429. |
| **OpenRouter and Ollama have no rate-limit handling at all.** | Only Groq's 429s are parsed. An OpenRouter 429 is caught by the generic `except Exception` and falls straight through to Ollama. |
| **`_learn_tpm_limit` only ever resizes the extraction bucket** | The query role has no bucket, so a learned limit doesn't help it. |
| **Token estimation is ~4 chars/token** | Fine for English prose; wrong for code, CJK text and heavily-tokenized identifiers, all of which this system ingests. |
| **`_ollama_ready` is checked once, at boot** | Ollama starting *after* the server means the fallback stays disabled until a restart. |
| **No circuit breaker on OpenRouter** | Unlike Groq, a persistently failing OpenRouter is retried on every single call. |
| **The 300s cooldown is a guess** | The comment says so — deliberately shorter than the real 30–60 minute window, trading some wasted retries for picking freed quota back up. |
| **`_rag` is a module global with no lock** | Two concurrent first-requests could both enter `get_rag()` and construct two instances. In practice the app lifespan calls it at boot, so the race isn't reachable. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [Fallback chain](../GLOSSARY.md#fallback-chain),
  [Role LLM config](../GLOSSARY.md#role-llm-config),
  [Token bucket](../GLOSSARY.md#token-bucket), [TPD / TPM](../GLOSSARY.md#tpd--tpm),
  [Namespace](../GLOSSARY.md#namespace)
- [`BACKEND.md`](../BACKEND.md) — [§2 Tech Stack](../BACKEND.md#2-tech-stack),
  [`get_rag`/`shutdown_rag`](../BACKEND.md#get_rag--shutdown_rag),
  [`TokenBucket`](../BACKEND.md#tokenbuckettokens_per_minute--acquiretokens-resizetokens_per_minute),
  [§8 environment variables](../BACKEND.md#8-configuration--setup)
- **Its callers:**
  - [`query-orchestrator`](query-orchestrator.md) — the chat path
  - [`multi-hop-planner`](multi-hop-planner.md) — calls `llm_model_func` directly
  - [`spreadsheet-sql-agent`](spreadsheet-sql-agent.md) — calls `query_llm_func` directly
- **What it wires in:** [`entity-extraction-agent`](entity-extraction-agent.md) — the
  `extract` role
- [`agents/README.md`](README.md)
