# Security

## The model in one line

The client proves who it is with a token it cannot forge; the server derives
every storage path from that proof and from nothing else.

## Authentication

* Bearer tokens, not cookies. The dashboard and the extension are both
  cross-origin to the API, and a bearer token needs no CSRF story.
* Verified locally with the project's HS256 JWT secret (`app/core/auth.py`).
  The API never calls the auth provider on the request path, so a sign-in
  outage cannot take retrieval down.
* `exp` and `sub` are **required** claims, and the audience must be
  `authenticated` — a Supabase anon key is signed with the same secret and is
  not a user, so it is rejected.
* A rejected token's reason is never echoed back: the response is a flat
  `401 Not authenticated`, because "bad signature" versus "bad audience" tells
  an attacker which half of a forgery to fix. The one exception is expiry,
  which the user needs to see to know to sign in again.

## Authorization

There is no authorization *check* to forget, because there is no unscoped
store to check against. `get_rag()`, `manifest`, `chat_store` and
`get_connection()` each resolve the current identity themselves and open only
that user's data. See `docs/MULTI_TENANCY.md`.

Client-supplied identity is never read. A `user_id` in a body, a query string
or a header is ignored; the only identity is the token's `sub`. Tested in
`test_identity_comes_from_the_token_not_the_request`.

Every application router carries `dependencies=[Depends(get_current_user)]` at
the **router** level, so a route added later cannot ship public by forgetting a
decorator. `/health` is the only public endpoint.

## Server-path folder ingestion

`POST /api/v1/ingest/folder` reads a directory on the server's own disk. That
is safe only when the caller *is* the machine; on a shared deployment it is an
arbitrary-file-read endpoint with a knowledge base attached.

It now returns **403 whenever authentication is on**, and is confined to
`FOLDER_INGEST_ROOT` when it is off. A hosted deployment offers file upload
instead, which is quota-checked and never names a server path.

## Path traversal

User ids come from a verified JWT and are UUIDs, but they still land in a
filesystem path, so `auth.user_dir()` refuses anything that is not a single
safe path component (`/`, `\`, `.`, `..`, empty). LightRAG applies its own
`validate_workspace` to the same value independently. Tested, parametrised, in
`test_a_user_id_that_is_not_a_path_component_is_refused`.

## Generated SQL

The spreadsheet path lets an LLM write SQL. Three guardrails were already in
place — single-`SELECT` parse, bind against the real catalog, row cap — and
`enable_external_access=False` stops a generated statement reaching the disk
through `read_csv`/`read_text`.

Multi-tenancy adds a fourth that does not depend on the other three: **each
user's tables live in their own DuckDB file**. A statement that defeated every
validator still could not name another user's table, because it is not in the
database the connection is attached to.

## Limits

| Limit | Setting | Enforced |
|---|---|---|
| Storage per user | `STORAGE_QUOTA_BYTES` (5 GB) | before the LightRAG insert, so an over-quota upload costs no embedding or extraction |
| Single file | `MAX_FILE_SIZE_BYTES` | on the bytes read, before any parser sees them |
| Batch total / count | `MAX_BATCH_SIZE_BYTES`, `MAX_FILES_PER_BATCH` | same |
| LLM requests/min per user | `LLM_LIMIT_USER_RPM` | waited out — a burst refills in seconds |
| LLM concurrency per user | `LLM_LIMIT_USER_CONCURRENT` | semaphore |
| LLM tokens/day per user | `LLM_LIMIT_USER_TOKENS_PER_DAY` | refused — waiting out a daily reset is an outage, not a timeout |
| Provider TPM (global) | `GROQ_TPM_LIMIT` | shared token bucket, self-correcting from Groq's own 429 body |

The per-user LLM ceilings exist so that exhaustion is contained to whoever
caused it: without them one person ingesting a book spends the whole free
tier's daily tokens and everybody else's chat silently drops to Ollama.

## CORS

`CORS_ORIGINS` is an explicit list and `allow_credentials=True`. Never set it
to `*` — the browser refuses that combination anyway, and the failure looks
like a network error rather than a configuration mistake.

## Secrets

| Value | Where it lives | Public? |
|---|---|---|
| Supabase JWT secret | backend env | **No** — it is the signing key |
| Google OAuth client secret | Supabase dashboard | **No** — never in this repo |
| Supabase anon key | frontend env, extension settings | Yes, by design |
| Supabase project URL | same | Yes |
| Groq / OpenRouter keys | backend env | **No** |

The extension stores a short-lived access token and a refresh token in
`chrome.storage.local` — a token, never a secret. A compromised browser profile
leaks minutes of access, and signing out ends it.

## What is logged

Request-scoped: user id, document id, operation, status. Never: passwords,
tokens, file contents, or full prompts. Opik tracing is opt-in via
`OPIK_API_KEY` and `ingest_document` already declares
`ignore_arguments=["text"]`, so document text is not shipped to it.

## Known gaps

* **Limits are per-process.** The LLM buckets and semaphores are in-process.
  Correct for the single-container deployment this targets; move them to Redis
  before running a second replica.
* **Ingestion is synchronous.** A large document occupies its request. Bounded
  by the upload limits above, but a slow upload is still a held connection.
* **No account lockout.** Password attempt throttling is Supabase's, not ours.
* **Original uploads are not retained.** Only extracted text is stored, so a
  user cannot re-download what they uploaded.
