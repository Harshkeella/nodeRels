# LLM operations

## Chain, unchanged

```
Groq  ->  OpenRouter  ->  Ollama
```

Per role, as before: `query` answers chat on `GROQ_MODEL`; `extract` and
`keyword` run graph building on `GROQ_EXTRACT_MODEL` (and `extract` is the
local GLiNER encoder entirely when `EXTRACTION_BACKEND=gliner`, which is the
default). Embeddings and reranking were always local and still are. Nothing
moved to a paid model.

## Two ceilings, because they fail differently

| Scope | Limit | On breach |
|---|---|---|
| Global | `GROQ_TPM_LIMIT` token bucket | pace — spend at the refill rate, avoiding the 429 entirely |
| Per user | `LLM_LIMIT_USER_RPM` | **wait** — a burst refills in seconds and the caller barely notices |
| Per user | `LLM_LIMIT_USER_CONCURRENT` | wait on a semaphore |
| Per user | `LLM_LIMIT_USER_TOKENS_PER_DAY` | **refuse** — waiting out a daily reset is an outage, not a timeout |

The global bucket already existed; the per-user half is what stops one user's
book ingest from spending everybody's day. Both wrap the same two entry points
(`llm_model_func`, `query_llm_func`), so every LLM call in the app is covered
including multi-hop planning and spreadsheet SQL generation.

## Fallback behaviour

Unchanged and still exercised by `test_rate_limit_fallback.py`: a Groq 429 that
reports a per-minute throttle is waited out (its body carries the real ceiling,
which resizes the bucket); a tokens-per-day 429 puts that model on cooldown and
drops straight to OpenRouter, then Ollama. A missing Ollama model is reported
at boot, not discovered mid-document.

## Usage accounting

`services/llm_limits.py` writes one row per call into the user's own
`usage.sqlite3`: provider, model, operation, input tokens, output tokens,
estimated cost, timestamp. `GET /api/v1/me/usage` returns today's and this
month's totals.

"Which user consumed the most this month" is then a query per user directory:

```sql
SELECT SUM(input_tokens + output_tokens) FROM llm_usage
WHERE created_at >= '2026-08';
```

Two honest limits: token counts are the same ~4-chars-per-token estimate the
rate limiter uses, not the provider's own `usage` block; and a *streaming*
response is charged for its input only, because counting its output would mean
wrapping the iterator and delaying every chunk on its way to the user. Set
`LLM_COST_PER_INPUT_TOKEN` / `..._OUTPUT_TOKEN` to attribute spend — zero on a
free tier is correct, and the token ledger still answers the question.
