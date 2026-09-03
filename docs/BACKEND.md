# Backend

> The FastAPI service in `backend/`. It owns ingestion, the knowledge graph, the vector
> store, the tabular store and the chat endpoint. Every other part of the system is a
> client of this one.

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

The backend takes anything you can point it at — a PDF, a spreadsheet, a web article, a
YouTube video, a folder of source code, or a block of pasted text — and turns it into two
things at once: a set of searchable text chunks, and a **knowledge graph** of the entities
and relationships found inside them. When you later ask a question, it searches both.

The searching is deliberately two-sided. A *dense* vector search finds text that means the
same thing as your question even when it shares no words with it. A *sparse* keyword
search finds an exact token — a part number, a column header, a surname — which the dense
search is bad at. Both run in one database call and their results are fused. Whatever
comes back is re-scored by a local cross-encoder, capped to a token budget, assembled into
a prompt alongside the relevant slice of the graph, and streamed back as an answer with
its sources attached.

Two things are handled specially because getting them wrong is worse than not doing them.
**Spreadsheets** never have their numbers answered by a language model: the rows live in
DuckDB, the model only writes a `SELECT` that is parsed and bound against the real schema
before a single row is read. **Code folders** get parsed into a call graph, and a call
whose target is ambiguous is recorded as unresolved rather than wired to a guess.

Everything runs locally by default. Embeddings, entity extraction and reranking are local
models with no API key; the vector store is embedded Qdrant with no server; the graph and
the key-value stores are files on disk. The only thing that needs a network is answering,
and even that falls back through three providers to a local Ollama.

---

## 2. Tech Stack

Read off `backend/requirements.txt` and `backend/requirements-codeintel.txt`.

### Core

| Package | Version | Why this one |
|---|---|---|
| `fastapi` | ≥0.115 | Async by default, and native `StreamingResponse` — chat is SSE, which a sync framework makes awkward. Pydantic models double as the API schema. |
| `uvicorn[standard]` | ≥0.32 | The ASGI server. `[standard]` pulls in `uvloop`/`httptools` where available. |
| `pydantic` | ≥2.9 | Request/response schemas (`app/models/schemas.py`). |
| `python-multipart` | ≥0.0.12 | Required for `UploadFile` — file uploads fail without it. |
| `python-dotenv` | ≥1.0 | Loads `backend/.env` at import time in `app/core/config.py`. |

### RAG engine

| Package | Version | Why this one |
|---|---|---|
| `lightrag-hku` | ≥1.4 | The RAG framework. It supplies chunking, extraction orchestration, entity merging, context assembly and generation. Crucially it's *pluggable* at exactly the seams this project needed: storage backends resolve by class name, LLM functions are injectable per role, and the extractor is just another LLM role — which is what let a local encoder replace it without touching anything downstream. |
| `qdrant-client` | ≥1.12 | The vector store. Chosen over LightRAG's default NanoVectorDB (a JSON file scanned in-process, dense-cosine only) because it supports **named vectors and server-side RRF fusion**, which is the entire hybrid-search feature. Runs *embedded* out of a directory by default — no Docker, no server — and points at a real instance by setting one env var. |
| `sentence-transformers` | ≥3.2 | Two jobs: the embedding model (`all-MiniLM-L6-v2`, 384-dim) and the reranking cross-encoder (`ms-marco-MiniLM-L-6-v2`). Both local, no key. One dependency covers both. |
| `gliner` | ≥0.2.20 | Zero-shot NER. Replaces per-chunk LLM extraction, which was bound to ~2 chunks/minute against Groq's free-tier token budget — a 200-page book never finished. GLiNER is one forward pass per ~150-word window with no quota at all. |

### Parsing

| Package | Version | Why this one |
|---|---|---|
| `pymupdf` | ≥1.24 | PDF text extraction. Fast, and good at multi-column layouts where simpler extractors interleave the columns. |
| `trafilatura` | ≥1.12 | Main-content extraction from HTML, and the direct-fetch fallback for URLs. It drops nav/sidebar/footer, which is straight token savings before the LLM sees anything. |
| `youtube-transcript-api` | ≥1.2 | Transcripts without the YouTube Data API (no key, no quota). |
| `openpyxl` | ≥3.1 | Reads `.xlsx`/`.xlsm` **twice** — once with `data_only=True` for values, once without for formulas — which is the only way to capture both the computed number and the formula that produced it. |
| `duckdb` | ≥1.1 | The tabular store. An embedded analytical database: real types, real SQL, real aggregate arithmetic. It also provides `extract_statements` and `DESCRIBE` for validating generated SQL without executing it. |
| `httpx` | ≥0.27 | Async HTTP for ZenRows and the Ollama health check. |
| `pyyaml` | ≥6.0 | Frontmatter on cached article scrapes; the eval question set. |

### Storage & observability

| Package | Version | Why this one |
|---|---|---|
| `aiosqlite` | ≥0.20 | The document manifest and chat store. SQLite because a document inventory and a session list are small, relational, mutable and single-writer — none of which is what a graph or a vector index is for. |
| `opik` | ≥1.7 | LLM call tracing (Comet). Every provider function is wrapped in `@opik.track`. Disabled by leaving `OPIK_API_KEY` blank. |

### Optional extras (`requirements-codeintel.txt`)

Everything here degrades in a **defined** way when absent — nothing raises.

| Package | Absent behaviour |
|---|---|
| `tree-sitter-language-pack` ≥0.7 | Non-Python code files are still ingested as `CodeFile` leaves, they just carry no symbols. Python is unaffected — it uses the standard library's `ast`. |
| `pillow` ≥11.0 | `Image` nodes carry format and byte size but no pixel dimensions. |

---

## 3. Folder & File Structure

Generated from the repository. Test files (`test_*.py`) sit next to what they test rather
than in a separate tree; they're listed but not individually described.

```
backend/
├── .env                          # Local secrets and overrides (gitignored)
├── .env.example                  # Every setting, documented inline — the real reference
├── requirements.txt              # Core dependencies
├── requirements-codeintel.txt    # Optional: tree-sitter grammars + Pillow
│
├── app/
│   ├── main.py                   # FastAPI app, CORS, router mounting, lifespan/warmup
│   │
│   ├── core/
│   │   └── config.py             # Settings dataclass read from env; @lru_cache singleton
│   │
│   ├── models/
│   │   └── schemas.py            # Every request/response Pydantic model
│   │
│   ├── api/                      # HTTP layer — thin, all logic lives in services/
│   │   ├── ingest.py             # POST /ingest/{file,url,folder,text}
│   │   ├── knowledge_base.py     # Inventory list, delete, reprocess, undo column
│   │   ├── chat.py               # SSE chat stream + session CRUD
│   │   ├── graph.py              # Whole graph, sources-only, one-hop expand
│   │   └── test_graph_hop.py     # Exact-count tests for the hop view
│   │
│   └── services/                 # Everything that isn't HTTP
│       ├── lightrag_engine.py    # THE ENGINE: LightRAG singleton, LLM roles, fallbacks
│       ├── ingestion.py          # Shared ingest orchestration for file/url/text
│       ├── manifest.py           # SQLite document inventory
│       ├── dedup.py              # Whitespace-normalised sha256 content hash
│       │
│       ├── graph_schema.py       # THE ONTOLOGY: labels, rel types, canonicalization,
│       │                         #   validated upsert_node/upsert_edge, flush
│       ├── source_graph.py       # Source Supernode: create, attach, status, remove
│       ├── tabular_graph.py      # Workbook/Worksheet/Column projection into the graph
│       ├── folder_ingest.py      # Directory walk → graph tree + deferred document pass
│       ├── code_intel.py         # Symbols, imports, inheritance and the call graph
│       │
│       ├── qdrant_store.py       # HybridQdrantStorage: dense+sparse, RRF, registration
│       ├── sparse.py             # BM25-style sparse encoder (tokenize → hash → saturate)
│       ├── reranker.py           # Cross-encoder rerank, LightRAG's rerank_model_func
│       ├── rate_limiter.py       # Async TPM token bucket + token estimator
│       │
│       ├── gliner_extract.py     # Local extraction: LightRAG "extract" role replacement
│       ├── multihop.py           # Question decomposition and seed-keyword collection
│       ├── spreadsheet_query.py  # NL → validated DuckDB SQL → exact rows
│       ├── provenance.py         # Evidence chains from what survived into the prompt
│       ├── grounding.py          # Lexical faithfulness check on the finished answer
│       ├── chat_store.py         # SQLite sessions + messages (+ persisted evidence)
│       │
│       ├── parsers/
│       │   ├── pdf.py            # PyMuPDF page-text join
│       │   ├── text.py           # UTF-8 decode with replacement
│       │   ├── youtube.py        # Video id extraction + transcript fetch
│       │   ├── url.py            # ZenRows → trafilatura, with an on-disk scrape cache
│       │   ├── spreadsheet.py    # Excel/CSV → DuckDB; typing, formulas, lineage
│       │   └── test_url.py
│       │
│       └── test_*.py             # 15 test modules covering services
│
├── scripts/                      # Operational tools. None run automatically.
│   ├── backfill_sources.py       # Give pre-supernode documents a Source node
│   ├── reingest_folders.py       # Re-scan every folder source with the current code
│   ├── eval_retrieval.py         # Scored retrieval eval; --save / --compare a baseline
│   ├── eval_questions.yaml       # The eval question set with expected evidence strings
│   ├── graph_duplicates.py       # Report entities split across several nodes (read-only)
│   ├── verify_depth.py           # Filesystem ground truth vs. what landed in the graph
│   ├── benchmark_extraction.py   # Time a book-sized ingest into a throwaway storage dir
│   └── test_graph_duplicates.py
│
└── storage/                      # Runtime data (gitignored) — see §8
```

### Things documented here that live elsewhere in the repo

Per the discovery pass, these don't belong to Frontend, Extension or Agents, so they're
covered here:

- **`backend/scripts/`** — operational and evaluation tooling, covered in §6 and §8.
- **`lightrag_hybrid/`** (repo root) — four orphan files (`core/config.py`,
  `core/models.py`, `storage/vector_store.py`, `utils/text_utils.py`) from an earlier
  design. **Nothing imports them.** The live vector store is
  `backend/app/services/qdrant_store.py`. Listed in §9 as dead code.
- **`PLAN.md`** (repo root) — the v2 storage/retrieval rebuild plan. Historically useful;
  every phase in it is implemented.

---

## 4. How This Fits Into the Bigger Picture

The backend is the only component that touches storage. Both clients speak to it over
HTTP and nothing else.

```
   ┌──────────────────┐        ┌──────────────────────┐
   │  Next.js         │        │  Chrome extension    │
   │  dashboard       │        │  popup               │
   │  (localhost:3000)│        │  (MV3)               │
   └────────┬─────────┘        └──────────┬───────────┘
            │  fetch()                    │  fetch()
            │                             │
            ▼                             ▼
   ┌─────────────────────────────────────────────────────┐
   │  FastAPI  (localhost:8000)                          │
   │    /api/v1/ingest   /api/v1/knowledge-base          │
   │    /api/v1/chat     /api/v1/graph    /health        │
   └───┬─────────────┬──────────────┬────────────┬───────┘
       │             │              │            │
       ▼             ▼              ▼            ▼
   ┌────────┐  ┌──────────┐  ┌───────────┐  ┌─────────┐
   │ Qdrant │  │ NetworkX │  │  DuckDB   │  │ SQLite  │
   │ dense+ │  │ property │  │ worksheet │  │ manifest│
   │ sparse │  │  graph   │  │  tables   │  │ + chat  │
   └────────┘  └──────────┘  └───────────┘  └─────────┘
       (all under backend/storage/, all local files)

   Outbound, only when answering or scraping:
     Groq → OpenRouter → Ollama    ZenRows    YouTube transcripts
```

### Who calls what

| Endpoint | Called by | Documented in |
|---|---|---|
| `POST /api/v1/ingest/file` | Frontend [Dropzone](FRONTEND.md#dropzone-oningested), Extension [PDF path](EXTENSION.md#ingestpdfurlurl) | [§6](#post-apiv1ingestfile) |
| `POST /api/v1/ingest/url` | Frontend [UrlInput](FRONTEND.md#urlinput-oningested), Extension [YouTube path](EXTENSION.md#ingesturlurl) | [§6](#post-apiv1ingesturl) |
| `POST /api/v1/ingest/text` | Frontend [PasteSandbox](FRONTEND.md#pastesandbox-oningested), Extension [clip + selection](EXTENSION.md#ingesttexttext-title-sourcetype) | [§6](#post-apiv1ingesttext) |
| `POST /api/v1/ingest/folder` | Frontend [FolderInput](FRONTEND.md#folderinput-oningested) | [§6](#post-apiv1ingestfolder) |
| `GET /api/v1/knowledge-base` | Frontend Knowledge page, Graph Explorer's source picker | [§6](#get-apiv1knowledge-base) |
| `DELETE /api/v1/knowledge-base/{doc_id}` | Frontend [InventoryTable](FRONTEND.md#inventorytable-documents-isloading-ondelete) | [§6](#delete-apiv1knowledge-basedoc_id) |
| `DELETE …/spreadsheet/{table}/columns/{column}` | Frontend [DataResultTable](FRONTEND.md#dataresulttable-result) undo button | [§6](#delete-apiv1knowledge-basespreadsheettablecolumnscolumn) |
| `POST /api/v1/chat/stream` | Frontend [chat page](FRONTEND.md#5-core-concepts--key-components), Extension [Chat tab](EXTENSION.md#streamchatmessage-history-handlers-signal) | [§6](#post-apiv1chatstream) |
| `POST/GET/PATCH/DELETE /api/v1/chat/sessions…` | Frontend [SessionSidebar](FRONTEND.md#sessionsidebar-props) only | [§6](#session-endpoints) |
| `GET /api/v1/graph` | Frontend [GraphExplorer](FRONTEND.md#graphexplorer) full view | [§6](#get-apiv1graph) |
| `GET /api/v1/graph/sources` | Frontend GraphExplorer hop view (landing state) | [§6](#get-apiv1graphsources) |
| `GET /api/v1/graph/expand` | Frontend GraphExplorer double-click / Expand button | [§6](#get-apiv1graphexpand) |

### Contracts the clients depend on

1. **SSE frame shapes.** `sources`, `evidence`, `table`, `token`, `grounding`, `error`,
   `done`. The frontend's `streamChat` and the extension's `streamChat` both switch on
   `event.type`; an unknown type is silently ignored by both, so adding one is safe.
   Removing or renaming one is not.
2. **Edge ids are the lexically-sorted node pair** (`_edge_id`). This exactly matches what
   LightRAG's own subgraph query produces, which is what lets the frontend dedupe an edge
   that arrives once from `/graph` and again from an expansion.
3. **`degree` on a graph node is the true store degree**, not the drawn degree — that's
   what lets the frontend compute "how many neighbours are still off-canvas" without
   another round trip.
4. **`entity_type` carries the label and `keywords` carries the relationship type.** No
   new fields were added for the property-graph model, which is why the Graph Explorer
   picked up every new node type with zero frontend change.

---

## 5. Core Concepts & Key Components

### The engine (`services/lightrag_engine.py`)

Owns the single `LightRAG` instance for the process. It builds it once, lazily, on the
first `get_rag()` and holds it in a module global. Everything that touches the knowledge
base goes through that instance. Beyond construction, this module is where the **LLM
provider policy** lives: which model serves which job, in what order providers are tried,
how a 429 is handled, and how the extraction token budget is paced. See
[`llm-router`](agents/llm-router.md).

### The ontology (`services/graph_schema.py`)

The single chokepoint every direct graph write passes through. It defines the closed sets
of node labels and relationship types, rejects anything outside them with a `ValueError`
at the boundary rather than writing it, and — just as importantly — owns
`canonical_name()` and `canonical_label()`, the two folds that decide whether two
mentions of a thing become one node or two. It also owns `flush()`, without which direct
writes live only in memory.

### Ingestion orchestration (`services/ingestion.py`)

The shared path all of file, URL and text ingestion funnel into: hash → dedup check →
`rag.ainsert()` → manifest row → Source Supernode. It also contains the recovery
machinery for LightRAG's filename-based deduplication, which can otherwise leave a
filename permanently blocked by a document that failed mid-pipeline.

### The parsers (`services/parsers/`)

Six small modules, each turning one input format into text (or, for spreadsheets, into
DuckDB tables plus a short summary). They are pure and know nothing about the graph. The
URL parser is the largest because it has a two-provider strategy and an on-disk cache.

### The graph writers

Three modules write structure into the graph *deterministically* — no model involved, so
what lands is exactly what was read:

- **`source_graph.py`** — the [Source Supernode](GLOSSARY.md#source-supernode) and its
  `HAS_ROOT` edges.
- **`tabular_graph.py`** — a workbook's Worksheet and Column nodes, `DERIVED_FROM` edges
  from captured formulas, and `HAS_VALUE` bridges to entities documents already named.
- **`code_intel.py`** — classes, functions, methods, imports, inheritance and calls. See
  [`code-intelligence-agent`](agents/code-intelligence-agent.md).

### Retrieval storage (`services/qdrant_store.py`, `sparse.py`, `reranker.py`)

`HybridQdrantStorage` subclasses LightRAG's Qdrant backend and overrides exactly four
methods to add a second named vector per point and fuse both branches with RRF.
`sparse.py` is the encoder for that second vector — a tokenizer and a hash, with no
corpus statistics to maintain. `reranker.py` supplies LightRAG's `rerank_model_func`.

### The query path (`api/chat.py` + `multihop.py`, `spreadsheet_query.py`,
`provenance.py`, `grounding.py`)

`_chat_stream` is the orchestrator: it decides whether a message is a data question, runs
multi-hop planning when the question warrants it, streams the answer, builds the evidence
chain and runs the faithfulness check. Each of the four services it calls is documented
separately — see [`query-orchestrator`](agents/query-orchestrator.md).

### App state (`services/manifest.py`, `services/chat_store.py`)

Two SQLite files, kept deliberately out of the graph and the vector store. They're
separate files so that deleting `storage/kb` to rebuild the knowledge base never takes
the chat history with it.

---

## 6. Function & Component Reference

### API endpoints

---

#### `POST /api/v1/ingest/file`

**What it does:** Accepts one or more uploaded files, parses each by extension, and adds
them to the knowledge base. Per-file failures are reported rather than failing the batch,
so uploading ten files where one is corrupt still indexes nine.

**Input:** `multipart/form-data` with repeated `files` parts.

| Part | Type | Example |
|---|---|---|
| `files` | file (repeatable) | `handbook.pdf`, `q3.xlsx` |

Accepted extensions: `.pdf`, `.md`, `.markdown`, `.txt`, `.xlsx`, `.xlsm`, `.csv`.
Anything else raises `Unsupported file type: .docx` into the `errors` array.

**Output:** `{"results": [IngestResult, …], "errors": [{file_name, error}, …]}` — always
HTTP 200, even when every file failed.

**Example:**
```python
# POST /api/v1/ingest/file  with handbook.pdf
{
  "results": [{
    "doc_id": "doc-4f1c2a9e8b7d4e5f9012345678abcdef",
    "file_name": "handbook.pdf",
    "source_type": "pdf",
    "chunk_count": 37,
    "size_bytes": 148203,
    "date_added": "2026-08-30T09:14:22.918431+00:00",
    "deduped": false
  }],
  "errors": []
}
```

**Notes:** A spreadsheet takes a different path from everything else — its rows go into
DuckDB, only a short schema summary is indexed as text, and its structure is projected
into the graph afterwards. Re-uploading identical content returns the existing record with
`deduped: true` and re-indexes nothing (but a spreadsheet's DuckDB tables *are* rewritten,
because the projection must match them).

---

#### `POST /api/v1/ingest/url`

**What it does:** Ingests a web article or a YouTube video from its URL. YouTube links are
detected by hostname and go to the transcript API; everything else is scraped.

**Input:**

| Param | Type | Example |
|---|---|---|
| `url` | `str` | `"https://en.wikipedia.org/wiki/Knowledge_graph"` |

**Output:** `IngestResult` (HTTP 200) or HTTP 422 with `{"detail": "..."}`.

**Example:**
```python
# POST /api/v1/ingest/url  {"url": "https://youtu.be/dQw4w9WgXcQ"}
{
  "doc_id": "doc-8ab31f7e...", "file_name": "YouTube: dQw4w9WgXcQ",
  "source_type": "youtube", "chunk_count": 4, "size_bytes": 9124,
  "date_added": "2026-08-30T09:20:41.002110+00:00", "deduped": false
}
```

**Notes:** Scraping tries ZenRows first (cheap settings), then retries once with
`js_render` + `premium_proxy`, then falls back to `trafilatura.fetch_url`. Successful
scrapes are cached as `.md` files with YAML frontmatter under `SCRAPED_ARTICLES_DIR`, keyed
by a hash of the normalised URL, so re-ingesting the same URL never costs a second ZenRows
credit. `source_type` is `article_zenrows` or `article` depending on which path won.

---

#### `POST /api/v1/ingest/text`

**What it does:** Ingests a block of raw text.

**Input:**

| Param | Type | Example |
|---|---|---|
| `text` | `str` | `"# Leave policy\n\nEmployees accrue 20 days…"` |
| `title` | `str \| null` | `"Leave policy"` |
| `source_type` | `str \| null` | `"article_clipper"` |

**Output:** `IngestResult`, or HTTP 422.

**Notes:** `source_type` is allowlisted to `{"paste", "article_clipper"}` — anything else
(including a missing value) becomes `"paste"`. This is what lets the Chrome extension mark
a reviewed clip as distinct from a plain paste while not letting a client invent source
types. With no `title`, the first 60 characters of the text become the file name.

---

#### `POST /api/v1/ingest/folder`

**What it does:** Mirrors a directory tree **that is already on the server's disk** into
the graph, and optionally routes the documents inside it through the normal ingestors.

**Input:**

| Param | Type | Example |
|---|---|---|
| `path` | `str` | `"D:/Crag/backend"` |
| `name` | `str \| null` | `"crag-backend"` (defaults to the directory's basename) |
| `index_documents` | `bool` | `true` |

**Output:** `FolderIngestResult` — the manifest record plus tree and symbol counts.

**Example:**
```python
{
  "doc_id": "doc-9c2f...", "file_name": "crag-backend", "name": "crag-backend",
  "path": "D:\\Crag\\backend", "source_type": "folder",
  "folders": 9, "files": 61, "code_files": 58, "images": 0, "videos": 0,
  "classes": 12, "functions": 214, "methods": 63,
  "calls": 486, "external_symbols": 41, "unresolved": 130,
  "documents_indexed": 0, "documents_pending": 2, "status": "processing",
  "max_depth_reached": 3, "total_folders": 9, "total_files": 61,
  "chunk_count": 61, "size_bytes": 412887,
  "date_added": "2026-08-30T09:31:07.442019+00:00", "errors": []
}
```

**Notes:** Path-based, not an upload — a browser `webkitdirectory` upload would re-post
every byte of a repo through multipart to rebuild a tree the server can already see.
`documents_indexed` is **always 0 in this response**: the tree is written and flushed
first, then the document pass runs detached. `status` says whether more is coming, and the
Source node carries the final count. Set `FOLDER_INGEST_ROOT` to confine ingestion to one
subtree — unset, any readable directory is fair game, which is fine on localhost and wrong
the moment the API is bound to anything else. Idempotent on the same path + name.

---

#### `GET /api/v1/knowledge-base`

**What it does:** Lists every ingested document, newest first.

**Input:** none.

**Output:** `list[DocumentOut]`.

**Example:**
```python
[{"doc_id": "doc-4f1c...", "file_name": "handbook.pdf", "source_type": "pdf",
  "chunk_count": 37, "size_bytes": 148203,
  "date_added": "2026-08-30T09:14:22.918431+00:00"}]
```

**Notes:** Reads only the SQLite manifest, so it's fast and never touches the graph.
Folder sources appear here as a single row with `source_type: "folder"`, whose
`chunk_count` is actually the file count.

---

#### `DELETE /api/v1/knowledge-base/{doc_id}`

**What it does:** Removes a document and everything it put into the system.

**Input:** `doc_id` path parameter.

**Output:** `{"doc_id": "...", "deleted": true}`, or HTTP 404.

**Notes:** What "everything" means depends on the source type, and the ordering matters:

- **folder** — `folder_ingest.remove()` drops the supernode and every node whose id starts
  with `<name>/`, in one pass. Documents indexed from inside it keep their own inventory
  rows; they were ingested as documents and are deleted as documents.
- **spreadsheet** — the graph nodes go **before** the DuckDB tables, because the DuckDB
  metadata is what names the nodes to remove.
- **everything else** — Source node, then `rag.adelete_by_doc_id()`.

In all cases `_clear_blocking_rows` then frees the filename of any `dup-` tombstones and
failed runs, because delete-then-reupload is the documented recovery path and it has to
actually work.

---

#### `DELETE /api/v1/knowledge-base/spreadsheet/{table}/columns/{column}`

**What it does:** Undoes a column that was added to a spreadsheet through chat.

**Input:** `table` and `column` path parameters.

**Output:** `{"table": "...", "column": "...", "dropped": true}`, or HTTP 404 when there's
no *computed* column by that name.

**Notes:** Only columns with `added_later = true` in `_node_rels_columns` can be dropped.
The workbook's own data is not deletable through this endpoint at all.

---

#### `POST /api/v1/knowledge-base/reprocess`

**What it does:** Resumes any document left pending, processing or failed by an
interrupted run — a crash, or a dev-server reload mid-ingestion.

**Input:** none. **Output:** `{"status": "ok"}`.

**Notes:** Deliberately manual. Running it at startup would re-trigger costly extraction on
every `--reload` restart.

---

#### `POST /api/v1/chat/stream`

**What it does:** Answers a question against the knowledge base, streaming the response as
Server-Sent Events.

**Input:**

| Param | Type | Example |
|---|---|---|
| `message` | `str` | `"What is the leave policy?"` |
| `history` | `list[{role, content}]` | `[{"role":"user","content":"Hi"}]` |
| `session_id` | `str \| null` | `"3f2a…"` — omit and nothing is persisted |

**Output:** `text/event-stream`. Frames, in the order they can appear:

| Frame | Payload | When |
|---|---|---|
| `table` | `{result: TableResult}` | Spreadsheet answers only |
| `sources` | `{sources: [{reference_id, file_path}]}` | Document answers, before the text |
| `evidence` | `{evidence: [EvidenceSource]}` | Immediately after `sources` |
| `token` | `{text: "…"}` | Repeatedly, as the answer generates |
| `grounding` | `{grounding: {checked, unsupported, supported_ratio}}` | Only when something was unsupported |
| `error` | `{message: "…"}` | Any failure |
| `done` | `{}` | Always last on a successful turn |

**Example** (one SSE frame per line, blank line between):
```
data: {"type": "sources", "sources": [{"reference_id": "1", "file_path": "handbook.pdf"}]}

data: {"type": "evidence", "evidence": [{"reference_id": "1", "file_path": "handbook.pdf", "chain": [{"type": "source", "id": "handbook.pdf", "label": "handbook.pdf", "snippet": ""}, {"type": "chunk", "id": "c1", "label": "handbook.pdf", "snippet": "Employees accrue 20 days of annual leave per year."}]}]}

data: {"type": "token", "text": "Employees accrue "}

data: {"type": "token", "text": "20 days of annual leave per year."}

data: {"type": "done"}
```

**Notes:** Two headers are set deliberately: `X-Accel-Buffering: no` and
`Cache-Control: no-cache`, without which a reverse proxy will buffer the whole stream and
the answer arrives in one lump. An error mid-stream ends the generator; the frontend keeps
whatever tokens already arrived. Persistence is best-effort — the answer has already
reached the user by the time it runs, so a store failure is logged, not raised. See
[`query-orchestrator`](agents/query-orchestrator.md) for the full decision flow.

---

#### Session endpoints

| Route | Does | Returns |
|---|---|---|
| `POST /api/v1/chat/sessions` | Creates a session titled `"New chat"` | `SessionOut` |
| `GET /api/v1/chat/sessions` | Lists sessions, most recently *used* first | `list[SessionOut]` |
| `GET /api/v1/chat/sessions/{id}/messages` | Full transcript with persisted evidence | `list[SessionMessageOut]` |
| `PATCH /api/v1/chat/sessions/{id}` | Renames (title trimmed, clipped to 120 chars) | `SessionOut` |
| `DELETE /api/v1/chat/sessions/{id}` | Deletes; messages cascade | `{"id": "…", "deleted": true}` |

**Notes:** Ordering is by `updated_at`, which `add_message` bumps — so an old thread you
replied to today sorts to the top rather than staying buried at its creation date. Grouping
into Today / Yesterday / Previous 7 Days is deliberately **not** done here: those buckets
are relative to the *viewer's* midnight, and a server that grouped them would bake one
timezone into the response. See [FRONTEND.md `groupByRecency`](FRONTEND.md#groupbyrecencyitems-now).
An empty title on `PATCH` is a 422.

---

#### `GET /api/v1/graph`

**What it does:** Returns the whole graph, or one node's neighbourhood, capped.

**Input:**

| Param | Type | Default | Example |
|---|---|---|---|
| `label` | `str` | `"*"` | `"source:handbook.pdf"` |
| `max_depth` | `int` 1–10 | `3` | `4` |
| `max_nodes` | `int` 1–1000 | `300` | `300` |

**Output:** `{nodes: [GraphNodeOut], edges: [GraphEdgeOut], is_truncated: bool}`.

**Example:**
```python
{
  "nodes": [{"id": "ACME CORP", "entity_type": "organization",
             "description": "ACME Corp is the employer.", "file_path": "handbook.pdf",
             "degree": 4, "source_type": null, "status": null,
             "qualified_name": null, "signature": null,
             "calls_in_count": null, "calls_out_count": null}],
  "edges": [{"id": "ACME CORP-LEAVE POLICY", "source": "ACME CORP",
             "target": "LEAVE POLICY", "keywords": "RELATED_TO",
             "edge_category": "semantic", "description": "ACME defines it.",
             "weight": 1.0, "file_path": "handbook.pdf"}],
  "is_truncated": false
}
```

**Notes:** LightRAG picks the top-`max_nodes` nodes **by degree in the full graph** and
returns the induced subgraph — so edges to cut nodes go with them, and a node whose
neighbours all fell outside the cut arrives with no edges at all. Those render as dots
drifting off the side of a force layout, so this endpoint **drops every node with zero
drawn edges**. That also means `degree` here is the *drawn* degree, not the store degree —
unlike `/sources` and `/expand`.

---

#### `GET /api/v1/graph/sources`

**What it does:** Returns every [Source Supernode](GLOSSARY.md#source-supernode) and
nothing else — no edges. This is the Graph Explorer's landing state.

**Input:** none. **Output:** `GraphOut` with `edges: []` and `is_truncated: false`.

**Example:**
```python
{"nodes": [{"id": "source:handbook.pdf", "entity_type": "source",
            "description": "PDF document 'handbook.pdf', ingested 2026-08-30T…: 37 chunk(s), 148203 bytes. The source everything below it was extracted from.",
            "file_path": "handbook.pdf", "degree": 22, "source_type": "pdf",
            "status": null, …}],
 "edges": [], "is_truncated": false}
```

**Notes:** Exactly `count(Source)` nodes — one per ingestion event. `degree` is the **true
store degree**, which is what lets the frontend draw a "still has neighbours off-canvas"
ring without another round trip.

---

#### `GET /api/v1/graph/expand`

**What it does:** One hop out from a node — its immediate neighbours and the edges to
them, in both directions.

**Input:**

| Param | Type | Example |
|---|---|---|
| `node_id` | `str` | `"proj/src/app.py::main"` |

**Output:** `GraphOut`. HTTP 404 if the node doesn't exist.

**Example:** expanding `proj/src/app.py::main` in a repo where it calls `helper`:
```python
{"nodes": [
   {"id": "proj/src/app.py", "entity_type": "codefile", "degree": 3, …},
   {"id": "proj/src/util.py::helper", "entity_type": "function",
    "calls_in_count": 1, "calls_out_count": 0, "degree": 2, …}],
 "edges": [
   {"id": "proj/src/app.py-proj/src/app.py::main", "source": "proj/src/app.py",
    "target": "proj/src/app.py::main", "keywords": "DEFINES",
    "edge_category": "structural", …},
   {"id": "proj/src/app.py::main-proj/src/util.py::helper",
    "source": "proj/src/app.py::main", "target": "proj/src/util.py::helper",
    "keywords": "CALLS", "edge_category": "behavioral", …}],
 "is_truncated": false}
```

**Notes:** "Both directions" falls out for free — the store is an undirected NetworkX
graph, so every incident edge is returned, and each edge's own
[`rel_from`/`rel_to`](GLOSSARY.md#rel_from--rel_to) says which way it actually points.
That's what makes expanding a Function surface both what it calls *and* what calls it, off
the same query the folder layers use. An edge pointing at a node that was deleted out from
under it is skipped rather than returned with a null.

---

#### `GET /health`

Returns `{"status": "ok"}`. No dependency checks — it answers as soon as the process is
serving.

---

### Ingestion services

---

#### `ingest_text(text, file_name, source_type)`

`app/services/ingestion.py`

**What it does:** The shared ingestion path. Hashes the text, returns early if it's a
duplicate, hands it to LightRAG, writes a manifest row and registers the Source Supernode.

**Input:**

| Param | Type | Example |
|---|---|---|
| `text` | `str` | `"Employees accrue 20 days of annual leave per year."` |
| `file_name` | `str` | `"handbook.pdf"` |
| `source_type` | `str` | `"pdf"` |

**Output:** the manifest record dict plus `deduped: bool`.

**Example:**
```python
record = await ingest_text(text, "handbook.pdf", "pdf")
# => {"doc_id": "doc-4f1c…", "file_name": "handbook.pdf", "source_type": "pdf",
#     "content_hash": "9b74c9…", "chunk_count": 37, "size_bytes": 148203,
#     "date_added": "2026-08-30T09:14:22.918431+00:00", "deduped": False}
```

**Notes:** Raises `IngestionError` on empty text or a failed LightRAG run. The retry loop
(two attempts) exists for a specific LightRAG behaviour: it refuses any insert whose file
basename already has a `doc_status` row **of any status**, and files the attempt under a
synthetic `dup-` id — so the caller's `doc_id` is simply absent from the result. Usually
that means the document really is indexed and only its manifest row is missing, which
`_reconcile_manifest` repairs; otherwise `_clear_blocking_rows` frees the name and the
second attempt proceeds.

---

#### `ingest_file_bytes(data, file_name)`

`app/services/ingestion.py`

**What it does:** Routes one file's bytes to the right parser by extension, then through
`ingest_text`. For spreadsheets it additionally loads DuckDB and projects the workbook
into the graph.

**Input:** `data: bytes`, `file_name: str`.

**Output:** the same record shape as `ingest_text`.

**Notes:** Raises `IngestionError(f"Unsupported file type: {ext}")` for anything outside
`.pdf`, `.md`, `.markdown`, `.txt`, `.xlsx`, `.xlsm`, `.csv`. The spreadsheet projection
runs on a **deduped re-upload too**, deliberately — the workbook was just reloaded into
DuckDB and the graph has to match it. Folder ingestion routes its document leaves through
this exact function rather than growing a second, subtly different copy of it.

---

#### `delete_document(doc_id)`

`app/services/ingestion.py`

**What it does:** Removes a document and its traces from every store, dispatching on
`source_type`.

**Input:** `doc_id: str`. **Output:** `bool` — `False` when there's no such row.

**Notes:** See the notes under
[`DELETE /api/v1/knowledge-base/{doc_id}`](#delete-apiv1knowledge-basedoc_id) — the
ordering constraints are the important part.

---

#### `content_hash(text)` / `normalize_text(text)`

`app/services/dedup.py`

**What it does:** `normalize_text` collapses all whitespace runs to single spaces and
strips; `content_hash` returns the hex sha256 of that.

**Example:**
```python
content_hash("Leave   policy\nis  20 days.")
# => "…"  (identical to content_hash("Leave policy is 20 days."))
```

**Notes:** Normalising first is why a PDF re-exported with different line wrapping still
dedupes against the original.

---

#### Manifest functions

`app/services/manifest.py` — a thin async SQLite layer. All are `async`.

| Function | Returns |
|---|---|
| `init_db()` | Creates `documents` and its `content_hash` index. Called from the app lifespan. |
| `insert_document(doc_id, file_name, source_type, content_hash, chunk_count, size_bytes)` | The inserted row as a dict, with `date_added` set to now (UTC ISO 8601). |
| `find_by_hash(content_hash)` | `dict \| None` — the dedup lookup. |
| `find_by_name(file_name)` | `dict \| None` — used by folder re-ingest and the blocked-filename recovery. |
| `get_document(doc_id)` | `dict \| None` |
| `list_documents()` | `list[dict]`, `date_added DESC` |
| `delete_document(doc_id)` | `bool` |

---

### Parsers

---

#### `extract_pdf_text(data)`

`app/services/parsers/pdf.py`

**What it does:** Extracts text from PDF bytes, joining pages with a blank line.

**Input:** `data: bytes`. **Output:** `str`.

**Example:**
```python
text = extract_pdf_text(open("handbook.pdf", "rb").read())
# => "ACME Employee Handbook\n\nSection 1 — Leave\n\nEmployees accrue 20 days…"
```

**Notes:** Always closes the document, even on error. A scanned PDF with no text layer
returns `""`, which `ingest_text` then rejects as "No content to ingest."

---

#### `extract_article(url)`

`app/services/parsers/url.py`

**What it does:** Turns a web page into clean article Markdown. Serves from the on-disk
cache if it's been seen, otherwise tries ZenRows then a direct fetch, extracts the main
content with trafilatura, and caches the result.

**Input:** `url: str`.

**Output:** `tuple[str, str | None, str]` — `(markdown, title, source_type)`.

**Example:**
```python
text, title, source_type = await extract_article("https://example.com/post")
# => ("# The post's title\n\nBody text…", "The post's title", "article_zenrows")
```

**Notes:** Rule-based end to end — no LLM touches the article text anywhere in this path.
Two cleanups run on the extracted body: everything from the first "Written by" / "Related
articles" / "References" heading to the end is cut (that tail is never article body), and
bare footnote markers like ` [23]` are removed. `deduplicate=True` is deliberately *not*
passed to trafilatura: its "already seen this text" cache is per-process, so in a
long-running server a re-scrape would return an empty body.

---

#### `is_youtube_url(url)` / `extract_video_id(url)` / `extract_youtube_transcript(url)`

`app/services/parsers/url.py`, `app/services/parsers/youtube.py`

**What they do:** Detect a YouTube URL by hostname; pull the 11-character video id out of
it; fetch the English transcript and join its snippets.

**Example:**
```python
is_youtube_url("https://youtu.be/dQw4w9WgXcQ")     # => True
extract_video_id("https://youtu.be/dQw4w9WgXcQ")   # => "dQw4w9WgXcQ"
text, video_id = extract_youtube_transcript("https://youtu.be/dQw4w9WgXcQ")
# => ("We're no strangers to love You know the rules and so do I…", "dQw4w9WgXcQ")
```

**Notes:** `extract_video_id` raises `ValueError` when no id matches; a video with no
English transcript raises `ValueError(f"No transcript available for video: {id}")`. Both
surface as HTTP 422.

---

#### `load_spreadsheet(data, file_name)`

`app/services/parsers/spreadsheet.py`

**What it does:** Loads every worksheet of an Excel/CSV file into its own DuckDB table,
inferring each column's storage type *and* its semantic type, capturing formulas, and
recording which columns each formula reads.

**Input:** `data: bytes`, `file_name: str`.

**Output:** `{"workbook_id": str, "file_name": str, "sheets": [sheet, …]}` where each
sheet is `{"worksheet", "table", "rows", "columns"}` and each column is
`{"name", "header", "data_type", "semantic", "formula", "derived_from"}`.

**Example:**
```python
workbook = load_spreadsheet(xlsx_bytes, "q3.xlsx")
# => {"workbook_id": "a1b2c3d4", "file_name": "q3.xlsx", "sheets": [
#      {"worksheet": "Sales", "table": "workbook_a1b2c3d4__sales", "rows": 120,
#       "columns": [
#         {"name": "customer", "header": "Customer", "data_type": "VARCHAR",
#          "semantic": "categorical", "formula": None, "derived_from": []},
#         {"name": "margin", "header": "Margin", "data_type": "DOUBLE",
#          "semantic": "percentage", "formula": "=C2/B2",
#          "derived_from": ["Cost", "Revenue"]}]}]}
```

**Notes:** The workbook is opened **twice** by openpyxl — `data_only=True` for values and
`data_only=False` for formulas — because a single read can give you one or the other but
not both. Excel keeps dates as serial numbers and percentages as plain floats, so the
cell's **number format** decides `semantic`, not its value. Re-uploading the same file
drops its previous tables first, otherwise the ingest dedup keeps one manifest row while
DuckDB accumulates a second copy of every table. Raises `ValueError` if no sheet holds a
table (needs a header row plus at least one non-empty data row).

---

#### `build_summary(workbook)`

`app/services/parsers/spreadsheet.py`

**What it does:** Produces the short text body that gets indexed for a spreadsheet.

**Example:**
```python
build_summary(workbook)
# => "Spreadsheet workbook: q3.xlsx\nWorksheet 'Sales': 120 rows, 6 columns,
#     queryable as the table workbook_a1b2c3d4__sales in the nodeRels spreadsheet store."
```

**Notes:** Deliberately thin. The workbook's real structure is written node by node into
the graph by `tabular_graph`, each node carrying its own retrieval card. Repeating it as
prose only handed the entity extractor words like `VARCHAR`, `BIGINT` and `categorical` to
turn into graph nodes — which is what the graph used to fill up with. The first line,
`SUMMARY_HEADER`, is how the extractor recognises this text and skips it entirely.

---

#### `get_connection()` / `drop_workbook_tables(file_name)` / `record_columns(...)`

`app/services/parsers/spreadsheet.py`

| Function | What it does |
|---|---|
| `get_connection()` | The one shared DuckDB connection, opened with **`enable_external_access=False`** — a generated `SELECT` can otherwise reach the local filesystem through `read_csv`/`read_text`. Also performs the `_crag_columns` → `_node_rels_columns` rename migration on first open. |
| `drop_workbook_tables(file_name)` | Drops every table belonging to a workbook and its metadata rows, so a deleted workbook can't keep answering queries. |
| `record_columns(con, table, columns, workbook, worksheet, added_later=False)` | Writes (replacing) the `_node_rels_columns` rows for a set of columns. |

**Notes:** CSV type inference runs on a **throwaway in-memory connection** precisely
because the real one has file access switched off — DuckDB's `read_csv_auto` needs it.

---

### The graph layer

---

#### `canonical_name(raw)`

`app/services/graph_schema.py`

**What it does:** Folds a raw entity name so that one real-world thing gets one node.

**Input:** `raw: str | None`. **Output:** `str` (uppercase; `""` for empty input).

**Example:** (these assertions are the module's own self-check)
```python
canonical_name("Microsoft") == canonical_name("microsoft") == "MICROSOFT"
canonical_name("the Microsoft")   # => "MICROSOFT"
canonical_name("Microsoft's")     # => "MICROSOFT"
canonical_name('"Acme Corp".')    # => "ACME CORP"
canonical_name("LIONEL_MESSI") == canonical_name("Lionel-Messi") == "LIONEL MESSI"
canonical_name("Theodore")        # => "THEODORE"  (only a *whole* article is dropped)
canonical_name("Ronaldo Jr") != canonical_name("Ronaldo")
```

**Notes:** This is the project's entity resolution, and it has to run *before* extraction
hands anything to LightRAG, because LightRAG merges entities by exact string match.
Separator folding was worth 35 duplicate groups in a single ingest — a caption
(`LIONEL_MESSI`), a URL slug (`CRISTIANO-RONALDO`) and prose (`Lionel Messi`) are one
person. Uppercase is LightRAG's own convention, and it's the only fold that's
deterministic without a cross-chunk registry.

---

#### `canonical_label(raw)`

`app/services/graph_schema.py`

**What it does:** Folds a raw type name into the graph's one spelling for it.

**Example:**
```python
canonical_label("Organization")   # => "organization"
canonical_label("data source")    # => "datasource"   (what LightRAG itself writes)
canonical_label("CodeFile")       # => "codefile"
canonical_label(None)             # => "UNKNOWN"
```

**Notes:** Lowercase isn't a preference. LightRAG normalizes every extracted entity type
to `.replace(" ", "").lower()` before it reaches the graph, so anything written past it
(the tabular and code projections) has to use the same form or the legend splits into
`person` and `Person`.

---

#### `upsert_node(rag, node_id, label, description, file_path, source_id, keep_existing_label=False, index=True, **properties)`

`app/services/graph_schema.py`

**What it does:** Writes one labelled node and, by default, indexes it in the entity vector
store so retrieval can find it.

**Input:**

| Param | Type | Example |
|---|---|---|
| `node_id` | `str` | `"q3.xlsx:Sales.customer"` |
| `label` | `str` | `"column"` (must be in `NODE_LABELS`) |
| `description` | `str` | the [schema card](GLOSSARY.md#schema-card) |
| `file_path` | `str` | `"q3.xlsx"` |
| `source_id` | `str` | `"doc-9c2f…"` |
| `keep_existing_label` | `bool` | `True` for values a document may already have typed |
| `index` | `bool` | `False` for code symbols |
| `**properties` | any | `table="workbook_a1b2__sales", column="customer"` |

**Output:** `None`.

**Notes:** Raises `ValueError` for a label outside the ontology — that's the whole point of
routing every write through here. `keep_existing_label=True` returns early if the node
exists, so a customer name arriving as a spreadsheet cell can't overwrite the
`Organization` label a contract gave it. `index=False` keeps a node out of the entity
vector store: a repo contributes one node per class, function and method, and indexing all
of them would bury the documents under thousands of symbol cards.

---

#### `upsert_edge(rag, source, target, rel_type, description, file_path, source_id, weight=1.0, **properties)`

`app/services/graph_schema.py`

**What it does:** Writes one typed edge.

**Notes:** Raises `ValueError` for a `rel_type` outside `REL_TYPES`. Always writes
`rel_from` and `rel_to` properties alongside the pair, because the underlying store is an
**undirected** NetworkX graph — it keeps the pair, not the order, and reads it back in
whichever order the nodes were added. For `RELATED_TO` that never mattered; for `CALLS`,
"A calls B" read back as "B calls A" is a wrong answer, not a weaker one.

---

#### `edge_category(rel_type)`

`app/services/graph_schema.py`

**What it does:** Maps a relationship type to how it should be read and drawn.

**Example:**
```python
edge_category("CALLS")              # => "behavioral"
edge_category("CONTAINS_FILE")      # => "structural"
edge_category("RELATED_TO")         # => "semantic"
edge_category("mentions in passing") # => "semantic"  (an older ingest's free text)
edge_category(None)                 # => "semantic"
```

**Notes:** Derived rather than stored, deliberately. Writing it would be a denormalised
copy of a pure function — and one that only *this project's* edges could carry, since
LightRAG writes its own `RELATED_TO` edges directly. Derived, every edge has a category,
old ones included, with no backfill.

---

#### `flush(rag)`

`app/services/graph_schema.py`

**What it does:** Commits direct graph and entity-vector writes to disk.

**Notes:** This is not optional bookkeeping. `upsert_node`/`upsert_edge` only mutate
memory. Inside `rag.ainsert()` LightRAG's own pipeline commits at the end of its batch,
but everything written *outside* that pipeline — the supernode, a workbook's structure, a
folder tree, code symbols — was surviving only if some later document ingest happened to
flush on its way past. Whatever was written after the last such flush was silently lost,
which is why code symbols, written last, never reached disk at all.

---

#### `update_node(rag, node_id, **properties)` / `remove_nodes(rag, node_ids)`

`app/services/graph_schema.py`

`update_node` merges properties into an existing node — not `upsert_node`, which would
restate the label, description and provenance that a status flip has no business
rewriting. A missing node is a no-op: the caller is patching, not creating.
`remove_nodes` drops nodes and their vector records; incident edges go with them.

---

#### `source_graph.register(rag, record)`

`app/services/source_graph.py`

**What it does:** Creates the Source Supernode for a finished ingestion and links every
node that ingestion wrote to the graph underneath it.

**Input:** `rag`, and the manifest `record` dict.

**Example:**
```python
await source_graph.register(rag, record)
# writes node "source:handbook.pdf" (entity_type="source", source_type="pdf")
# plus a HAS_ROOT edge to each of the 22 entity nodes carrying file_path "handbook.pdf"
```

**Notes:** Finds its own output by scanning every node's `file_path`, which is O(graph) per
upload — a dict walk over an in-memory NetworkX graph, fine into the tens of thousands of
nodes. Matching is **exact against the separator-joined list**, not a substring, so
`report.pdf` can't claim the nodes of `old report.pdf`.

---

#### `source_graph.create(rag, record, **counts)` / `attach(...)` / `set_status(...)` / `remove(...)` / `source_node(file_name)`

`app/services/source_graph.py`

| Function | What it does |
|---|---|
| `source_node(file_name)` | Returns `f"source:{file_name}"`. Prefixed so it can never collide with a Workbook node, which is named by the bare file name. |
| `create(rag, record, **counts)` | Writes just the Source node and returns its id. Split out for folder ingestion, which already has its output list in hand and must not pay for a scan to rediscover it. |
| `attach(rag, record, node_ids)` | Adds `HAS_ROOT` edges to the given nodes. Idempotent; skips self-edges. |
| `set_status(rag, file_name, **properties)` | Patches the node's lifecycle fields. Only folder ingestion has a phase long enough for `processing` to be observable — the other six ingestors finish inside one request. |
| `remove(rag, file_name)` | Drops the Source node. Its `HAS_ROOT` edges go with it; the nodes it pointed at belong to the document and are deleted by LightRAG. |

---

#### `tabular_graph.project(rag, workbook, doc_id)`

`app/services/tabular_graph.py`

**What it does:** Writes a workbook's whole structure into the property graph. Idempotent.

**Input:** `rag`, the `workbook` dict from `load_spreadsheet`, and the `doc_id`.

**Output:** `None`. Writes:
```
(:Workbook) -[:HAS_SHEET]->  (:Worksheet) -[:HAS_COLUMN]-> (:Column)
(:Column)   -[:DERIVED_FROM]-> (:Column)        from the captured formula
(:Column)   -[:HAS_VALUE]->  (existing entity)  for categorical/text columns only
```

**Notes:** Nothing here is extracted — every node and edge is derived from what the parser
read, so the spreadsheet's shape in the graph is exactly right rather than as right as a
model happened to be. **Rows never enter the graph.** `HAS_VALUE` only ever links to a
node that *already exists*, so a spreadsheet never invents entities; distinct values are
read `SPREADSHEET_MAX_GRAPH_VALUES` at a time, and each is folded through
`canonical_name()` because that's how the documents stored it. Ends with `gs.flush()`.

---

#### `tabular_graph.remove(rag, file_name)` and the node-id helpers

| Function | Returns |
|---|---|
| `workbook_node(file_name)` | `"q3.xlsx"` |
| `worksheet_node(file_name, worksheet)` | `"q3.xlsx:Sales"` |
| `column_node(file_name, worksheet, column)` | `"q3.xlsx:Sales.customer"` |
| `remove(rag, file_name)` | Drops the workbook's structural nodes. **Must run before the DuckDB tables are dropped** — their metadata is what says which nodes exist. |

**Notes:** Cell-value nodes are left alone by `remove`: they're document entities the
spreadsheet merely pointed at, and deleting a workbook doesn't unsay what a document said.
Their `HAS_VALUE` edges go with the column nodes.

---

### Retrieval storage

---

#### `HybridQdrantStorage` and `qdrant_store.register()`

`app/services/qdrant_store.py`

**What it is:** A subclass of LightRAG's `QdrantVectorDBStorage` that gives every point two
named vectors and answers a query with one RRF-fused call. Four methods are overridden —
`initialize`, `_flush_pending_vector_ops`, `query`, `get_vectors_by_ids`. Buffering,
deletes, workspace isolation and read-your-writes are inherited untouched.

**`register()`** makes LightRAG able to resolve the class by name and returns that name:

```python
_rag = LightRAG(vector_storage=qdrant_store.register(), ...)
# register() returns "HybridQdrantStorage" after adding it to LightRAG's three registries
```

**Notes on the overrides:**

- `initialize()` creates the collection with a 384-d cosine `dense` vector and an
  IDF-modified `sparse` vector, and checks for a
  [stale collection](GLOSSARY.md#stale-collection) *before and after*. On Windows,
  embedded Qdrant's `delete_collection` calls `shutil.rmtree(..., ignore_errors=True)`
  while still holding the collection's SQLite file open — the unlink fails, the error is
  swallowed, and the next `create_collection` reloads the **old** points. `_drop_collection`
  closes the handle first, which is what makes the delete real.
- `query()` runs two `Prefetch` branches at `top_k × HYBRID_PREFETCH_MULTIPLIER` depth and
  fuses with `Fusion.RRF`. The cosine threshold stays on the **dense branch only** — it's a
  cosine number and means nothing against a BM25 score, and a rare exact term should be
  able to rank on the sparse branch alone. The `distance` field it returns is an RRF score,
  not a cosine one; nothing in LightRAG reads it, it's kept for parity.
- `get_vectors_by_ids()` unwraps the named-vector dict, since callers want the dense one.

---

#### `sparse.encode_document(text)` / `sparse.encode_query(text)`

`app/services/sparse.py`

**What they do:** Turn text into `(indices, values)` for a Qdrant sparse vector — term ids
and weights.

**Example:** (from the module's own self-check)
```python
i, v = encode_document("Invoice INV-2024-017 for Acme Corp: the total is 4200")
# "acme" is present; "the" is not; "inv-2024-017" stayed one term;
# indices are ascending; every value is strictly between 0 and 1.

encode_document("acme " * 50)   # "acme"'s weight is still < 1.0 — tf saturates
encode_query("What is the Acme total?")  # values are all exactly 1.0
encode_document("")             # => ([], [])
```

**Notes:** Term ids are the first 4 bytes of a `blake2b` digest — collisions are ~1 in 4
billion per pair, and a collision costs a little precision, never correctness. Only `k1`
saturation is applied; BM25's length normalisation needs the corpus average document
length, which would have to be maintained alongside the index. Query terms all weigh the
same because Qdrant's IDF modifier is what makes the rare ones count. The stopword list is
deliberately short — with IDF weighting a common word is already worth almost nothing, so
an aggressive list only risks dropping a real query term.

---

#### `rerank(query, documents, top_n=None, **kwargs)`

`app/services/reranker.py`

**What it does:** Re-scores candidate chunks against the query with a cross-encoder and
returns them highest-first. This is LightRAG's `rerank_model_func` contract.

**Input:** `query: str`, `documents: list[str]`, `top_n: int | None`.

**Output:** `list[{"index": int, "relevance_score": float}]` over the **input** list.

**Example:**
```python
await rerank("annual leave", ["Leave is 20 days.", "Parking is free."], top_n=1)
# => [{"index": 0, "relevance_score": 7.42}]
```

**Notes:** Runs the model in a thread (`asyncio.to_thread`) so it doesn't block the event
loop. `warmup()` loads it at boot so the first chat message doesn't pay for it. An empty
`documents` list returns `[]` without touching the model.

---

#### `TokenBucket(tokens_per_minute)` — `acquire(tokens)`, `resize(tokens_per_minute)`

`app/services/rate_limiter.py`

**What it does:** An async token bucket that paces LLM calls to a tokens-per-minute
budget. `acquire` waits until the tokens are available, then spends them.

**Example:**
```python
budget = TokenBucket(12000)
await budget.acquire(estimate_tokens(prompt, system_prompt))  # blocks if needed
budget.resize(6000)   # Groq's 429 body said the real ceiling is 6000
```

**Notes:** A request larger than the whole bucket drains it rather than waiting forever.
`resize` exists because Groq's own 429 body reports the model's real TPM limit, which beats
keeping a config value in sync by hand — the limit differs per model and changes with your
tier. `ponytail:` one lock serializes waiters, so callers go FIFO, not fair-share — fine at
`MAX_ASYNC_LLM=2`.

---

#### `estimate_tokens(prompt, system_prompt, output_allowance=1000)`

`app/services/rate_limiter.py`

Returns `(len(prompt) + len(system_prompt or "")) // 4 + output_allowance` — roughly 4
characters per token, plus room for the response, because Groq's TPM counts both.

---

### The engine

---

#### `get_rag()` / `shutdown_rag()`

`app/services/lightrag_engine.py`

**What they do:** Build (once) and tear down the process-wide `LightRAG` instance.

**Example:**
```python
rag = await get_rag()          # builds on first call, returns the same object after
await shutdown_rag()           # finalize storages, then close the Qdrant client
```

**Notes:** `get_rag()` checks the Ollama fallback and warns about an impossible context
budget *before* constructing, so both problems surface at boot rather than mid-request.
`shutdown_rag()` must close the Qdrant client explicitly: embedded Qdrant holds an
exclusive lock on its directory until closed, so a reload that skips this can't reopen the
store.

---

#### `llm_model_func(...)` / `query_llm_func(...)` / `check_ollama_fallback()`

`app/services/lightrag_engine.py` — see [`llm-router`](agents/llm-router.md) for the full
treatment.

| Function | Role | Chain |
|---|---|---|
| `llm_model_func` | Graph building + keyword extraction | Groq (`GROQ_EXTRACT_MODEL`, TPM-paced) → OpenRouter → Ollama |
| `query_llm_func` | Answering | Groq (`GROQ_MODEL`) → OpenRouter → Ollama |
| `check_ollama_fallback()` | Boot check | Returns `bool`; sets the module flag that makes `_ollama_complete` raise a useful message instead of a 404 mid-extraction |

---

### Query-path services

Each of these has its own document; this is the calling contract.

| Function | Module | Signature → Output |
|---|---|---|
| `multihop.gather(rag, question, param_factory)` | `multihop.py` | `→ list[str]` seed keywords, `[]` for single-hop questions (no LLM call). See [`multi-hop-planner`](agents/multi-hop-planner.md). |
| `multihop.is_multi_hop(question)` | `multihop.py` | `→ bool`. `"Compare the Q3 and Q4 revenue figures."` → `True`; `"What is the leave policy?"` → `False`. |
| `spreadsheet_query.relevant_tables(rag, question)` | `spreadsheet_query.py` | `→ list[str]` table names, `[]` when the question isn't about data. See [`spreadsheet-sql-agent`](agents/spreadsheet-sql-agent.md). |
| `spreadsheet_query.answer(question, tables)` | `spreadsheet_query.py` | `→ dict \| None`. `None` means "not a spreadsheet question after all". Raises `SpreadsheetError` after 3 failed attempts. |
| `provenance.build_evidence(data)` | `provenance.py` | `→ list[EvidenceSource]`. See [`grounding-verifier`](agents/grounding-verifier.md). |
| `grounding.check(answer, evidence)` | `grounding.py` | `→ {"checked": int, "unsupported": [str], "supported_ratio": float}` |

**`grounding.check` example** (from its own tests):
```python
evidence = [{"file_path": "handbook.pdf", "chain": [
    {"type": "chunk", "label": "handbook.pdf",
     "snippet": "Employees accrue 20 days of annual leave per year."}]}]

check("Employees accrue 20 days of annual leave per year.", evidence)
# => {"checked": 1, "unsupported": [], "supported_ratio": 1.0}

check("Employees accrue 20 days of annual leave per year. "
      "The chief executive resigned in Lisbon following a currency scandal.", evidence)
# => {"checked": 2,
#     "unsupported": ["The chief executive resigned in Lisbon following a currency scandal."],
#     "supported_ratio": 0.5}

check("The knowledge base has no information on this.", [])
# => {"checked": 0, "unsupported": [], "supported_ratio": 1.0}   # a refusal is not a claim
```

---

### Chat store

`app/services/chat_store.py` — all async except `fallback_title`.

| Function | Returns |
|---|---|
| `init_db()` | Creates `chat_sessions`, `chat_messages` and their indexes. |
| `create_session(title="New chat")` | The new session dict with a UUID id. |
| `list_sessions()` | `list[dict]`, `updated_at DESC`. |
| `get_session(session_id)` | `dict \| None`. |
| `rename_session(session_id, title)` | `bool`. Bumps `updated_at`. |
| `delete_session(session_id)` | `bool`. Messages cascade. |
| `add_message(session_id, role, content, evidence=None)` | The message dict. Bumps the session's `updated_at`. |
| `list_messages(session_id)` | `list[dict]` with `evidence` decoded back to a list. |
| `fallback_title(message)` | First line of the question, clipped to 50 chars with `...`. |
| `generate_title(message)` | A ≤6-word title from the cheap extraction model, degrading to `fallback_title` on any failure or an over-long reply. |

**Notes:** `_connect()` must stay a context manager rather than returning a connection: an
aiosqlite `Connection` is a `Thread` that starts when awaited, so `async with await
connect()` would start that thread twice. `PRAGMA foreign_keys = ON` is set per connection
because SQLite defaults it off and the cascade needs it. A malformed evidence blob is
discarded with a warning rather than taking the whole conversation down — the message still
reads fine without its provenance panel.

---

### Operational scripts

All are run as modules from `backend/`, and **none run automatically**.

| Script | Command | What it does |
|---|---|---|
| `backfill_sources` | `python -m scripts.backfill_sources [--apply]` | Registers a Source node for inventory rows ingested before supernodes existed. Idempotent; folder sources are skipped (theirs carries tree counts a `file_path` scan would replace). |
| `reingest_folders` | `python -m scripts.reingest_folders [--apply]` | Re-scans every folder source so old ingests get the current graph. The repair path for folders whose code symbols never reached disk. |
| `eval_retrieval` | `python -m scripts.eval_retrieval [--save f.json] [--compare f.json]` | Scores **retrieval**, not prose: each question in `eval_questions.yaml` declares substrings that must appear in the evidence chain, so the number doesn't move when the answering model changes its wording. Measures retrieval, grounding, and whether a multi-hop question actually decomposed. |
| `graph_duplicates` | `python -m scripts.graph_duplicates [--type person]` | Reports entities split across several nodes, separating `RESOLVED` (folds to one name today — a re-ingest merges them) from `AMBIGUOUS` (a short name that's a prefix/suffix of longer ones). **Read-only by design**: the graph is derived data, so the fix is a re-ingest, not surgery on nodes. |
| `verify_depth` | `python -m scripts.verify_depth <folder> [name]` / `--check <name>` | Walks the tree with the ingest's own ignore rules and compares folder count, file count and depth against the graph, plus a zero-gap chain check. |
| `benchmark_extraction` | `python scripts/benchmark_extraction.py [path] [--words 90000]` | Times a book-sized ingest into a throwaway storage dir. Synthesizes *varied* prose when given no path — identical chunks would hit LightRAG's extraction cache and report a fictional number. |

---

## 7. End-to-End Walkthroughs

### 7.1 A user uploads `handbook.pdf`

Every function that runs, in order:

1. **`POST /api/v1/ingest/file`** → `api/ingest.py::ingest_files` reads the upload's bytes.
2. `services/ingestion.py::ingest_file_bytes(data, "handbook.pdf")` — extension is `.pdf`.
3. `parsers/pdf.py::extract_pdf_text(data)` → the page text, joined with blank lines.
   `source_type` is set to `"pdf"`.
4. `services/ingestion.py::ingest_text(text, "handbook.pdf", "pdf")`:
   1. `dedup.content_hash(text)` → whitespace-normalised sha256.
   2. `manifest.find_by_hash(hash)` → `None`, so this is new.
   3. `lightrag_engine.get_rag()` → builds the engine on the first ingest of the process:
      `check_ollama_fallback()`, `_warn_if_context_budget_exceeds_tpm()`, then `LightRAG(...)`
      with `qdrant_store.register()` as the vector storage and `gliner_extract` bound to
      the `extract` role.
   4. `rag.ainsert(input=[text], ids=["doc-…"], file_paths=["handbook.pdf"])`. Inside
      LightRAG: chunk to 1,024 tokens with 100 overlap → embed each chunk via
      `lightrag_engine._embed` → for each chunk, call the `extract` role, which is
      `gliner_extract.gliner_extract`.
   5. Inside each extraction: `_INPUT_TEXT_RE` pulls the chunk text back out of LightRAG's
      prompt → `_mask_urls` blanks URLs (length-preserving, so offsets stay valid) →
      `_split_sentences` → `_windows` groups sentences under ~900 chars →
      `_predict` runs GLiNER over the batch → each hit's text goes through
      `graph_schema.canonical_name` and its label through `canonical_label` →
      same-sentence co-occurrence becomes `RELATED_TO` relationships → JSON back to
      LightRAG, which parses, merges and upserts it exactly as if an LLM had written it.
   6. `rag.aget_docs_by_track_id(track_id)` confirms the document was accepted.
   7. `manifest.insert_document(...)` writes the inventory row.
   8. `_reconcile_manifest(rag, skip_doc_id)` gives a manifest row to any *other* document
      LightRAG opportunistically finished as a side effect of this one.
   9. `_register_source(rag, record)` → `source_graph.register(rag, record)`:
      `graph_schema.upsert_node("source:handbook.pdf", label="source", …)`, then a scan for
      every node whose `file_path` list contains `handbook.pdf`, then one
      `upsert_edge(..., HAS_ROOT)` per node, then `graph_schema.flush(rag)`.
5. **Vector writes land** when LightRAG flushes: `HybridQdrantStorage._flush_pending_vector_ops`
   embeds the buffered chunks in batches, builds each point with **both** a `dense` vector
   (from the embedding) and a `sparse` one (`sparse.encode_document(content)`), and upserts.
6. The endpoint returns `{"results": [record], "errors": []}`.
7. The frontend's `Dropzone` marks the upload row done and calls `refresh()` →
   `GET /api/v1/knowledge-base`.

---

### 7.2 A user asks "Compare the Q3 and Q4 revenue figures."

1. **`POST /api/v1/chat/stream`** → `api/chat.py::chat_stream` wraps `_chat_stream` in a
   `StreamingResponse` with `X-Accel-Buffering: no`.
2. `lightrag_engine.get_rag()`.
3. **Data-question check first.** `spreadsheet_query.relevant_tables(rag, message)` queries
   the entity vector store with the message and keeps hits whose graph node has a
   `workbook`/`worksheet`/`column` label. Suppose a `Revenue` column card matches →
   `["workbook_a1b2c3d4__sales"]`.
4. `_spreadsheet_answer(message, tables)` → `spreadsheet_query.answer(...)`:
   1. `schema_context(tables)` renders only those tables' DDL.
   2. `_generate(question, schema, None)` → `lightrag_engine.query_llm_func` with the
      SQL-writer system prompt → `SELECT quarter, SUM(revenue) … GROUP BY quarter`.
   3. `run_select(sql)` → `_validate_select(sql)`: `extract_statements` (exactly one
      statement, and it must be `SELECT`), reject any mention of `_node_rels_columns`,
      then `DESCRIBE <sql>` to bind every name against the real catalog **without running
      it** — so a hallucinated column fails here rather than halfway through a scan.
   4. Execute with `LIMIT 501`, keep 500, set `truncated`.
   5. On a `SpreadsheetError`, the exact failure is handed back to the model and it retries,
      up to 3 attempts total.
5. `_describe_table_result(result)` → `"2 rows."`. Frames emitted: `table`, then `token`,
   then `_persist(...)`, then `done`. **The document RAG path never runs**, and no
   grounding check is needed — DuckDB rows are exact, not retrieved prose.

**If step 3 had returned `[]`** (no spreadsheets, or the question isn't about them):

6. `build_query_param(history)` builds the `QueryParam`: `mode="mix"`, streaming,
   `include_references=True`, reranking on, and the four token budgets all derived from
   `QUERY_CONTEXT_TOKEN_BUDGET`.
7. `multihop.gather(rag, message, build_retrieval_param)`:
   - `is_multi_hop("Compare the Q3 and Q4 revenue figures.")` → `True` (the `\bcompare[ds]?\b`
     pattern).
   - `decompose(...)` → one `llm_model_func` call → `["What were the Q3 revenue figures?",
     "What were the Q4 revenue figures?"]`.
   - Each sub-question runs `rag.aquery_data(sub, param=param_factory())` — **retrieval only,
     no generation** — with a fresh `QueryParam` each time, because LightRAG writes resolved
     keywords back onto it and a shared instance would leak hop 1's keywords into hop 2.
   - `seed_keywords(datas)` counts entity names across the hops and returns the top 12,
     most-frequent first. These become `param.ll_keywords`, so LightRAG skips its own
     keyword extraction and searches for hop 1's discoveries by name.
8. `rag.aquery_llm(message, param=param)`. Inside: keyword extraction (skipped — seeded) →
   `HybridQdrantStorage.query` on chunks, entities and relationships (dense + sparse,
   RRF-fused) → `reranker.rerank` re-orders the chunk candidates → context assembled under
   the token budget → `query_llm_func` generates.
9. `provenance.build_evidence(data)` groups what **survived truncation** by
   `reference_id` into `source → chunk → entity → relationship` chains, capped at 6 per
   kind, dropping sources that contributed nothing.
10. Frames: `sources`, `evidence`, then `token` repeatedly as the iterator yields.
11. `grounding.check(answer, evidence)` on the finished text; a `grounding` frame is sent
    **only if** something was unsupported.
12. `_persist(session_id, message, answer, evidence)` — writes both turns, and generates
    the session title if this was the first exchange.
13. `done`.

---

### 7.3 A user scans the folder `D:\Crag\backend`

1. **`POST /api/v1/ingest/folder`** → `api/ingest.py::ingest_folder` →
   `folder_ingest.ingest_folder(path, name=None, index_documents=True)`.
2. `_resolve_root(path)` — absolute, must be a directory, and must sit inside
   `FOLDER_INGEST_ROOT` if that's set.
3. `scan(root, name)` — pure, no writes:
   - `load_ignores(root)` = `DEFAULT_IGNORES` + the root's `.kbignore`.
   - `os.walk`, pruning `dirnames` **in place** so it never descends into an ignored tree.
   - Each file is classified by extension: `CODE_LANGUAGES` → `codefile`,
     `IMAGE_EXTENSIONS` → `image`, `VIDEO_EXTENSIONS` → `video` (audio rides with video),
     else `file`.
   - `_log_levels` prints one line per depth, so a future regression is readable off the log.
4. `manifest.find_by_name(name)` — on a re-ingest, the existing `doc_id` is reused and the
   old row deleted, so the graph tracks the folder as it is now.
5. `manifest.insert_document(...)` with `source_type="folder"` and a **tree-shape hash**,
   not a content hash (a folder has no single body to hash).
6. `source_graph.create(rag, record, **counts)` writes the supernode with
   `status="processing"` and the tree counts on it.
7. Every folder → `gs.upsert_node(..., FOLDER)` + `gs.upsert_edge(parent, child,
   CONTAINS_FOLDER)`. Then `source_graph.attach(rag, record, [name])` — **one** edge from
   the supernode to the tree's root; everything else is reachable through it.
8. Every file → a node with its properties (`loc` for code, `width`/`height` for images
   when Pillow is installed) + a `CONTAINS_FILE` edge. Code file contents are read **once**
   here and kept in `sources`, because `loc` is needed now and the text is needed after
   every file node exists. Document leaves are pushed onto `pending`, **not** ingested yet.
9. **Symbols last**, because a call in the first file can land in the last:
   - `code_intel.extract(text, language)` per file → `FileSymbols`.
   - `code_intel.project(rag, parsed, file_meta, name, doc_id)`:
     `build_index` → `plan_calls` (resolve every call site before anything is written, so
     `calls_in_count` can be known) → write `ExternalSymbol` nodes → write every symbol node
     with its counts → `DEFINES`/`DEFINES_METHOD`/`INHERITS`/`IMPLEMENTS`/`IMPORTS` edges →
     `CALLS` edges last.
10. **`gs.flush(rag)` — the durability point.** The complete tree and every symbol are on
    disk before any slow work starts. If there's nothing pending, `set_status(..., completed)`
    runs **before** the flush, not after — a status written past the commit point lives in
    memory only and the node on disk stays `processing` forever.
11. The endpoint **returns now**, with `documents_indexed: 0`, `documents_pending: 2`,
    `status: "processing"`.
12. `_spawn(_index_documents(...))` runs detached: each document leaf goes through
    `ingestion.ingest_file_bytes` — the same path a manual upload takes — and its file node
    gets a `doc_id` property. One unreadable file is recorded in `errors` and the rest
    continue. The `finally` block always writes the final status and flushes, so even a
    cancellation records the truth rather than leaving the node stuck on `processing`.

---

## 8. Configuration & Setup

### Running it

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
# source .venv/bin/activate                          # macOS / Linux

pip install -r requirements.txt
pip install -r requirements-codeintel.txt            # optional: JS/TS symbols, image dims

cp .env.example .env                                 # then fill in GROQ_API_KEY
uvicorn app.main:app --reload --port 8000
```

First boot downloads three models to the HuggingFace cache (~500 MB total):
`all-MiniLM-L6-v2`, `gliner_small-v2.1`, `ms-marco-MiniLM-L-6-v2`. The lifespan handler
prefetches the latter two so the first upload and the first message don't pay for it — and
a download failure there is logged, not fatal, so being offline doesn't stop the server
booting.

### Tests

```bash
cd backend
pytest                                    # the test_*.py modules
python -m app.services.graph_schema       # ontology + canonicalization self-check
python -m app.services.sparse             # sparse encoder self-check
python -m app.services.folder_ingest      # classification + ignore-rule self-check
python -m app.services.code_intel         # symbol extraction + call resolution self-check
```

The `__main__` self-checks are assert-based and need no fixtures; they print `ok`.

### Environment variables

`backend/.env.example` documents every one inline and is the authoritative reference.
Grouped summary:

| Group | Variables | Notes |
|---|---|---|
| **Answering** | `GROQ_API_KEY`, `GROQ_MODEL`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | `GROQ_MODEL` is a **TPM decision, not a speed one**: a chat call sends the whole context in one request, so the model's per-minute ceiling must exceed `QUERY_CONTEXT_TOKEN_BUDGET` plus room for the answer. |
| **Extraction** | `EXTRACTION_BACKEND`, `GLINER_MODEL`, `GLINER_THRESHOLD`, `GLINER_BATCH_SIZE`, `GLINER_USE_GPU`, `GLINER_MAX_ASYNC`, `ENTITY_LABELS` | `ENTITY_LABELS` **is the ontology** — editing it is how you retarget extraction at new content. Keep it to 8–10 deliberate types. |
| **Rate limiting** | `GROQ_TPM_LIMIT`, `GROQ_RATE_LIMIT_RETRIES`, `GROQ_RATE_LIMIT_WAIT_SECONDS`, `GROQ_DAILY_QUOTA_COOLDOWN_SECONDS`, `MAX_ASYNC_LLM`, `MAX_GLEANING` | A wrong `GROQ_TPM_LIMIT` self-corrects on the first 429 and logs the value to put in the file. |
| **Retrieval** | `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `CHUNK_TOKEN_SIZE`, `CHUNK_OVERLAP_TOKEN_SIZE`, `QUERY_CONTEXT_TOKEN_BUDGET`, `RERANK_ENABLED`, `RERANK_MODEL`, `RERANK_TOP_N`, `HYBRID_PREFETCH_MULTIPLIER` | Changing `EMBEDDING_DIM` invalidates every stored vector — `HybridQdrantStorage` detects this at boot and recreates the collections. |
| **Storage** | `STORAGE_DIR`, `QDRANT_URL`, `QDRANT_API_KEY`, `SCRAPED_ARTICLES_DIR` | Blank `QDRANT_URL` = embedded, no server, no Docker. |
| **Scraping** | `ZENROWS_API_KEY`, `ZENROWS_TIMEOUT_SECONDS`, `ZENROWS_USE_JS_RENDER_DEFAULT`, `ZENROWS_USE_PREMIUM_PROXY_DEFAULT` | Blank key = trafilatura-only. A zero-code rollback. |
| **Spreadsheets** | `SPREADSHEET_MAX_ROWS`, `SPREADSHEET_MAX_GRAPH_VALUES` | |
| **Folders** | `FOLDER_INGEST_ROOT`, `FOLDER_MAX_FILE_MB`, `FOLDER_MAX_DEPTH` | `FOLDER_MAX_DEPTH` defaults to 1000 on purpose: a small default here is exactly the bug that looks like "the walk stopped early". |
| **Other** | `CORS_ORIGINS`, `OPIK_API_KEY`, `OPIK_WORKSPACE`, `OPIK_PROJECT_NAME` | |

### Storage layout

| Path | Contents | Safe to delete? |
|---|---|---|
| `storage/kb/` | LightRAG KV stores, doc-status store, NetworkX graph | Yes — rebuilds on re-ingest. Chat history survives. |
| `storage/qdrant/` | Embedded Qdrant collections | Yes, but **delete `storage/kb/` with it** or the graph will reference vectors that no longer exist. |
| `storage/spreadsheets.duckdb` | Worksheet tables + `_node_rels_columns` | Yes; spreadsheets need re-uploading. |
| `storage/manifest.sqlite3` | Document inventory | Losing it orphans indexed documents (they stay searchable but vanish from the UI). |
| `storage/chat.sqlite3` | Sessions and messages | Yes — independent of everything else. |
| `storage/scraped_articles/` | Cached URL scrapes | Yes; costs ZenRows credits to rebuild. |

---

## 9. Known Limitations & Open TODOs

### Acknowledged ceilings (each marked `ponytail:` in the source)

| Where | Limitation | The upgrade path |
|---|---|---|
| `graph_schema.py` | The NetworkX store keeps **one edge per node pair**, so two different relationship types between the same two nodes collide. Never happens in the tabular projection (distinct pairs), and it's what LightRAG already does for entities. | A multigraph store — Kùzu, Memgraph. |
| `gliner_extract.py` | **Every prose edge is `RELATED_TO`.** There's no relation model — relationships are same-sentence co-occurrence. The sentence is the edge's description, so no evidence is lost, but the edge carries no verb. | GLiREL, or a Tier-2 LLM pass. |
| `gliner_extract.py` | Entity descriptions are merged by de-duplicating and joining verbatim source sentences, not abstractively summarised. | Route `summarize_descriptions` back to `llm_model_func`. |
| `grounding.py` | Lexical overlap, so a **correct paraphrase** that shares few words with its evidence can be flagged as unsupported. | An entailment model or an LLM judge. |
| `provenance.py` | The chain is ordered **by kind, not by a real derivation trace** — LightRAG doesn't record which entity produced which claim. It shows what the answer rests on; it is not a proof tree. | Would need derivation tracking inside LightRAG. |
| `sparse.py` | **No stemming** — "invoices" and "invoice" are different terms. | `py-rust-stemmers` in `tokenize()`. |
| `source_graph.py` | `register()` finds its own output by scanning **every** node's `file_path` — O(graph) per upload. | Have the extractor report the names it wrote. |
| `tabular_graph.py` | `HAS_VALUE` linking is **order-dependent**: a contract ingested *after* its spreadsheet won't retro-link. | Re-run `project()` on the workbook, or re-upload it. |
| `code_intel.py` | Call resolution is **by name, no type inference**, so `self.save()` and `db.save()` are indistinguishable and an ambiguous name is left unresolved. Only module-level definitions and class methods become nodes. | A Hybrid-LSP layer. |
| `code_intel.py` | An import is the only signal for an `ExternalSymbol`, so a library call reached through an un-imported alias stays in `calls_unresolved`. | Same. |
| `qdrant_store.py` | `_flush_pending_vector_ops` batches by **point count only**; the parent also estimates payload bytes. ~0.5 MB per batch at a 1024-token chunk, against a 16 MB ceiling. | Restore the byte estimate if payloads grow. |
| `rate_limiter.py` | One lock serializes waiters, so callers go **FIFO, not fair-share**. | Per-role buckets. |
| `parsers/spreadsheet.py` | One shared DuckDB connection, serialized by the event loop. A whole CSV is materialized in Python on the way across. | `con.cursor()` per request; `ATTACH` the temp database. |
| `parsers/url.py` | A new `httpx.AsyncClient` per ZenRows call. | An app-lifespan singleton. |
| `folder_ingest.py` | **No per-file checksum**, so a re-scan re-reads and rewrites every file. | `sha256` per file to skip unchanged ones. |
| `api/chat.py` | A spreadsheet answer **does not also run document retrieval** — the two paths are exclusive. | Run both when cross-referencing needs the prose alongside the rows. |

### Structural gaps

- **No authentication, no multi-tenancy.** Every endpoint is open. `FOLDER_INGEST_ROOT`
  exists precisely because folder ingestion reads the *server's* disk, which is fine on
  localhost and wrong the moment this is bound to anything else.
- **`/ingest/folder` is path-based only.** There's no upload variant, by design.
- **No progress events during ingestion.** A single upload is one request with no
  server-side progress, which is why the frontend's `UploadStatusList` shows *timed*
  stages rather than measured ones.
- **Re-ingestion is manual.** Nothing watches a folder for changes.
- **Failed extractions aren't retried automatically** — `POST /knowledge-base/reprocess`
  exists and is deliberately not wired to startup.

### Dead code

- **`lightrag_hybrid/`** at the repository root (4 files: `core/config.py`,
  `core/models.py`, `storage/vector_store.py`, `utils/text_utils.py`) is from an earlier
  design and is imported by nothing. The live vector store is
  `backend/app/services/qdrant_store.py`. It should be deleted; it is not, because this
  pass does not modify application code.
- `backend/uvicorn*.log` (4 files) are committed run logs. `.gitignore` covers `*.log`, so
  they predate that rule.

### Naming drift

The project renamed itself from **Crag** to **nodeRels**. Traces remain: the repository
directory is `D:\Crag`, the DuckDB metadata table carries a `_crag_columns` →
`_node_rels_columns` rename migration in `get_connection()`, and the FastAPI app title is
`"nodeRels GraphRAG Knowledge Base API"`. None of this is broken; it's just confusing on a
first read.

---

## 10. See Also

- [`docs/GLOSSARY.md`](GLOSSARY.md) — every term used here
- [`docs/FRONTEND.md`](FRONTEND.md) — the dashboard that calls these endpoints
- [`docs/EXTENSION.md`](EXTENSION.md) — the Chrome clipper, which calls three of them
- [`docs/agents/README.md`](agents/README.md) — index of the orchestration components
  - [`query-orchestrator`](agents/query-orchestrator.md) — `api/chat.py::_chat_stream`
  - [`llm-router`](agents/llm-router.md) — `lightrag_engine.py`
  - [`multi-hop-planner`](agents/multi-hop-planner.md) — `multihop.py`
  - [`spreadsheet-sql-agent`](agents/spreadsheet-sql-agent.md) — `spreadsheet_query.py`
  - [`entity-extraction-agent`](agents/entity-extraction-agent.md) — `gliner_extract.py`
  - [`folder-ingestion-agent`](agents/folder-ingestion-agent.md) — `folder_ingest.py`
  - [`code-intelligence-agent`](agents/code-intelligence-agent.md) — `code_intel.py`
  - [`grounding-verifier`](agents/grounding-verifier.md) — `grounding.py`, `provenance.py`
- [`docs/README.md`](README.md) — index and suggested reading order
