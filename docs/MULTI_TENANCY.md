# Multi-tenancy

## The shape of it

    request
      -> get_current_user()          verifies the JWT, sets a ContextVar
        -> auth.workspace()          LightRAG workspace  = user id
        -> auth.user_dir()           storage/users/<id>/ = everything else

Isolation is enforced at the four places state is **opened**, not at the forty
places they are called:

| Store | Isolated by | Enforced in |
|---|---|---|
| Vectors (Qdrant) | `workspace_id` payload filter, applied in the query | `qdrant_store.HybridQdrantStorage.query` (LightRAG) |
| Graph (NetworkX) | one `graph_*.graphml` per workspace | LightRAG `networkx_impl` |
| KV / doc status | one directory per workspace | LightRAG `json_kv_impl` |
| Documents | one `manifest.sqlite3` per user | `services/manifest.py` |
| Chat | one `chat.sqlite3` per user | `services/chat_store.py` |
| Spreadsheets | one `spreadsheets.duckdb` per user | `parsers/spreadsheet.py` |
| LLM usage | one `usage.sqlite3` per user | `services/llm_limits.py` |

Two consequences worth stating plainly:

* **There is no unscoped store to reach.** An endpoint that forgot a filter is
  not a hole, because `get_rag()` / `manifest` / `chat_store` /
  `get_connection()` resolve the current identity themselves. There is no
  variant of them that returns everybody's data.
* **The vector filter is a database filter.** `HybridQdrantStorage.query`
  passes `workspace_id` into both prefetch branches *and* the fused query, so
  another user's chunks are never in the candidate set to begin with. No
  post-filtering in Python.

## Why LightRAG's `workspace`, not a `user_id` column

Because it already exists and is already enforced end to end. LightRAG
namespaces the graph file, every KV store, the doc-status rows, the Qdrant
point ids (`compute_mdhash_id_for_qdrant(..., prefix=workspace)`) and the
Qdrant payload — and its Qdrant backend already creates the
`is_tenant=True` keyword index for exactly this. Setting
`workspace = user id` turned the entire retrieval core multi-tenant without a
line of filtering code, and preserved every retrieval, graph, spreadsheet,
provenance and grounding behaviour untouched.

Qdrant stays a **single collection per namespace**, tenant-partitioned by
payload — which is what Qdrant recommends, and what its tenant index is for.
Nothing here creates a collection per user.

## The local user

`LOCAL_USER_ID` (default `local`) maps to workspace `""` and to the flat
`storage/` layout — the exact paths a single-user install already wrote. So
`AUTH_DISABLED=true` behaves precisely as before this change, and no migration
is needed to keep an existing local knowledge base working.

## What is NOT isolated, deliberately

`storage/scraped_articles/` is a shared cache of **public web pages**, keyed by
URL. Two users who clip the same article share the fetched bytes. Nothing
private passes through it — the content is whatever the URL serves to anyone —
and re-ingesting a URL stays free. Set `SCRAPED_ARTICLES_DIR` under a per-user
path if you would rather not share even that.

## Tests

`backend/app/api/test_multi_tenancy.py` proves the guarantee rather than
asserting it: unauthenticated, forged, expired and wrong-audience tokens are
rejected; a client-supplied user id is ignored; and user A's documents, chat
sessions, spreadsheet tables and quota are invisible and unreachable from
user B — including by guessing a document id.
