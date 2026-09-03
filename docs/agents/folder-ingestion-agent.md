# Agent: Folder Ingestion Agent

> `backend/app/services/folder_ingest.py` — walks a directory on the server's disk, mirrors
> it into the graph, then indexes the documents inside it in a detached second pass.

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

Point this at a directory and the whole tree appears in the knowledge graph: every folder a
node, every file a node, every containment relationship an edge. Nothing is extracted or
inferred — the tree in the graph *is* the tree on disk.

Two things make it more than a file lister. Code files are handed to
[`code-intelligence-agent`](code-intelligence-agent.md), which hangs classes, functions,
methods and a call graph off them. And document files — PDFs, Markdown, text, spreadsheets —
are routed through the **normal ingestors**, so their contents actually land in the knowledge
base by exactly the path a manual upload takes. A folder ingest that only tracked file names
would be a file browser, not a knowledge base.

The structure of the run is shaped by one hard-won lesson. Routing a document through the
LLM pipeline takes minutes. Doing that inside the tree-write loop put those minutes inside
the HTTP request — so a client timeout cancelled the walk, and because `CancelledError` is a
`BaseException` no `except Exception` caught it, the closing flush never ran, and
**everything past the first document leaf was lost.** So the run is now split: write the
whole tree, write every code symbol, **flush to disk**, respond, and only then index
documents in a background task. That flush is the durability point, and nothing slow happens
before it.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **`os.walk`** (stdlib) | The walk. `dirnames` is pruned *in place*, which is what stops it descending into an ignored tree rather than walking it and discarding the results. |
| **`fnmatch`** (stdlib) | The ignore rules. Deliberately not a `.gitignore` implementation — no negation, no anchoring, no directory-only semantics. |
| **`mimetypes`** (stdlib) | Fills in a `mime_type` property. It never decides a label: it disagrees with itself across platforms (on Windows it reads the registry). |
| **`asyncio.create_task`** | The detached document pass. Tasks are held in a module-level set because asyncio keeps only weak references to running tasks and would garbage-collect one mid-flight. |
| **`hashlib`** (stdlib) | A tree-*shape* hash, not a content hash. |
| **Pillow** (optional) | Image dimensions. Absent, `Image` nodes carry format and size but no `width`/`height`. |

---

## 3. Folder & File Structure

```
backend/app/services/
├── folder_ingest.py           # 578 lines
│   ├── DEFAULT_IGNORES        # .git, node_modules, __pycache__, dist, .venv, …
│   ├── CODE_LANGUAGES         # 30 extensions -> language string
│   ├── IMAGE_EXTENSIONS / VIDEO_EXTENSIONS / DOCUMENT_EXTENSIONS
│   ├── PROCESSING / COMPLETED / FAILED
│   ├── _background: set[asyncio.Task]   # holds detached tasks against GC
│   │
│   ├── load_ignores(root) / is_ignored(name, rel_path, patterns)
│   ├── classify(ext)                    # -> a node label
│   ├── _resolve_root(path)              # THE TRUST BOUNDARY
│   ├── scan(root, name)                 # THE WALK — pure, no writes, no rag
│   ├── _log_levels / _parent_of / _file_description / _image_size
│   │
│   ├── ingest_folder(path, name, index_documents)   # THE ENTRY POINT
│   ├── _index_documents(rag, name, doc_id, entries, errors)  # detached pass
│   ├── remove(rag, name)
│   ├── wait_for_documents()             # for scripts and tests
│   └── __main__ self-check
│
└── test_folder_ingest.py      # 359 lines
```

---

## 4. How This Fits Into the Bigger Picture

```
   POST /api/v1/ingest/folder  ← FRONTEND FolderInput (path-based, not an upload)
        │
        ▼
   ingest_folder(path, name, index_documents)
        │
        ├─ _resolve_root      → IngestionError if outside FOLDER_INGEST_ROOT
        ├─ scan               → {folders: [...], files: [...]}     (pure)
        ├─ manifest.insert_document(source_type="folder")
        ├─ source_graph.create(…, status="processing", **counts)
        ├─ graph_schema.upsert_node/upsert_edge  × every folder and file
        ├─ code_intel.extract  × every code file   ─────► code-intelligence-agent
        ├─ code_intel.project  once, across the whole tree
        ├─ graph_schema.flush  ◄── THE DURABILITY POINT
        │
        └─ RESPONDS  {status: "processing", documents_pending: N, …}
                │
                └─ _spawn(_index_documents(...))       detached
                        │
                        └─ ingestion.ingest_file_bytes × each document leaf
                                │  (the same path a manual upload takes)
                                └─ source_graph.set_status(completed | failed)
```

**Deletion** goes the other way: `ingestion.delete_document` dispatches on
`source_type == "folder"` and calls `folder_ingest.remove(rag, name)`, which takes the
supernode *and* the tree in one call — deliberately, so there's no way to half-delete a
folder and leave a dangling `source:` node.

**The frontend** never polls for completion; see [§9](#9-known-limitations--open-todos).

---

## 5. Core Concepts & Key Components

### Node ids are paths, and that's the whole idempotency story

```
<source name>/<path relative to the root>
```

`crag-backend/app/services/code_intel.py`. Re-ingesting the same folder rewrites the **same
nodes** rather than duplicating the tree, and two different folders never collide unless
given the same name. `remove()` exploits the same property: everything to delete shares one
prefix, so it's a single pass rather than a traversal.

### The durability point

Direct graph writes only mutate memory. LightRAG's own pipeline flushes at the end of *its*
batch, but nothing written outside it is covered — the tree, the supernode, the code symbols.
`graph_schema.flush()` is what commits them.

Its placement is exact: **the complete tree and every symbol are on disk before any slow work
starts.** And when there's nothing pending, `set_status(completed)` runs *before* the flush,
not after — a status written past the commit point lives in memory only and the node on disk
stays `processing` forever.

### Two-phase, because phase two is slow and phase one must not be lost

| Phase | Runs | Cost | If it fails |
|---|---|---|---|
| 1. Tree + symbols | Inside the request | Seconds | The request fails; nothing was written |
| 2. Documents | Detached task | Minutes | The Source node goes `failed`; **the tree survives** |

`_index_documents` catches `BaseException` — including `CancelledError` — sets `status =
FAILED`, re-raises, and writes the status in a `finally`. Recording the truth beats leaving
the node stuck on `processing` forever.

### The status lifecycle exists for exactly one ingestor

`source_graph`'s own docstring makes the point: the other six ingestors run synchronously
inside one request and raise *before* the manifest row (and therefore the Source node)
exists, so for them `processing` is a state nothing could ever observe. A folder's document
pass is detached and takes minutes, so for that one it is real.

### Ignore rules, deliberately not gitignore

`DEFAULT_IGNORES` covers build output, dependency trees and tool caches — never content,
always bulk: `.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`, `.next`,
`coverage`, `.serena`, and ~25 more. A `.kbignore` in the root adds one glob per line, `#`
for comments.

It is **not** a `.gitignore` implementation: no negation, no anchoring, no directory-only
semantics. A pattern matches a bare name **or** a path relative to the root, and that covers
what a scan actually needs to skip. Pruning happens in `dirnames[:]` so `os.walk` never
descends.

### Classification is by extension only

`mimetypes` disagrees with itself across platforms — on Windows it reads the registry — so it
fills in a property but never decides a label.

| Extension set | Label |
|---|---|
| `CODE_LANGUAGES` (30 entries: `.py`, `.ts`, `.tsx`, `.go`, `.rs`, `.java`, …) | `codefile` |
| `IMAGE_EXTENSIONS` (11) | `image` |
| `VIDEO_EXTENSIONS` (16 — **audio rides with video**, both being timed media whose leaf carries metadata only until a transcription hook runs) | `video` |
| everything else, including `.pdf` | `file` |

A file whose extension is in `CODE_LANGUAGES` is a `CodeFile` **even if no parser for that
language exists yet** — the language string is what the parser registry dispatches on.

### `FOLDER_MAX_DEPTH` defaults to 1000, on purpose

The comment is explicit: a folder ingest is meant to mirror the whole tree, and a small
default here is exactly the bug that looks like "the walk stopped early". Set it only to
deliberately clip a pathological tree.

`_log_levels` prints one line per depth after every scan, so a future depth regression is
readable straight off the log without re-deriving ground truth from the filesystem.

### The path is a trust boundary

`_resolve_root` expands, absolutises and checks `os.path.isdir`. If `FOLDER_INGEST_ROOT` is
set, `os.path.commonpath` confines ingestion to that subtree — and a `ValueError` (different
drives on Windows) counts as outside. Unset means the operator accepted that any readable
directory is fair game: fine for the local single-user default, wrong the moment this is
bound to anything but localhost.

---

## 6. Function & Component Reference

---

### `ingest_folder(path, name=None, index_documents=True)`

**What it does:** The entry point. Mirrors a folder into the graph and queues its documents.
Idempotent on the same path + name.

**Input:**

| Param | Type | Example |
|---|---|---|
| `path` | `str` | `"D:/Crag/backend"` |
| `name` | `str \| None` | defaults to the directory's basename |
| `index_documents` | `bool` | `False` makes this a pure structure scan — seconds instead of minutes on a repo full of PDFs |

**Output:** `dict` — the manifest record plus tree and symbol counts. Raises
`IngestionError`.

**Example:**
```python
await folder_ingest.ingest_folder("D:/Crag/backend")
# => {"doc_id": "doc-9c2f…", "file_name": "backend", "name": "backend",
#     "path": "D:\\Crag\\backend", "source_type": "folder",
#     "folders": 9, "files": 61, "code_files": 58, "images": 0, "videos": 0,
#     "classes": 12, "functions": 214, "methods": 63,
#     "calls": 486, "external_symbols": 41, "unresolved": 130,
#     "documents_indexed": 0, "documents_pending": 2, "status": "processing",
#     "max_depth_reached": 3, "total_folders": 9, "total_files": 61,
#     "chunk_count": 61, "size_bytes": 412887, "errors": []}
```

**Notes:** `documents_indexed` is **always 0** here — the count lands on the Source node
later. Raises `IngestionError` for a non-directory, a path outside `FOLDER_INGEST_ROOT`, or a
tree where everything was ignored. On re-ingest, `manifest.find_by_name` finds the old row,
its `doc_id` is **reused** and the row deleted and rewritten, so the inventory keeps one
entry. The content hash is a hash of the `path:size` listing — a folder has no single body to
hash, and hashing every file just to dedup a re-scan is work nobody asked for.

---

### `scan(root, name)`

**What it does:** Walks the tree and returns the nodes to write. **No writes, no `rag`** —
separated so the walk, the ignore rules and the classification are testable on a `tmp_path`
alone.

**Input:** `root: str` (absolute), `name: str`.

**Output:** `{"folders": [...], "files": [...]}`.

**Example:**
```python
scan("/tmp/proj", "proj")
# => {"folders": [
#      {"id": "proj", "name": "proj", "rel_path": "", "depth": 0,
#       "parent": None, "children": ["src"]},
#      {"id": "proj/src", "name": "src", "rel_path": "src", "depth": 1,
#       "parent": "proj", "children": []}],
#     "files": [
#      {"id": "proj/src/app.py", "name": "app.py", "rel_path": "src/app.py",
#       "full_path": "/tmp/proj/src/app.py", "parent": "proj/src",
#       "ext": ".py", "label": "codefile", "language": "python",
#       "mime_type": "text/x-python", "size_bytes": 812, "too_big": False}]}
```

**Notes:** `dirnames[:]` is reassigned (sorted, filtered) so `os.walk` prunes rather than
walks-and-discards. Files are sorted so output is deterministic. An `OSError` on `getsize`
skips that file silently — a broken symlink shouldn't abort a scan. `too_big` is set from
`FOLDER_MAX_FILE_MB`; a too-big file still becomes a node with name, size and type, only its
content is skipped.

---

### `classify(ext)` / `load_ignores(root)` / `is_ignored(name, rel_path, patterns)`

**Examples** (all asserted by the module's `__main__` self-check):
```python
classify(".py")   # => "codefile"
classify(".png")  # => "image"
classify(".mp3")  # => "video"    audio rides with video
classify(".pdf")  # => "file"     documents are plain File leaves

patterns = ["node_modules", "*.log", "docs/private"]
is_ignored("node_modules", "a/node_modules", patterns)  # => True
is_ignored("run.log", "a/run.log", patterns)            # => True
is_ignored("private", "docs/private", patterns)         # => True   path match
is_ignored("main.py", "src/main.py", patterns)          # => False
```

Run it: `python -m app.services.folder_ingest` → prints `ok`.

---

### `_resolve_root(path)`

```python
_resolve_root("~/projects/api")          # => "/home/user/projects/api"
_resolve_root("/etc")                     # IngestionError if FOLDER_INGEST_ROOT is set
_resolve_root("/nope")                    # IngestionError: Not a directory: /nope
```

The confinement message names the allowed root:
`Folder ingestion is confined to /home/user/projects; /etc is outside it.`

---

### `_index_documents(rag, name, doc_id, entries, errors)`

**What it does:** The detached second pass. Routes each document leaf through
`ingestion.ingest_file_bytes` and records the outcome on the Source node.

**Output:** `None`. Runs as a background task.

**Notes:** Per-file failures are appended to `errors` and the loop continues — one unreadable
file must not abandon the rest of the tree; the leaf already exists, it just isn't indexed.
On success the file's graph node gets a `doc_id` property linking the leaf to the document it
became. The `except BaseException` / `finally` pair is the important part: a cancellation
sets `FAILED`, re-raises, and the `finally` still writes the status and flushes.

---

### `remove(rag, name)`

**What it does:** Drops every node of a folder ingest — supernode, tree, leaves, and any code
symbols hanging off them.

**Input:** `rag`, `name: str`. **Output:** `int` — how many nodes were dropped.

**Example:**
```python
await folder_ingest.remove(rag, "backend")   # => 348
```

**Notes:** The supernode is removed **here** rather than by the caller, deliberately: a
dangling `source:` node pointing at a tree that no longer exists is exactly the state a second
call site would leave behind. Node ids all share the `<name>/` prefix, which is what makes
this one pass instead of a traversal.

---

### `wait_for_documents()`

Blocks until every detached document pass has finished. For scripts and tests, which have no
browser to poll the Source node's status.

```python
await folder_ingest.ingest_folder("/tmp/proj")
await folder_ingest.wait_for_documents()      # now the documents really are indexed
```

---

## 7. End-to-End Walkthroughs

### 7.1 Scanning a repository with two PDFs in it

1. `POST /api/v1/ingest/folder {"path": "D:/Crag/backend", "index_documents": true}`.
2. `_resolve_root` → absolute path, is a directory, inside `FOLDER_INGEST_ROOT` (or it's
   unset). `name = "backend"`.
3. `scan`: `load_ignores` returns `DEFAULT_IGNORES` + any `.kbignore`. `os.walk` prunes
   `.venv`, `__pycache__`, `storage` and `.pytest_cache` in place. 9 folders, 61 files.
   `_log_levels` prints one line per depth.
4. `manifest.find_by_name("backend")` → `None`. `insert_document(source_type="folder")` with
   a tree-shape hash.
5. `source_graph.create(rag, record, status="processing", code_files=58, total_folders=9,
   max_depth_reached=3, origin_path=…)`.
6. Nine `upsert_node(…, FOLDER)` + eight `upsert_edge(parent, child, CONTAINS_FOLDER)`.
   Then `source_graph.attach(rag, record, ["backend"])` — **one** edge from the supernode to
   the tree root; everything else is reachable through it.
7. Per file: properties assembled (`loc` for code, `width`/`height` for images if Pillow is
   present), a node, and a `CONTAINS_FILE` edge. Code file contents are read **once** here
   and kept in `sources` — `loc` is needed now, the text is needed after every file node
   exists. Two PDFs go onto `pending`, **not** ingested.
8. `code_intel.extract` per code file, then one `code_intel.project` across the whole tree —
   a call in the first file can land in the last, so nothing can be written until everything
   is parsed.
9. **`gs.flush(rag)`.** Tree and symbols are on disk. `status` stays `processing` because
   `pending` is non-empty.
10. The endpoint returns: `documents_indexed: 0, documents_pending: 2, status: "processing"`.
11. `_spawn(_index_documents(...))`. Each PDF → `ingest_file_bytes` → `extract_pdf_text` →
    `ingest_text` → chunk, embed, extract entities, Source node. Its leaf node gets
    `doc_id`.
12. `finally`: `set_status(completed, documents_indexed=2, document_errors=0)`, `flush`.

The Knowledge Base page now shows **three** rows: the folder, and one per PDF. Deleting a PDF
removes the document; deleting the folder removes the tree but leaves the PDFs' own rows.

---

### 7.2 A structure-only scan

`{"index_documents": false}`. Steps 1–10 are identical, except step 7 never appends to
`pending`. So step 9's `status` is `COMPLETED`, written **before** the flush, and step 11
never happens. The response reads `documents_pending: 0, status: "completed"` and the whole
thing takes seconds.

---

### 7.3 Re-scanning after the code changed

1. Same path, same name. `manifest.find_by_name("backend")` → the existing row.
2. Its `doc_id` is **reused**; the old row is deleted and a new one inserted with the same id
   and a fresh tree hash. The inventory still shows one entry.
3. Every folder and file node is upserted at its **same path-derived id**, so unchanged
   entries are rewritten identically and new ones appear.
4. `code_intel.project` rewrites every symbol node and every edge from the current parse.
5. Flush. Documents re-run through `ingest_file_bytes`, where the **content hash dedup**
   short-circuits unchanged ones instantly.

**What re-ingest does not do:** delete nodes for files that were *removed* from disk. See
[§9](#9-known-limitations--open-todos).

---

## 8. Configuration & Setup

| Variable | Default | Effect |
|---|---|---|
| `FOLDER_INGEST_ROOT` | unset | Confines ingestion to one subtree. **Set this if the API is bound to anything but localhost.** |
| `FOLDER_MAX_FILE_MB` | `25` | Above this, a leaf node is created (name/size/type) but the content is skipped |
| `FOLDER_MAX_DEPTH` | `1000` | A sentinel, not a limit. Set it only to clip a pathological tree. |

Optional install for full fidelity:

```bash
pip install -r backend/requirements-codeintel.txt   # tree-sitter grammars + Pillow
```

Without it: non-Python code files still become `CodeFile` leaves with no symbols, and `Image`
nodes carry no dimensions. Nothing raises.

### A `.kbignore`

```
# one glob per line; matches a bare name or a path relative to the root
*.min.js
fixtures/
docs/private
```

### Tests and verification

```bash
cd backend
pytest app/services/test_folder_ingest.py -v
python -m app.services.folder_ingest            # classification + ignore self-check

python -m scripts.verify_depth D:/Crag/backend  # re-ingest and compare to ground truth
python -m scripts.verify_depth --check backend  # check what is already stored
python -m scripts.reingest_folders --apply      # re-scan every folder source
```

`verify_depth` walks the tree with the ingest's own ignore rules and asks the graph the same
three questions — how many folders, how many files, how deep — plus a **zero-gap chain
check**: every folder at depth N hangs off exactly one folder at depth N−1.

`reingest_folders` is the repair path for folders ingested before direct graph writes were
flushed. Those trees reached disk only partially, and their code symbols — written last —
not at all.

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **No per-file checksum** (`ponytail:`) | A re-scan re-reads and rewrites every file. Node ids are paths so it rewrites in place; add `sha256` when skipping unchanged files is worth the read. |
| **Deleted files are never removed** | Re-ingest upserts everything it finds; a file deleted from disk keeps its node, its edges and its symbols forever. Only deleting and re-adding the whole folder clears them. |
| **No progress reporting** | The response says `documents_pending: N` and nothing updates it. The Source node's `status` flips server-side, but the frontend never polls. |
| **Path-based only** | No upload variant, by design. |
| **Ignore rules are not `.gitignore`** | No negation (`!keep.log`), no anchoring (`/dist` vs `dist`), no directory-only (`build/`). A `.gitignore` copied into `.kbignore` will behave differently. |
| **Only the root's `.kbignore` is read** | Nested ones are ignored. |
| **`_index_documents` is sequential** | Documents are ingested one at a time; the LLM pipeline is the bottleneck either way, but nothing overlaps. |
| **A detached task dies with the process** | A restart mid-pass leaves the Source node on `processing` forever. `POST /knowledge-base/reprocess` resumes LightRAG's queue but does not fix the status. |
| **Audio is classified as `video`** | Deliberate — both are timed media — but the label is misleading until a transcription hook exists. |
| **Symlinks are followed implicitly** | `os.walk` doesn't follow directory symlinks by default, but a symlinked *file* is read and hashed as though it were real. A loop through file symlinks isn't detected. |
| **Two folders with the same basename collide** | Node ids are `<name>/…`, and `name` defaults to the basename. Scanning `a/src` and `b/src` without explicit names overwrites one tree with the other. |
| **`_background` is process-local** | Multiple uvicorn workers each have their own set; `wait_for_documents()` only sees its own. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [`.kbignore`](../GLOSSARY.md#kbignore),
  [Document leaf](../GLOSSARY.md#document-leaf),
  [Detached document pass](../GLOSSARY.md#detached-document-pass),
  [Durability point](../GLOSSARY.md#durability-point),
  [Source Supernode](../GLOSSARY.md#source-supernode), [Label](../GLOSSARY.md#label)
- [`BACKEND.md`](../BACKEND.md) —
  [`POST /api/v1/ingest/folder`](../BACKEND.md#post-apiv1ingestfolder),
  [walkthrough 7.3](../BACKEND.md#73-a-user-scans-the-folder-dcragbackend),
  [`delete_document`](../BACKEND.md#delete_documentdoc_id),
  [`verify_depth` and `reingest_folders`](../BACKEND.md#operational-scripts)
- [`code-intelligence-agent`](code-intelligence-agent.md) — step 9, the symbol pass
- [`entity-extraction-agent`](entity-extraction-agent.md) — what runs on each document leaf
- [`FRONTEND.md`](../FRONTEND.md#folderinput-oningested) — the form that calls this, and
  [the hop view](../FRONTEND.md#graphexplorer) that's the only way to see a deep tree
- [`agents/README.md`](README.md)
