# Glossary

Shared vocabulary for every document in `docs/`. A term is defined **once, here**, and
the other documents link back to this file rather than redefining it. If two documents
seem to disagree about what a word means, this file is the tiebreaker.

Terms are grouped by where you'll first meet them. Within each group they're
alphabetical.

- [Retrieval & RAG](#retrieval--rag)
- [The knowledge graph](#the-knowledge-graph)
- [Ingestion](#ingestion)
- [Tabular data](#tabular-data)
- [Code intelligence](#code-intelligence)
- [Chat & answering](#chat--answering)
- [Deck Studio](#deck-studio)
- [Infrastructure & operations](#infrastructure--operations)

---

## Retrieval & RAG

### Bi-encoder
An embedding model that turns a query and a document into vectors **separately**, then
compares the vectors. Fast enough to search millions of documents, but it never actually
reads the query and the document side by side. Contrast [cross-encoder](#cross-encoder).
The system's embedding model (`all-MiniLM-L6-v2`) is a bi-encoder.

### BM25
A classic keyword-relevance formula from information retrieval. It scores a document by
how often the query's terms appear in it, damped so that a word repeated fifty times
doesn't count fifty times, and weighted so that rare words count more than common ones.
This project implements a simplified BM25 in `backend/app/services/sparse.py` — the
term-frequency saturation locally, and the rare-word weighting delegated to Qdrant's
[IDF modifier](#idf-modifier).

### Chunk
A slice of a document, roughly 1,024 tokens with 100 tokens of overlap with its
neighbours. Chunking exists because a whole PDF is too big to embed meaningfully, and
because retrieval wants to return the paragraph that answers the question rather than
the book that contains it. Configured by `CHUNK_TOKEN_SIZE` / `CHUNK_OVERLAP_TOKEN_SIZE`.

### Cross-encoder
A model that reads the query and one candidate document **together** in a single forward
pass and scores that pair directly. Much more accurate than a
[bi-encoder](#bi-encoder) and far too slow to search with — so it's used only to
re-order a shortlist. See [reranking](#reranking).

### Dense vector
The 384-number embedding produced by `all-MiniLM-L6-v2`. It captures *meaning*: two
paraphrases of the same sentence land near each other even with no shared words. Its
weakness is exactly the reverse — an exact token like `INV-2024-017` gets smeared into a
fuzzy neighbourhood. That's what the [sparse vector](#sparse-vector) is for.

### Evidence chain
The ordered list of things one source contributed to an answer:
`Source document → Chunk → Entity → Relationship → … → Answer`. Built by
`backend/app/services/provenance.py` from what actually survived truncation into the
prompt — *not* the candidate set that was considered. Rendered by the frontend's
provenance panel and persisted with the assistant message so an old conversation's
panels still work. See [`grounding-verifier`](agents/grounding-verifier.md).

### Grounding
A post-hoc check of whether the answer's sentences are supported by the retrieved
evidence. Deliberately lexical, not an LLM judge: it measures what fraction of a
sentence's content words appear anywhere in the evidence text, and flags a sentence
below 50%. It **flags rather than strips**, because the answer has already streamed to
the user by the time the check runs.

### Hybrid search
Running a [dense](#dense-vector) and a [sparse](#sparse-vector) search over the same
collection and fusing the two ranked lists with [RRF](#rrf-reciprocal-rank-fusion). One
Qdrant Query API call does all of it server-side. This is what makes both "what does this
document say about pricing?" and "find INV-2024-017" work against one index.

### IDF modifier
A Qdrant collection setting (`models.Modifier.IDF`) that makes Qdrant compute
inverse-document-frequency weights itself, from the collection's own contents. It's why
`sparse.py` only has to emit term ids and raw frequencies — there are no corpus
statistics to keep in sync with the index.

### LightRAG
The open-source RAG framework (`lightrag-hku`) this project is built on. It owns
chunking, entity extraction orchestration, graph merging, context assembly and answer
generation. This project supplies it with custom storage
([`HybridQdrantStorage`](#hybridqdrantstorage)), custom LLM functions per
[role](#role-llm-config), a custom extractor
([GLiNER](#gliner)), and a reranker.

### Mix mode
The `QueryParam(mode="mix")` retrieval mode used for every chat query: LightRAG searches
chunks, entities *and* relationships, then assembles a combined context. The alternatives
(`naive`, `local`, `global`, `hybrid`) are not used here.

### Multi-hop question
A question that cannot be answered by one retrieval because its subject is only
identified by another fact — "which columns feed the metric the Q3 report calls out?"
needs the metric found first, then its columns. See
[`multi-hop-planner`](agents/multi-hop-planner.md).

### Prefetch branch
One half of a hybrid Qdrant query. Each branch (dense, sparse) independently retrieves
`top_k × HYBRID_PREFETCH_MULTIPLIER` candidates before fusion. Fusion can only rank what
the branches returned, so the multiplier (default 4) is what gives a weak-semantic /
strong-keyword match room to surface.

### Reranking
A second pass over retrieved chunks using a [cross-encoder](#cross-encoder)
(`ms-marco-MiniLM-L-6-v2`), which reorders ~40 vector-search candidates down to the
`RERANK_TOP_N` that fit in the context budget. Local, no API. It matters *more* the
tighter the budget: the budget decides how many chunks survive, reranking decides which.

### RRF (Reciprocal Rank Fusion)
The algorithm that merges the dense and sparse result lists. It scores each document by
the reciprocal of its rank in each list and sums those, so a document ranked #1 by
keywords and #40 by semantics still beats one ranked #20 by both. Crucially it uses
*ranks*, not scores — which is what lets a cosine similarity and a BM25 score be combined
at all, since they're on incomparable scales.

### Schema card
The description string written onto a [Worksheet](#worksheet-node) or
[Column](#column-node) node, which is also indexed as that node's retrieval text. It
reads like `Column revenue (header 'Revenue', currency, DOUBLE) of worksheet 'Q3' in the
spreadsheet books.xlsx, queryable as workbook_a1b2__q3.revenue.` Naming a column in a
question hits its card exactly on the sparse half of hybrid search — which is how the
system decides a question is about tabular data without spending an LLM call.

### Sparse vector
A list of `(term_id, weight)` pairs derived from a text's words: regex tokenize →
lowercase → drop stopwords → `blake2b` hash to a uint32 term id → BM25 saturation on the
count. It captures *exact tokens* where the [dense vector](#dense-vector) captures
meaning.

---

## The knowledge graph

### canonical_label()
`graph_schema.canonical_label()` — folds a raw entity type into the one spelling the
graph uses: strip punctuation, remove whitespace and underscores, lowercase. So
`CodeFile` → `codefile` and `Organization` → `organization`. Lowercase isn't a style
preference: LightRAG normalizes every extracted type this way before it reaches the
graph, so anything written *past* LightRAG has to match or the legend splits one type in
two.

### canonical_name()
`graph_schema.canonical_name()` — folds a raw entity name so one real-world thing gets
one node: separators (`_`, `-`, en/em dash) become spaces, a leading article and a
trailing possessive are dropped, edge punctuation is stripped, and the result is
uppercased. `LIONEL_MESSI`, `Lionel-Messi` and `Lionel Messi` all become `LIONEL MESSI`.
This is the project's **entity resolution** — LightRAG merges entities by exact string
match, so the fold has to happen *before* extraction hands anything over.

### Column node
A `column`-labelled graph node, one per column of a worksheet, written deterministically
by `tabular_graph.py`. Node id is `<file>:<sheet>.<column>`. Carries `table`, `column`,
and a [schema card](#schema-card) as its description.

### Edge category
`structural` | `behavioral` | `semantic` — how an edge should be *read*, and therefore
drawn. It is **derived** from the relationship type by `graph_schema.edge_category()`,
never stored, so every edge has one including edges written before the concept existed.
Containment (`CONTAINS_FILE`, `HAS_COLUMN`) is structural; execution and derivation
(`CALLS`, `IMPORTS`, `DERIVED_FROM`) is behavioral; anything the text extractor wrote is
semantic.

### Entity
A named thing found in prose by the [extractor](#gliner) — a person, organization,
location, product, technology, event, concept or date. Becomes a graph node whose
`entity_type` is its label and whose description is the sentence it was found in.

### ExternalSymbol
A graph node (`externalsymbol`) for a function or method that a scanned repo *calls* but
does not *define* — `httpx.get`, say. Created only when the call is traceable to an
import, so builtins (`len`, `print`) and methods on locals never become nodes. Node id is
`external:<dotted name>`. Without this, an unresolved library call would be a string
buried on the caller node instead of a visible edge.

### Hop / hop-expand
Loading one node's immediate neighbours and adding them to the canvas, rather than
loading the whole graph. Served by `GET /api/v1/graph/expand`. This is the *only* way to
reach a node deep in a tree: the whole-graph endpoint runs a degree-prioritised BFS
capped at `max_nodes`, so a folder eight levels down is past its horizon no matter how
far you zoom.

### Label
A node's type, from a closed vocabulary. Rides on the `entity_type` property, which is
what the graph API and the Graph Explorer already read — so adding a label required no
new field anywhere. The full set is `graph_schema.NODE_LABELS`: the eight document types
from `ENTITY_LABELS`, plus `workbook`/`worksheet`/`column`, plus
`folder`/`file`/`codefile`/`class`/`function`/`method`/`image`/`video`/`externalsymbol`,
plus `source` and `UNKNOWN`.

### Property graph
The data model: nodes carry a **label** plus properties, edges carry a **type** plus
properties, and both vocabularies are closed. It's Neo4j's model without Neo4j — the
discipline is enforced in `graph_schema.upsert_node` / `upsert_edge`, which reject an
unknown label or relationship type at the boundary, while the storage stays LightRAG's
local NetworkX graph.

### rel_from / rel_to
Two properties written onto every deterministic edge recording which way it actually
points. The underlying store is an *undirected* NetworkX graph — it keeps the pair, not
the order. For `RELATED_TO` that never mattered; for `CALLS` it is the entire content of
the edge, since "A calls B" read back as "B calls A" is not a weaker answer, it's a wrong
one.

### Relationship type
An edge's type, from the closed set `graph_schema.REL_TYPES`, stored in the edge's
`keywords` property. The full set: `RELATED_TO`, `HAS_SHEET`, `HAS_COLUMN`,
`DERIVED_FROM`, `HAS_VALUE`, `HAS_ROOT`, `CONTAINS_FOLDER`, `CONTAINS_FILE`, `DEFINES`,
`DEFINES_METHOD`, `CALLS`, `IMPORTS`, `INHERITS`, `IMPLEMENTS`.

### Source Supernode
A `source`-labelled graph node, one per ingestion event, with a `HAS_ROOT` edge to every
node that ingestion put in the graph. Node id is `source:<file_name>`. It exists because
before it, "show me everything that came out of this upload" was a property filter over
the whole graph rather than one hop — and because the Graph Explorer's landing view is
exactly `count(Source)` nodes. The SQLite [manifest](#manifest) remains the system of
record for metadata; the supernode is the *graph's* handle on it.

### Worksheet node
A `worksheet`-labelled graph node, one per sheet of an ingested workbook. Node id is
`<file>:<sheet>`. Carries a `table` property naming its DuckDB table, which is how the
[SQL router](#sql-router) turns a retrieval hit into a queryable table name.

---

## Ingestion

### Content hash
`sha256` of the whitespace-normalised document text, stored on the manifest row. Two
uploads with the same hash are the same document: the second returns the first's record
with `deduped: true` and nothing is re-indexed.

### Deduped
A flag on an ingest response meaning "we already had this — here's the existing record".
Not an error; the UI shows "Already in the knowledge base".

### doc_id
The primary key of a manifest row, `doc-<32 hex>`. Also written as `source_id` onto every
graph node and edge that ingestion produced.

### Document leaf
A `.pdf` / `.md` / `.txt` / `.xlsx` / `.xlsm` / `.csv` file found inside a folder scan.
Unlike other files, its *contents* are routed through the normal ingestors so it becomes
searchable — a folder ingest that only tracked file names would be a file browser, not a
knowledge base.

### Gleaning
LightRAG's optional second extraction pass over the same chunk, to catch entities the
first pass missed. It doubles the LLM calls per chunk, so it's disabled here
(`MAX_GLEANING=0`). The [GLiNER](#gliner) extractor returns an empty result for the
gleaning prompt, since a single-pass encoder has nothing to add on a second look.

### GLiNER
A local zero-shot named-entity-recognition encoder (`urchade/gliner_small-v2.1`). Given a
text window and a list of candidate labels, it scores spans against those labels in one
forward pass — no API, no rate limit, no quota. It's what makes ingesting a 200-page book
possible where the per-chunk LLM path would have been bound to ~2 chunks/minute against
Groq's TPM ceiling. See [`entity-extraction-agent`](agents/entity-extraction-agent.md).

### .kbignore
A per-folder ignore file read by `folder_ingest.load_ignores()`, one glob per line, `#`
for comments. Deliberately **not** a `.gitignore` implementation: no negation, no
anchoring, no directory-only semantics. Patterns are matched against both the bare name
and the path relative to the scan root, and are added to `DEFAULT_IGNORES`.

### Manifest
The SQLite inventory at `storage/manifest.sqlite3` (`backend/app/services/manifest.py`)
holding one row per ingested document: `doc_id`, file name, source type, content hash,
chunk count, byte size, date added. It records the metadata LightRAG itself doesn't
track, and it's what the Knowledge Base page lists.

### Ontology
The closed set of entity labels the extractor is scored against on every chunk, from the
`ENTITY_LABELS` environment variable. Default:
`person, organization, location, product, technology, event, concept, date`. Keeping it
short and deliberate is what keeps entity types consistent across a document, instead of
a model inventing "artifact" in one chunk and "tool" in the next.

### source_type
Which kind of ingestion produced a document: `pdf`, `markdown`, `text`, `spreadsheet`,
`youtube`, `article`, `article_zenrows`, `article_clipper`, `paste`, `folder`. Stored on
the manifest row and on the [Source Supernode](#source-supernode); the frontend picks the
row's badge icon and the supernode's glyph from it.

### Track id
A handle LightRAG returns from `ainsert()` that can be exchanged for the document's
status row. Used by `ingestion.ingest_text` to find out whether the insert was actually
accepted, or silently filed under a synthetic `dup-` id because the filename was already
taken.

---

## Tabular data

### Computed column
A column added to a spreadsheet **through chat** — "add a margin column". Written by
`spreadsheet_query.add_computed_column()` as `ALTER TABLE … ADD COLUMN` plus an `UPDATE`,
with the expression bound against the real columns first. Marked `added_later = true` in
the metadata table, which is what makes it the *only* kind of column the undo endpoint
will drop.

### DuckDB
The embedded analytical database at `storage/spreadsheets.duckdb` holding one table per
ingested worksheet. **No cell value is ever answered by a language model** — DuckDB does
every calculation; the LLM only writes SQL that is parsed, bound and row-capped before a
single row is read.

### Formula lineage
Which columns a formula column is computed *from*, recovered by reading the formula's
cell references and mapping the column letters back to headers. Becomes `DERIVED_FROM`
edges between [Column nodes](#column-node).

### `_node_rels_columns`
DuckDB's internal metadata table: one row per column of every ingested worksheet, holding
`table_name`, `column_name`, `data_type`, `semantic`, `formula`, `derived_from`,
`workbook`, `worksheet`, `added_later`. It's what names the tables to drop on delete and
the graph nodes to remove. Generated SQL is explicitly forbidden from touching it.
(Formerly `_crag_columns`; `get_connection()` renames it in place on first open.)

### Semantic type
A column's *meaning* — `numeric`, `currency`, `percentage`, `date`, `boolean`,
`categorical`, `text`, `derived` — as opposed to its storage type. Excel keeps dates as
serial numbers and percentages as plain floats, so the cell's **number format**, not its
value, is what tells them apart.

### SQL router
`spreadsheet_query.relevant_tables()` — decides whether a chat message is about tabular
data, by querying the entity vector store and keeping only hits that are
[Worksheet](#worksheet-node) or [Column](#column-node) nodes. An empty list means the
question isn't about the data and no LLM call is made at all.

### Workbook node
A `workbook`-labelled graph node, one per ingested spreadsheet file. Node id is the bare
file name (which is why the [Source Supernode](#source-supernode) prefixes itself with
`source:` — so the two can never collide).

---

## Code intelligence

### Call resolution
Matching a call site's name to the symbol node it refers to. Done **by name only**, scoped
file-first then project-wide, and **only when exactly one candidate matches**. Zero or
several candidates means the call goes into `calls_unresolved` on the caller rather than
being guessed at — a wrong `CALLS` edge is worse than a missing one, because nothing
downstream can tell it's wrong.

### calls_in_count / calls_out_count
Properties on a code symbol node giving the **true project-wide** number of callers and
callees, computed at write time. They are not derivable from the loaded subgraph, which is
usually truncated — counting its edges would under-report "who calls this".

### Confidence
A number on a `CALLS` edge: `1.0` for a file-local resolution (a certainty), `0.8` for a
project-wide unique name (a strong guess a same-named import could fool), `0.0` for an
[ExternalSymbol](#externalsymbol) edge.

### Qualified name
A symbol's name within its file: `helper` for a module-level function,
`MyClass.save` for a method. The full node id is `<CodeFile node>::<qualified name>`.

### Symbol
A class, function or method extracted from a source file. Only module-level definitions
and class methods become nodes — a closure or an inline arrow function is *not* its own
node, and its calls are attributed to the function enclosing it.

### tree-sitter
The parser generator used for every language except Python. Its grammars are an
**optional** install (`requirements-codeintel.txt`); without them, non-Python code files
are still ingested as [CodeFile](#label) leaves and simply carry no symbols. Python uses
the standard library's `ast` instead — no dependency, correct by construction.

---

## Chat & answering

### Fallback chain
The ordered list of providers a role tries: **Groq → OpenRouter → Ollama**. Each step is
attempted only if the previous one has no key or its call failed. See
[`llm-router`](agents/llm-router.md).

### Query context token budget
`QUERY_CONTEXT_TOKEN_BUDGET` (default 8,000) — the hard ceiling on everything LightRAG
assembles for one chat query: entities + relationships + chunks + system prompt.
LightRAG's own default is 30,000, which no free-tier Groq model can accept in one minute,
so every chat call would 429 and silently fall back. The entity and relation sub-budgets
are *derived* from this one number rather than configured separately, so they can't drift
apart.

### Role LLM config
LightRAG's mechanism for pointing different jobs at different models. This project
configures four roles: `extract` (graph building — GLiNER, or Groq), `keyword` (query
keyword extraction — Groq), `query` (answering — Groq/OpenRouter/Ollama) and `vlm`
(declared for logging only). Splitting `query` and `extract` onto different models is what
stops chat and ingest competing for the same per-model rate-limit bucket.

### Seed keywords
Entity names discovered by a multi-hop question's early retrievals, ordered by how many
hops found them, and injected into the final query's `ll_keywords`. LightRAG skips its own
keyword extraction when keywords are pre-supplied, so the last retrieval searches for
hop 1's discoveries *by name*.

### Session
A persisted chat thread: a row in `storage/chat.sqlite3` plus its messages, each assistant
message carrying its [evidence chain](#evidence-chain) as JSON. Sessions are optional —
`POST /chat/stream` without a `session_id` answers identically and writes nothing.

### SSE (Server-Sent Events)
The one-way streaming protocol the chat endpoint uses: `data: {json}\n\n` frames over a
long-lived HTTP response. Frame types: `sources`, `evidence`, `table`, `token`,
`grounding`, `error`, `done`. See [BACKEND.md §6](BACKEND.md#post-apiv1chatstream).

### Token bucket
`rate_limiter.TokenBucket` — an async rate limiter sized in tokens-per-minute. A caller
waits until its estimated token cost is available, then spends it. Spending the budget at
the rate it refills avoids the 429 entirely, where reactive 429-then-backoff wastes a
round trip and the retry wait on every burst.

### TPD / TPM
Tokens-per-day and tokens-per-minute — the two rate limits Groq enforces. They need
different handling: a TPM throttle clears in ~60 seconds and is worth waiting out, while a
TPD exhaustion won't reset for tens of minutes, so it puts the model on a cooldown and
skips straight to the fallback.

---

## Deck Studio

> Terms from `Agents/A1_pptx/`, the standalone deck-generation application.

### Ask AI
Deck Studio's in-editor assistant. It receives the **current structured state** of the
user's selection and returns *operations*, never prose and never state — so an answer
that's wrong costs a rejection message rather than a corrupted deck. See
[`deck-assist`](agents/deck-assist.md).

### Deck / Presentation
Deck Studio's single document:
`Presentation { id, deck_title, template, w, h, slides[], created, updated }`, where each
`Slide` holds `elements[]`. Stored as `outputs/<id>.json`. Both renderers — the browser
editor and the `.pptx` exporter — are a loop over the same element list, which is why the
editor cannot show you something the export won't.

### Element
The atomic unit of a deck: `{ id, type, x, y, w, h, rotation, locked, hidden, content,
style }` where `type` is one of `text · image · shape · line · table · chart`. Geometry is
in **inches** (the slide is 13.333 × 7.5) and type size in **points** — exactly what
python-pptx wants, so neither renderer does arithmetic the other might get wrong. Array
order *is* z-order; there's no separate `zIndex` to fall out of step with it.

### expand()
`deck.expand()` — turns the planner's authored slide kinds (`title`, `bullets`, `stat`,
`two_col`, `chart`, `table`, `closing`) into flat element lists. It runs **once, on the
way in**, which is what lets both renderers stay dumb loops that don't know what a "stat
slide" is.

### Slot
An `ImageSlot` — a request for one picture on one slide, carrying its role
(`background` / `hero` / `supporting`), target aspect ratio and minimum pixels. The
caller owns the layout, so the caller supplies the slots.

### Theme token
A colour named `primary`, `accent`, `text`, `muted` or `bg` rather than a literal
`#rrggbb`. Changing the template repaints every token-coloured element while anything set
to a specific colour stays put. Fonts work the same way — a role (`display` / `body`) the
template resolves, or a literal family.

### Visual coverage
The guarantee that no content slide arrives as nothing but text. `deck.has_visual()` reads
the elements a slide **actually ended up with** — it never asks the model whether it added
a picture, since that's asking the thing that just failed to do it whether it did it. A
slide that fails gets one deterministic, offline repair pass. See
[`visual-coverage`](agents/visual-coverage.md).

---

## Infrastructure & operations

### Detached document pass
The second phase of a folder ingest, run as a background `asyncio` task after the HTTP
response has been sent. It's detached because routing documents through the LLM pipeline
takes minutes, and doing it inside the request meant a client timeout cancelled the walk —
`CancelledError` is a `BaseException`, so no `except Exception` caught it — and everything
past the first document leaf was lost.

### Durability point
The moment in a folder ingest when `graph_schema.flush()` commits the complete tree and
every code symbol to disk. Direct graph writes only mutate memory; LightRAG's own pipeline
flushes at the end of *its* batch, but nothing written outside it is covered. Everything
slow happens after this point, so nothing slow can cost you the tree.

### HybridQdrantStorage
The project's LightRAG vector-storage backend
(`backend/app/services/qdrant_store.py`), a subclass of LightRAG's `QdrantVectorDBStorage`
that overrides four methods to give every point two named vectors —
[`dense`](#dense-vector) and [`sparse`](#sparse-vector) — and to answer a query with one
fused call. Buffering, deletes, workspace isolation and read-your-writes are inherited
untouched.

### Namespace
LightRAG's partition of the vector store: `chunks`, `entities`, `relationships`. Each
becomes its own Qdrant collection. They all share one `QdrantClient`, because embedded
Qdrant takes an exclusive lock on its directory and a second client would fail to open it.

### Opik
Comet's LLM tracing service. Every LLM call in the backend is wrapped in `@opik.track`.
Leave `OPIK_API_KEY` blank to disable tracing.

### ponytail comment
A comment beginning `ponytail:` marking a deliberate simplification with a known ceiling
and its upgrade path — e.g. `# ponytail: global lock, per-account locks if throughput
matters`. Every one of them is a real, acknowledged limitation; they're collected in each
document's Chapter 9.

### Stale collection
A Qdrant collection whose stored vectors don't match the current embedding model's
dimension — usually because the embedding model changed, or a test leaked into the real
storage directory. `HybridQdrantStorage._stale_collection()` detects it **at boot** by
checking both the declared config and a real stored vector, and recreates the collection,
because the alternative is a numpy broadcast error at flush time after a whole document
has already been extracted.

### Storage directory
`backend/storage/` — everything the running system persists:

| Path | Holds |
|---|---|
| `storage/kb/` | LightRAG's KV stores, doc-status store, and the NetworkX graph |
| `storage/qdrant/` | The embedded Qdrant collections (dense + sparse vectors) |
| `storage/spreadsheets.duckdb` | One table per ingested worksheet, plus `_node_rels_columns` |
| `storage/manifest.sqlite3` | The document [manifest](#manifest) |
| `storage/chat.sqlite3` | Chat [sessions](#session) and messages |
| `storage/scraped_articles/` | Cached URL scrapes as `.md` with YAML frontmatter |

### ZenRows
A commercial scraping proxy used as the *first* attempt for URL ingestion, with
`trafilatura`'s direct fetch as the fallback. `js_render` and `premium_proxy` cost extra
credits, so they're off for the first attempt and only used on the automatic retry when
the first scrape comes back short or fails. Leaving `ZENROWS_API_KEY` blank is a zero-code
rollback to trafilatura-only.

---

## See also

- [`docs/README.md`](README.md) — index and reading order
- [`docs/BACKEND.md`](BACKEND.md)
- [`docs/FRONTEND.md`](FRONTEND.md)
- [`docs/EXTENSION.md`](EXTENSION.md)
- [`docs/agents/README.md`](agents/README.md)
