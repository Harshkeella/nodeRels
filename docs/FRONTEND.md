# Frontend

> The Next.js dashboard in `frontend/`. A landing page, a knowledge-base manager, a chat
> interface and an interactive graph explorer. It holds no state the backend doesn't
> already own.

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

The frontend is a four-surface Next.js application. A **landing page** explains what the
product is. A **Knowledge Base** page lets you add content four ways — drop files, scan a
server-side folder, paste a URL, paste raw text — and shows an inventory of everything
you've added. A **Chat** page is a full-width conversation with your knowledge base:
answers stream token by token, sources arrive as clickable chips, and each chip opens a
panel showing the exact chain of evidence the answer was built from. A **Graph Explorer**
renders the knowledge graph as an interactive force layout you navigate by expanding one
node at a time.

Two design decisions shape most of the code. First, **the backend is the only source of
truth** — there is no client-side cache, no optimistic model of the graph, and the only
global store is a list of in-flight uploads. Second, **the graph explorer is a hop
explorer, not a viewer**: it starts from the source nodes alone and you double-click your
way down, because the whole-graph endpoint is a degree-capped search whose horizon a deep
tree simply lives past.

Everything else follows from those. The components are mostly small and mostly presentation;
the two files that carry real logic are `lib/api.ts` (the typed client, including the SSE
parser) and `components/graph/graph-explorer.tsx` (the hop state machine).

---

## 2. Tech Stack

Read off `frontend/package.json`.

### Framework

| Package | Version | Why this one |
|---|---|---|
| `next` | 16.2.11 | App Router. Server Components by default, so the landing page and the dashboard shell ship no JavaScript for their own layout — only the interactive leaves are `"use client"`. `next/dynamic` with `ssr: false` is what keeps the WebGL hero and the force-graph canvas off the server. |
| `react` / `react-dom` | 19.2.4 | |
| `typescript` | ^5 | `strict: true`. The `@/*` path alias maps to `src/*`. |

> **Note:** `frontend/AGENTS.md` carries a standing warning that this Next.js version has
> breaking changes relative to older documentation, and points at
> `node_modules/next/dist/docs/` as the authority.

### Styling and components

| Package | Version | Why this one |
|---|---|---|
| `tailwindcss` | ^4 | Configured entirely in CSS (`src/app/globals.css` — `@import "tailwindcss"`, `@theme inline`, `@custom-variant dark`). There is no `tailwind.config.js`; that's Tailwind 4's model, not an omission. |
| `shadcn` + `@base-ui/react` | ^4.14.1 / ^1.6.0 | `components.json` sets style `base-nova`, base colour `neutral`, CSS variables on. Components are **copied into `src/components/ui/`**, not imported from a package — so they're editable, and there's no upstream to break. Base UI provides the unstyled accessible primitives underneath. |
| `class-variance-authority` | ^0.7.1 | Variant definitions for `Button`, `Badge`. |
| `clsx` + `tailwind-merge` | ^2.1.1 / ^3.6.0 | The `cn()` helper — merge conditional classes and let later Tailwind utilities win over earlier ones. |
| `tw-animate-css` | ^1.4.0 | Animation utilities. |
| `lucide-react` | ^1.26.0 | Icons. Used two ways: as React components in the legend and panels, and — unusually — as **raw geometry** (`__iconNode` from `lucide-react/dist/esm/icons/*.mjs`) imported into `constants/symbols.ts` so the canvas renderer can rasterize the same glyph the legend renders. That's what guarantees a node's icon can never diverge from its legend entry. |

### Feature libraries

| Package | Version | Why this one |
|---|---|---|
| `react-force-graph-2d` / `-3d` | ^1.29.1 | The graph canvas. 2D is the default; 3D is a toggle. Both are loaded via `next/dynamic({ ssr: false })` — they touch `window` at module scope. |
| `three` | ^0.185.1 | Peer dependency of `react-force-graph-3d`. |
| `react-markdown` + `remark-gfm` + `rehype-highlight` + `highlight.js` | | Assistant message rendering: GitHub-flavoured Markdown (tables, strikethrough) with syntax-highlighted code blocks. |
| `zustand` | ^5.0.14 | One store, holding the in-flight upload list and the document inventory. Chosen over Context because uploads are written from four sibling components and read by a fifth, and prop-drilling that through the page is worse than a 50-line store. |
| `@splinetool/react-spline` | ^4.1.0 | The landing page's WebGL galaxy background. Loaded dynamically, gated behind three checks (see [`Hero`](#hero)). |

### Testing

`npm test` runs `node --test --experimental-strip-types "src/**/*.test.ts"` — Node's own
test runner over TypeScript directly, no Jest, no Vitest, no build step. There is exactly
one test file (`lib/session-groups.test.ts`), covering the one piece of pure client logic.

---

## 3. Folder & File Structure

Generated from the repository.

```
frontend/
├── package.json               # Deps and the four scripts: dev, build, start, lint, test
├── next.config.ts             # Empty — no custom config is needed
├── tsconfig.json              # strict; "@/*" -> "./src/*"
├── postcss.config.mjs         # @tailwindcss/postcss
├── eslint.config.mjs          # eslint-config-next
├── components.json            # shadcn config: base-nova style, neutral base, lucide icons
├── .env.local.example         # NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
├── AGENTS.md / CLAUDE.md      # Standing note: this Next.js version has breaking changes
│
├── public/
│   ├── logo.png               # Wordmark used in both headers
│   ├── hero/                  # Self-hosted Spline scene (galaxy.splinecode)
│   └── *.svg                  # Next.js starter assets, unused
│
└── src/
    ├── app/
    │   ├── layout.tsx         # Root layout: Geist fonts, <html>/<body>, metadata
    │   ├── globals.css        # Tailwind 4 entry, theme tokens, dark variant, keyframes
    │   ├── favicon.ico
    │   ├── page.tsx           # "/" — the landing page: <Hero /> + <HowItWorks />
    │   │
    │   └── dashboard/
    │       ├── (shell)/       # Route group: pages that share the header nav
    │       │   ├── layout.tsx     #   header, logo, three nav links, centred main
    │       │   ├── knowledge/page.tsx   # "/dashboard/knowledge"
    │       │   └── graph/page.tsx       # "/dashboard/graph" — thin, renders GraphExplorer
    │       └── chat/page.tsx  # "/dashboard/chat" — OUTSIDE (shell): full-height, own header
    │
    ├── components/
    │   ├── landing/
    │   │   ├── hero.tsx           # Animated title, WebGL background behind three gates
    │   │   └── how-it-works.tsx   # Scroll-revealed three-beat explainer
    │   │
    │   ├── knowledge/
    │   │   ├── dropzone.tsx           # Drag/drop + click-to-browse file upload
    │   │   ├── folder-input.tsx       # Server-side path scan, with an index-documents flag
    │   │   ├── url-input.tsx          # Article / YouTube URL
    │   │   ├── paste-sandbox.tsx      # Title + body raw text
    │   │   ├── upload-status-list.tsx # In-flight uploads with timed stage labels
    │   │   └── inventory-table.tsx    # The document list, with delete and "show in graph"
    │   │
    │   ├── chat/
    │   │   ├── chat-message.tsx       # One bubble: markdown, table, grounding, sources
    │   │   ├── data-result-table.tsx  # A DuckDB result: sort, page, CSV, undo column
    │   │   ├── provenance-panel.tsx   # The evidence chain as a linear timeline
    │   │   └── session-sidebar.tsx    # Grouped session list, rename/delete inline
    │   │
    │   ├── graph/
    │   │   ├── graph-explorer.tsx     # THE PAGE: hop state machine, filters, detail panel
    │   │   ├── force-graph-canvas.tsx # react-force-graph wrapper + the pin-on-expand logic
    │   │   ├── entity-colors.ts       # Frequency-ranked categorical palette, light/dark
    │   │   └── node-icons.ts          # Rasterizes lucide geometry to canvas-blittable images
    │   │
    │   └── ui/                        # shadcn primitives (copied in, editable)
    │       ├── button.tsx  badge.tsx  card.tsx  input.tsx  table.tsx  textarea.tsx
    │       └── galaxy-hero-background.tsx  # Spline wrapper (dynamic, client-only)
    │
    ├── constants/
    │   └── symbols.ts         # THE TAXONOMY: entity_type -> icon + fixed colour.
    │                          #   Single source of truth for legend, canvas and panels.
    │
    ├── lib/
    │   ├── api.ts             # Typed backend client incl. the SSE parser. Every fetch.
    │   ├── session-groups.ts  # Today / Yesterday / Previous 7 Days / Older bucketing
    │   ├── session-groups.test.ts
    │   └── utils.ts           # cn()
    │
    ├── store/
    │   └── knowledge-store.ts # Zustand: uploads + documents
    │
    └── types/
        └── lucide-icon-node.d.ts  # Types the undocumented lucide __iconNode export
```

**Route-group note.** `dashboard/(shell)/` is a Next.js route group — the parenthesised
segment does **not** appear in the URL. `/dashboard/knowledge` and `/dashboard/graph` get
the shared header; `/dashboard/chat` deliberately sits outside it because chat is a
full-height two-column app with its own header and its own sidebar, and the shared
`max-w-6xl` centred `<main>` is the wrong container for it.

### Repo-root assets documented here

- `logo.png`, `newlogo.jpeg` (repo root) and `scripts/gen_logo_assets.py` — the brand
  wordmark and the script that generates the sized variants copied into
  `frontend/public/logo.png` and `extension/icons/`.
- `Use-this-as-reference.png` — a design reference image, not used at runtime.

---

## 4. How This Fits Into the Bigger Picture

The frontend talks to exactly one thing: the FastAPI backend, over `fetch`, through
`src/lib/api.ts`. Nothing else in `src/` calls `fetch` directly.

```
  Browser                                     FastAPI  (NEXT_PUBLIC_API_BASE_URL)
  ───────                                     ────────────────────────────────────
  /dashboard/knowledge
    Dropzone         ──── ingestFiles()  ───▶ POST /api/v1/ingest/file
    FolderInput      ──── ingestFolder() ───▶ POST /api/v1/ingest/folder
    UrlInput         ──── ingestUrl()    ───▶ POST /api/v1/ingest/url
    PasteSandbox     ──── ingestText()   ───▶ POST /api/v1/ingest/text
    InventoryTable   ──── listKnowledgeBase() ─▶ GET    /api/v1/knowledge-base
                     ──── deleteKnowledgeDocument() ─▶ DELETE /api/v1/knowledge-base/{id}

  /dashboard/chat
    ChatPage         ──── streamChat()   ───▶ POST /api/v1/chat/stream   (SSE)
    SessionSidebar   ──── createSession() / listSessions() / listSessionMessages()
                          / renameSession() / deleteSession()
                                             ─▶ /api/v1/chat/sessions…
    DataResultTable  ──── undoComputedColumn() ─▶ DELETE …/spreadsheet/{t}/columns/{c}

  /dashboard/graph
    GraphExplorer    ──── getSources()   ───▶ GET /api/v1/graph/sources   (landing)
                     ──── expandNode()   ───▶ GET /api/v1/graph/expand    (one hop)
                     ──── getGraph()     ───▶ GET /api/v1/graph           (full view)
                     ──── listKnowledgeBase() ─▶ GET /api/v1/knowledge-base (focus picker)
```

Every endpoint above is documented in
[BACKEND.md §6](BACKEND.md#6-function--component-reference).

### The contracts this side depends on

1. **SSE frame types.** `streamChat` switches on `event.type` and ignores unknown types
   silently, so the backend can add a frame without breaking a deployed client. See
   [`POST /api/v1/chat/stream`](BACKEND.md#post-apiv1chatstream).
2. **Edge ids are stable and identical across endpoints.** The backend builds them as the
   lexically-sorted node pair, which is exactly what LightRAG's own subgraph query
   produces — that's what lets `hop()` dedupe an edge arriving once from `/graph` and again
   from an expansion.
3. **`degree` from `/sources` and `/expand` is the *store* degree.** The explorer subtracts
   the drawn edge count from it to compute how many neighbours are still off-canvas, and
   draws a ring when that's non-zero. From `/graph` it's the *drawn* degree, so the
   difference is uniformly zero and no rings appear — which is correct, since the full view
   isn't something you hop through.
4. **`entity_type` is the label, lowercased and separator-stripped** by the backend's
   `canonical_label()`. `constants/symbols.ts` keys on exactly those values, which is why
   `CodeFile` appears there as `codefile`.
5. **`edge_category` tells the canvas how to draw an edge.** The frontend deliberately does
   *not* re-derive meaning from the `keywords` string — it reads the category the backend
   computed. (The single exception is distinguishing `CALLS` from other behavioral edges for
   colour and dash pattern.)

### Cross-references into the sibling documents

| Frontend thing | Backend counterpart |
|---|---|
| The four ingest cards | [Ingest endpoints](BACKEND.md#post-apiv1ingestfile) |
| `TableResult` type | [`spreadsheet-sql-agent`](agents/spreadsheet-sql-agent.md) output |
| `EvidenceSource` / `EvidenceStep` types | [`provenance.build_evidence`](BACKEND.md#query-path-services) |
| `GroundingVerdict` type | [`grounding.check`](agents/grounding-verifier.md) |
| Hop view | [`GET /api/v1/graph/expand`](BACKEND.md#get-apiv1graphexpand) |
| `symbolFor()` keys | [`graph_schema.canonical_label`](BACKEND.md#canonical_labelraw) |

The Chrome extension implements a **subset** of the same chat contract — it handles
`sources`, `token`, `error`, `done` and ignores `evidence`, `table` and `grounding`. See
[EXTENSION.md §4](EXTENSION.md#4-how-this-fits-into-the-bigger-picture).

---

## 5. Core Concepts & Key Components

### `lib/api.ts` — the single network boundary

Every request the app makes lives here, along with a TypeScript interface for every
payload. It exports 18 functions and 14 types. The most interesting part is `streamChat`,
which is a hand-written SSE parser rather than `EventSource` — because `EventSource` can
only issue `GET` requests and the chat endpoint needs a JSON body.

### `store/knowledge-store.ts` — the only global state

A Zustand store with two slices: `uploads` (in-flight ingestion jobs, added by four
components and rendered by a fifth) and `documents` (the inventory, so the page and the
table don't refetch independently). Everything else — sessions, messages, graph state — is
component state, because it belongs to one screen.

### `constants/symbols.ts` — the taxonomy

One table mapping `entity_type` to an icon and, for structural labels, a fixed colour. It
has **two consumers with incompatible needs**: the legend and detail panels want a React
component, while the canvas wants raw geometry it can rasterize. So each entry carries
both — `icon` (a `LucideIcon`) and `iconNode` (lucide's own path data). Taking both from
lucide rather than hand-copying one is what makes it impossible for a node's glyph to
diverge from its legend entry.

Structural labels (`source`, `folder`, `file`, `codefile`, `class`, `function`, `method`,
`externalsymbol`, `image`, `video`) carry **fixed** light/dark colours. Document and
tabular labels deliberately do not — giving them fixed colours would repaint every graph
that already contains one.

### `components/graph/entity-colors.ts` — the palette

Builds a colour scale per graph load. Types with a fixed colour are pulled out of the
assignment entirely; the rest are ranked by frequency and assigned from an eight-slot
validated categorical palette, with everything beyond that grey. Holding the structural
labels out of the ranking is what keeps existing graphs looking identical after Source
nodes were introduced — document types rank among themselves exactly as they did before,
instead of being pushed down a slot by a supernode that outranks them all.

### `components/graph/graph-explorer.tsx` — the hop state machine

The largest component (~810 lines) and the only one with real logic. It holds the loaded
graph, the set of expanded node ids, the current view mode, the search and filter state,
and the selection. Its central operation is `hop()`: fetch one node's neighbours, place the
arrivals on a golden-angle arc around their origin, and merge them in — deduping against
whatever state actually holds *now*, since two expansions can be in flight at once.

### `components/graph/force-graph-canvas.tsx` — the renderer

Wraps `react-force-graph` and owns two things the explorer can't: the custom canvas paint
(the hidden-neighbour ring, the rasterized glyph, the label) and `useSettle`, which pins
every already-placed node before an expansion so the settled map doesn't rearrange itself
under the user.

### The chat surface

`chat/page.tsx` is the shell — sessions, streaming, the auto-growing composer, scroll
management. `ChatMessageBubble` renders one message and everything hanging off it.
`ProvenancePanel` is a slide-over showing one source's evidence chain as a linear
timeline. `DataResultTable` renders a DuckDB result with client-side sorting, paging, CSV
export and an undo button for a column added through chat.

---

## 6. Function & Component Reference

### `lib/api.ts`

---

#### `streamChat(message, history, handlers, signal?, sessionId?)`

**What it does:** Sends a question to the backend and dispatches each Server-Sent Event
frame to the matching callback as it arrives. This is how the answer appears token by
token instead of all at once.

**Input:**

| Param | Type | Example |
|---|---|---|
| `message` | `string` | `"What is the leave policy?"` |
| `history` | `ChatHistoryMessage[]` | `[{role: "user", content: "Hi"}]` |
| `handlers` | `ChatStreamHandlers` | see below |
| `signal` | `AbortSignal?` | from an `AbortController`, for the Stop button |
| `sessionId` | `string \| null?` | `"3f2a…"`; omit and nothing is persisted |

`ChatStreamHandlers` — every callback optional:
`onSources`, `onEvidence`, `onGrounding`, `onTable`, `onToken`, `onError`, `onDone`.

**Output:** `Promise<void>`. Everything of interest arrives through the callbacks.

**Example:**
```ts
await streamChat("What is the leave policy?", [], {
  onSources: (s) => console.log(s),
  // => [{ reference_id: "1", file_path: "handbook.pdf" }]
  onToken:   (t) => (buffer += t),
  onDone:    () => console.log(buffer),
  // => "Employees accrue 20 days of annual leave per year."
}, controller.signal, sessionId);
```

**Notes:** Hand-written rather than `EventSource`, because `EventSource` is GET-only and
this needs a JSON body. The parser buffers on `"\n\n"` and keeps the trailing partial
frame, so a frame split across two TCP reads still parses. A non-OK response calls
`onError` and returns rather than throwing — the only thing that escapes is the
`AbortError` from `signal`, which the caller is expected to recognise. Unknown
`event.type` values fall through every branch and are silently ignored, which is what makes
adding a frame type on the backend a non-breaking change.

---

#### `listKnowledgeBase()`

**What it does:** Fetches the document inventory.

**Input:** none. **Output:** `Promise<KnowledgeDocument[]>`.

**Example:**
```ts
await listKnowledgeBase();
// => [{ doc_id: "doc-4f1c…", file_name: "handbook.pdf", source_type: "pdf",
//       chunk_count: 37, size_bytes: 148203,
//       date_added: "2026-08-30T09:14:22.918431+00:00" }]
```

**Notes:** Throws `Error(detail)` on a non-OK response, using the backend's
`{"detail": …}` body when there is one and `res.statusText` otherwise — that's
`parseErrorDetail`, which every function here shares.

---

#### `ingestFiles(files)`

**What it does:** Uploads one or more files as `multipart/form-data`.

**Input:** `files: File[]`.

**Output:** `Promise<IngestFileResponse>` — `{ results: IngestResult[], errors: {file_name, error}[] }`.

**Example:**
```ts
await ingestFiles([pdfFile]);
// => { results: [{ doc_id: "doc-4f1c…", file_name: "handbook.pdf",
//                  source_type: "pdf", chunk_count: 37, size_bytes: 148203,
//                  date_added: "2026-…", deduped: false }],
//      errors: [] }
```

**Notes:** Every file goes under the same `files` form key — that's what the backend's
`list[UploadFile]` expects. The response is HTTP 200 even when every file failed, so the
caller must check `errors`.

---

#### `ingestFolder({ path, name?, indexDocuments? })`

**What it does:** Asks the backend to scan a directory **on the server's own disk**.

**Input:** `{ path: string; name?: string; indexDocuments?: boolean }` — `indexDocuments`
defaults to `true`.

**Output:** `Promise<FolderIngestResult>`.

**Example:**
```ts
await ingestFolder({ path: "D:/Crag/backend", indexDocuments: true });
// => { name: "backend", path: "D:\\Crag\\backend", folders: 9, files: 61,
//      code_files: 58, classes: 12, functions: 214, methods: 63, calls: 486,
//      external_symbols: 41, unresolved: 130,
//      documents_indexed: 0, documents_pending: 2, status: "processing",
//      errors: [], … }
```

**Notes:** `documents_indexed` is **always 0** in this response — the tree is committed
first and the documents are indexed detached. `status` and `documents_pending` are what
tell you more is coming, which is why `FolderInput` phrases its success message as "N
document(s) still indexing" rather than "done".

---

#### `ingestUrl(url)` / `ingestText(text, title?)`

Both `POST` a JSON body and return `Promise<IngestResult>`, throwing on non-OK.

```ts
await ingestUrl("https://youtu.be/dQw4w9WgXcQ");
// => { file_name: "YouTube: dQw4w9WgXcQ", source_type: "youtube", … }

await ingestText("Employees accrue 20 days…", "Leave policy");
// => { file_name: "Leave policy", source_type: "paste", … }
```

**Notes:** `ingestText` here never sends `source_type` — the dashboard's pastes are always
plain pastes. The extension sends `"article_clipper"` on the same endpoint; see
[EXTENSION.md](EXTENSION.md#ingesttexttext-title-sourcetype).

---

#### `deleteKnowledgeDocument(docId)` / `undoComputedColumn(table, column)`

Both `DELETE`, both return `Promise<void>`, both throw on non-OK. All path segments go
through `encodeURIComponent`, which matters because file names become document ids'
neighbours in the UI and table names come from user data.

---

#### Session functions

| Function | Method | Returns |
|---|---|---|
| `createSession()` | POST `/chat/sessions` | `ChatSession` |
| `listSessions()` | GET `/chat/sessions` | `ChatSession[]` (most recently used first) |
| `listSessionMessages(sessionId)` | GET `…/{id}/messages` | `StoredMessage[]` with decoded `evidence` |
| `renameSession(sessionId, title)` | PATCH `…/{id}` | `ChatSession` |
| `deleteSession(sessionId)` | DELETE `…/{id}` | `void` |

---

#### `getGraph(params?)` / `getSources()` / `expandNode(nodeId)`

**What they do:** The three graph reads.

| Function | Endpoint | Use |
|---|---|---|
| `getSources()` | `/graph/sources` | The hop view's landing state: every Source node, no edges |
| `expandNode(nodeId)` | `/graph/expand?node_id=…` | One hop out, both directions |
| `getGraph({label?, maxDepth?, maxNodes?})` | `/graph?…` | The full, degree-capped view |

All three return `Promise<Graph>` = `{ nodes: GraphNode[], edges: GraphEdge[], is_truncated: boolean }`.

**Example:**
```ts
await getSources();
// => { nodes: [{ id: "source:handbook.pdf", entity_type: "source",
//                source_type: "pdf", degree: 22, … }],
//      edges: [], is_truncated: false }

await expandNode("proj/src/app.py::main");
// => { nodes: [{ id: "proj/src/util.py::helper", entity_type: "function",
//                calls_in_count: 1, calls_out_count: 0, degree: 2, … }],
//      edges: [{ id: "proj/src/app.py::main-proj/src/util.py::helper",
//                source: "proj/src/app.py::main",
//                target: "proj/src/util.py::helper",
//                keywords: "CALLS", edge_category: "behavioral", … }],
//      is_truncated: false }
```

**Notes:** `getGraph` builds its query string with `URLSearchParams` and omits any param
that's falsy, so the backend's own defaults apply. The doc comment on `getSources`
explains why the hop view exists at all — the whole-graph load is a degree-capped BFS, so
everything past its horizon is unreachable no matter how far you zoom.

---

### `store/knowledge-store.ts`

#### `useKnowledgeStore`

**What it is:** The app's only global store.

**State:** `uploads: UploadItem[]`, `documents: KnowledgeDocument[]`.

**Actions:**

| Action | Effect |
|---|---|
| `addUpload(item)` | Prepends — newest first |
| `updateUpload(id, patch)` | Shallow-merges into the matching item |
| `clearFinishedUploads()` | Drops `done` and `error` rows (defined; not currently called) |
| `setDocuments(docs)` | Replaces the inventory |
| `removeDocument(docId)` | Filters one out, for an optimistic delete |

**Example:**
```ts
const id = crypto.randomUUID();
addUpload({ id, label: "handbook.pdf", status: "processing" });
// … await ingestFiles([file])
updateUpload(id, { status: "done" });
```

`UploadStatus` is `"pending" | "processing" | "done" | "error"`.

---

### `lib/session-groups.ts`

#### `groupByRecency(items, now?)`

**What it does:** Buckets anything with an `updated_at` ISO timestamp into Today /
Yesterday / Previous 7 Days / Older, dropping empty buckets and preserving the input order
within each.

**Input:** `items: T[]` where `T extends { updated_at: string }`; `now?: number`
(defaults to `Date.now()`, injectable so the test is deterministic).

**Output:** `Grouped<T>[]` = `{ name: GroupName; items: T[] }[]`.

**Example:** (from `session-groups.test.ts`)
```ts
groupByRecency([
  { updated_at: "2026-08-26T13:59:00.000Z" },  // a minute ago
  { updated_at: "2026-08-25T01:00:00.000Z" },  // yesterday
  { updated_at: "2026-08-22T14:00:00.000Z" },  // 4 days ago
  { updated_at: "2026-05-28T14:00:00.000Z" },  // 90 days ago
], NOW);
// => [{name: "Today", items: [...]}, {name: "Yesterday", items: [...]},
//     {name: "Previous 7 Days", items: [...]}, {name: "Older", items: [...]}]
```

**Notes:** This lives on the client, not the API, precisely because the buckets are
relative to the **viewer's** midnight — a server that grouped them would bake one timezone
into the response. An unparseable timestamp lands in "Older" rather than vanishing from
the sidebar. "Today" and "Yesterday" are measured from local midnight; "Previous 7 Days"
is measured from `now` (a rolling 7×24h window), which is a deliberate difference.

---

### `lib/utils.ts`

#### `cn(...inputs)`

`twMerge(clsx(inputs))` — resolves conditional classes, then lets a later Tailwind utility
override an earlier one in the same group (`px-2 px-4` → `px-4`). Used everywhere a
component composes its own classes with a caller's.

---

### `constants/symbols.ts`

#### `symbolFor(entityType, sourceType?)`

**What it does:** Returns the icon (and fixed colour, if any) for a node type.

**Input:**

| Param | Type | Example |
|---|---|---|
| `entityType` | `string \| null \| undefined` | `"codefile"` |
| `sourceType` | `string \| null?` | `"folder"` — only read when `entityType === "source"` |

**Output:** `SymbolSpec | null` = `{ key, label, icon, iconNode, light?, dark? }`.

**Example:**
```ts
symbolFor("codefile");
// => { key: "file-code", label: "Code file", icon: FileCode,
//      iconNode: [...], light: "#0d7a7a", dark: "#2fb8b8" }

symbolFor("source", "folder");
// => { key: "supernode-folder", label: "Folder source", icon: FolderCog,
//      iconNode: [...], light: "#1b4f9c", dark: "#6aa9f5" }

symbolFor("person");     // => icon User, no fixed colour
symbolFor("mystery");    // => null
```

**Notes:** Source nodes are the one two-level lookup: the label says it's a source, and
`source_type` picks which of five variants to draw (folder / web / media / pdf / doc).
An unrecognised `source_type` falls back to the `doc` variant rather than null, so a
supernode always has a glyph. The key `codefile` (not `CodeFile`) is not a typo — the
backend's `canonical_label()` lowercases and strips separators before writing.

---

#### `fixedColorFor(entityType, isDark)`

Returns the type's fixed hex colour, or `null` if it belongs to the frequency-ranked
scale. Used only by `entity-colors.ts` to partition the ranking.

```ts
fixedColorFor("class", false);   // => "#4a3aa7"
fixedColorFor("person", false);  // => null  (frequency-scaled)
```

---

#### `CODE_ENTITY_TYPES`

A `Set` of the labels the "Code only" filter keeps:
`source, folder, codefile, class, function, method, externalsymbol`. Mirrors the backend's
`graph_schema.CODE_LABELS` — with `source` added, because filtering the supernodes out of
the hop view would leave a blank canvas with nothing to double-click.

---

### `components/graph/entity-colors.ts`

#### `buildEntityColorScale(nodes)`

**What it does:** Builds the colour assignment for one graph load.

**Input:** `nodes: GraphNode[]`.

**Output:** `EntityColorScale` = `{ colorFor(entityType), legend, otherColor }`.

**Example:**
```ts
const scale = buildEntityColorScale(graph.nodes);
scale.colorFor("class");         // => "#4a3aa7"  (fixed)
scale.colorFor("person");        // => "#2a78d6"  (slot 0 — the most frequent scaled type)
scale.colorFor("unknown");       // => "#898781"  (the "other" grey)
scale.legend;
// => [{ type: "source", color: "#1b4f9c" }, { type: "person", color: "#2a78d6" }, …]
```

**Notes:** Assignment happens **once per load and is then held stable**, so filtering or
hovering never repaints a type's colour. Types whose raw value is `""`, `unknown`,
`other`, `none` or `null` are excluded from the ranking entirely and always get grey — an
unresolved type shouldn't consume a palette slot. There are eight slots; a ninth scaled
type gets grey and the legend grows an "Other" entry. Light and dark palettes are separate
arrays in a **fixed hue order that must not be reordered or cycled**.

---

### `components/graph/node-icons.ts`

#### `iconImage(key, iconNode)`

**What it does:** Returns a rasterized `HTMLImageElement` for a lucide icon, cached by key,
or `null` until it has decoded.

**Notes:** Canvas can't render a React component. It *can* render an image, so each icon is
serialized to an SVG data URI once — white stroke at width 2.6, thicker than lucide's
default 2 because at 12px on a coloured disc a hairline outline disappears — and blitted
with one `drawImage` per node. Rasterizing rather than stroking path-by-path with `Path2D`
matters when the force layout repaints hundreds of nodes per frame.

#### `warmIconCache()`

Decodes every icon up front so the first paint already has them. Called from a
`useEffect` in `GraphExplorer`. No-ops on the server.

**Constants:** `ICON_MIN_RADIUS_PX = 7` and `ICON_MIN_ZOOM = 1.1` — below either, glyphs
are suppressed, because hundreds of overlapping icons at default zoom is more clutter than
the dots they replaced.

---

### Knowledge components

All four ingest cards take the same single prop, `{ onIngested: () => void }`, and follow
the same shape: add an upload row, call the API, update the row, call `onIngested()` on
success. They're documented together because the differences are the interesting part.

---

#### `Dropzone onIngested`

**What it does:** A drag-and-drop / click-to-browse card for file uploads.

**Notes:** Two hidden `<input type="file">` elements — one normal, one with
`webkitdirectory` for the "or select a folder" link. Both post through `ingestFiles`, so
the folder picker here really is a multi-file upload, **not** the server-side folder scan
that [`FolderInput`](#folderinput-oningested) does. Upload rows are matched back to their
files **by name**, which is why two files with the same name in one batch would confuse the
status list. Accepts `.pdf, .md, .markdown, .txt, .xlsx, .xlsm, .csv`.

---

#### `FolderInput onIngested`

**What it does:** Takes a filesystem path and asks the backend to scan it, with a checkbox
for whether to also index documents found inside.

**Notes:** Path-based on purpose, and the component says so in its own doc comment: a
`webkitdirectory` picker would re-post every byte of a repo through multipart just to
rebuild a tree the backend can already see. The success label reports the real outcome —
`"backend — 61 files, 214 functions, 2 document(s) still indexing"` — rather than claiming
completion the backend hasn't reached.

---

#### `UrlInput onIngested`

An `<input type="url">` plus a submit button. YouTube-vs-article detection happens
server-side, so this component doesn't care which it got.

---

#### `PasteSandbox onIngested`

An optional title plus a required 5-row textarea. When no title is given, the upload row is
labelled with the first 40 characters of the text (the *backend* separately uses the first
60 as the file name).

---

#### `UploadStatusList`

**What it does:** Renders every in-flight and finished upload from the store, with a status
badge and an animated sweep while work is in flight.

**Input:** none — reads `uploads` from the Zustand store directly.

**Notes:** The stage labels ("Scraping the page…", "Cleaning the article…", "Chunking +
embedding…", "Building the knowledge graph…") are **timed, not measured** — 4 seconds
each, holding on the last one until the request actually returns. Ingestion is a single
request with no server-side progress events, and the component's own comment says so.
There is deliberately **no percentage bar**, because a percentage would be a lie in a way a
sequence of honest stage names isn't. Marked `ponytail:` — swap the timer for real SSE
stages if ingest ever streams progress.

---

#### `InventoryTable documents isLoading onDelete`

**What it does:** The document list — name, source type with its icon, chunk count, size,
date, and two actions per row.

**Input:**

| Prop | Type |
|---|---|
| `documents` | `KnowledgeDocument[]` |
| `isLoading` | `boolean` |
| `onDelete` | `(docId: string) => Promise<void>` |

**Notes:** Delete uses a native `window.confirm()`, and the message is scoped to the source
type — deleting a folder says "and every folder, file and code symbol under it", because
one click really does cascade hundreds of nodes. Native `confirm` rather than a modal
component is a deliberate call: a dependency and three files for a yes/no question. The
source-type badge pulls its icon from `symbolFor("source", doc.source_type)` — the *same*
registry the graph legend reads, so a row and its supernode can never disagree about what
they are. The "show in graph" link deep-links to
`/dashboard/graph?focus=source%3A<file_name>`.

---

### Chat components

---

#### `ChatMessageBubble message onOpenEvidence`

**What it does:** Renders one message. A user message is a right-aligned bubble with
preserved whitespace; an assistant message is full-width and can carry, in order: a data
table, Markdown prose, an error, a grounding warning, and a collapsible source list.

**Input:**

| Prop | Type |
|---|---|
| `message` | `ChatMessage` |
| `onOpenEvidence` | `(evidence: EvidenceSource) => void` |

`ChatMessage` = `{ id, role, content, sources?, evidence?, grounding?, table?, error?, streaming? }`.

**Notes worth knowing before you touch it:**

- The `code` renderer distinguishes inline code from a fenced block by looking for `hljs`
  or `language-` in the className — a fenced block's `<code>` arrives with a language class
  from the highlighter and must keep its own styling.
- The copy button reads the rendered text **straight off the DOM**
  (`parentElement.querySelector("code").textContent`), because reconstructing it from React
  children means walking the highlighter's element tree. Clipboard access is
  permission-gated and absent over plain HTTP, so the failure is swallowed and the button
  simply does nothing.
- The grounding warning is shown, not hidden — the answer has already streamed, so the
  honest move is to say which parts the evidence didn't cover.
- A source chip only gets its provenance button **when there's a chain to show**, so the
  button is never a promise the panel can't keep.

---

#### `ProvenancePanel evidence onClose`

**What it does:** A right-hand slide-over showing one source's evidence chain as a vertical
timeline: `Source document → Matched text → Entity → Relationship → Answer`.

**Input:** `evidence: EvidenceSource`, `onClose: () => void`.

**Notes:** Deliberately **a chain, not a graph**. The doc comment makes the argument: the
force layout is the wrong tool at three-to-a-dozen nodes — a physics sim at that scale
jitters into a shape that says nothing — whereas a chain reads in one direction, holds
still, and *the direction is the claim*. Icons and colours come from `constants/symbols`,
the same table the canvas reads, so an entity is the same colour here as in the explorer.
Entities and both relationship endpoints deep-link into the graph via the existing
`?focus=` parameter rather than reimplementing exploration. Escape closes; focus moves into
the panel on open so a keyboard user isn't stranded behind it.

---

#### `DataResultTable result`

**What it does:** Renders a spreadsheet answer — the exact rows DuckDB returned — with
client-side sorting, 50-row paging, CSV download, an SQL disclosure, and an undo button
when the answer added a column.

**Input:** `result: TableResult` = `{ columns, rows, total_row_count, truncated, sql?, table?, added_column? }`.

**Notes:** Sorting is stable-ish and type-aware: numbers compare numerically, everything
else by `localeCompare`, and nulls always sort last regardless of direction. Undo is
optimistic in a specific way — after `undoComputedColumn` succeeds, the column is hidden
from the rendered table *and* from the CSV export by index, without refetching. All
comparison and export work is memoised on `result.rows`, which matters because the table
can hold 500 rows and re-sorts on every header click.

---

#### `SessionSidebar {...props}`

**What it does:** The chat session list — grouped by recency, with inline rename and
delete, a "New chat" button, a collapse toggle, and a link to the knowledge base.

**Input:** `sessions`, `activeId`, `collapsed`, `mobileOpen`, `onToggleCollapsed`,
`onCloseMobile`, `onNewChat`, `onSelect`, `onRename`, `onDelete`.

**Notes:** Three layouts from one component: an icon rail when collapsed (which keeps the
two controls that matter reachable without expanding), an overlay drawer below `md`, and an
in-flow column at `md` and up. Row actions are revealed on hover **and on
`group-focus-within`** — actions that only appear on hover are unreachable by keyboard
otherwise. Rename commits on Enter or the check button and reverts on Escape.

---

### Graph components

---

#### `GraphExplorer`

**What it does:** The whole `/dashboard/graph` page. Loads the graph, renders the canvas,
and owns every interaction: hop expansion, search, the code-only filter, the 2D/3D toggle,
the focus picker, and the selection detail panel.

**Input:** none. It reads `?focus=` off `window.location` on mount.

**Two view modes, and the difference matters:**

| Mode | Loads | Purpose |
|---|---|---|
| `hop` (default) | `getSources()` — Source nodes only, no edges | The landing state. Double-click your way down. **This is the only view that can reach a deep tree.** |
| `full` | `getGraph({ label: focus, maxNodes: 300, maxDepth: focus === "*" ? 3 : 4 })` | The dense overview, one button away. |

**Key internal functions:**

| Function | What it does |
|---|---|
| `hop(nodeId)` | The core operation — see below |
| `handleNodeClick(id)` | Selects on single click; times a 400 ms window to detect a double click and hop. `react-force-graph` exposes only `onNodeClick` and `onNodeRightClick`, so the double-click is timed here rather than handed to it. |
| `hiddenCounts` | `node.degree − (edges currently drawn on it)`, per node. Answerable without a round trip because `/sources` and `/expand` report the true store degree. |
| `view` | `graph`, or `graph` filtered to `CODE_ENTITY_TYPES` when "Code only" is on. Semantic edges are dropped with the entities they connected — an edge to a node that's no longer drawn is a line into empty space. |
| `trace` | For a selected code node: its `CALLS` edges split into callers and callees. Direction comes off the edge (`rel_from`/`rel_to` resolved by the backend), so this is two filters rather than a traversal. |
| `dimmed` / `dimmedLinks` | What to grey out. A trace wins over the search dim — it's the more specific thing the user just asked for. |

**`hop(nodeId)` in detail:**

1. If the ring already says this node has nothing left (`hiddenCounts.get(nodeId) === 0` in
   hop mode), show a notice and return. Answering from what's on screen beats a round trip
   that can only confirm it.
2. `expandNode(nodeId)`.
3. Filter arrivals to nodes not already on screen — a node reached twice by two paths is
   still one node; only the new edge is added.
4. Place each arrival on an arc around its origin at `HOP_RADIUS = 72` graph units, spaced
   by the **golden angle** (`π(3−√5)`). Even spacing makes the ring read as "these came
   from here"; the golden angle keeps a second expansion of the same node off the first
   one's spokes.
5. Merge with `setGraph(current => …)`, re-deduping against **whatever state actually holds
   now** rather than the snapshot from step 2 — two expansions can be in flight at once.
6. Bump `settle`, which tells the canvas to pin everything already placed.

**Notes:** A failed or empty expansion sets a local `notice` rather than the page-level
`error`, because raising `error` replaced the whole explorer — losing an exploration twenty
hops deep because one fetch failed. An `info` notice expires after 4 seconds; an `error`
one doesn't, because an error is something to read and act on. `focus` is read straight off
`window.location.search` rather than with `useSearchParams`, which would force the whole
page under a Suspense boundary for no benefit.

---

#### `ForceGraphCanvas {...props}`

**What it does:** Wraps `react-force-graph-2d` / `-3d`, applies the colour and width rules,
and paints the custom canvas layer.

**Input:** `mode`, `graphData`, `width`, `height`, `selectedId`, `colorFor`, `hiddenFor?`,
`settle?`, `dimmed`, `dimmedLinks?`, `onNodeClick`, `onBackgroundClick`, `graphRef`.

**The one thing to know before editing it:** the nodes handed to this canvas are **the
graph's own node objects, not copies**. `react-force-graph` stores each node's simulated
`x`/`y` *on the object it was given*, so copying them per render throws the layout away and
every hop would reset the whole view. That's why colour is looked up through `colorFor`
rather than baked onto the node.

**Edge styling, driven by `edge_category`:**

| Category | Colour | Width | Arrow | Dash |
|---|---|---|---|---|
| `behavioral`, `CALLS` | `#6366f1` | 2 | yes | solid |
| `behavioral`, other | `#8b8ad6` | 1.5 | yes | `[4,3]` |
| everything else | theme grey | `min(weight, 4)` | no | solid |

Structural edges are containment and read fine without arrows; a call has a direction and
is useless without one.

**Custom canvas paint** (`nodeCanvasObjectMode: "after"`), per node:
1. A **ring** if it still has neighbours off-canvas. When the ring is gone the node is
   exhausted — without it, hopping is guesswork and every leaf reads as a broken control.
2. The **glyph**, gated on apparent size (`ICON_MIN_ZOOM`, `ICON_MIN_RADIUS_PX`). A focused
   node always draws its icon so you can identify what you're pointing at in a dense
   cluster.
3. The **label**, above `globalScale` 1.2 or when focused.

---

#### `useSettle(nodes, settle, onSettled)`

**What it does:** Pins every node that already has a position before an expansion, so one
hop only moves the nodes it revealed. Returns a release function wired to `onEngineStop`.

**Notes:** This is the single thing that makes progressive exploration usable. d3-force
reheats the whole simulation whenever the data changes — left alone, every hop throws the
settled graph back into motion and the map you just built rearranges itself under you.
Pinned, the solver has only the arrivals left to place, and by the time `onEngineStop`
releases them alpha is spent, so letting go moves nothing.

It **must** be a layout effect, and the comment explains exactly why: react-kapsule pushes
changed props into the kapsule *during render*, so d3 has already been handed the new nodes
and told to restart before any effect runs. What saves it is that d3-force schedules ticks
on d3-timer, so the first tick lands on the next animation frame — and a layout effect is
still inside the same commit. A plain `useEffect` would *usually* make it, and "usually" is
not something to build a layout on. It falls back to `useEffect` on the server, where there
is no layout to be had.

`onSettled` additionally re-centres the expanded node — but only when it has drifted into
the outer 20% of the viewport, because recentring a node already comfortably in view is the
camera moving for its own sake.

---

### Landing components

---

#### `Hero`

**What it does:** The landing page's masthead: an animated title, a subtitle, a call to
action, and — conditionally — a WebGL galaxy background.

**Notes:** The Spline background is gated behind **three** checks, all evaluated on the
client via `useSyncExternalStore` so they re-evaluate on resize and on motion-preference
change:

1. `prefers-reduced-motion` is not set,
2. the viewport is at least `48rem` wide,
3. WebGL is actually available (probed by creating a canvas and asking for a context).

Phones fall back to the static hero rather than downloading a multi-megabyte WebGL runtime
and rendering a continuous scene — a decorative background hasn't earned that much data or
battery. The server **always** renders the static version; the client upgrades if the
machine can afford it.

---

#### `GalaxyHeroBackground`

The Spline wrapper. The scene is **self-hosted from `public/hero/`** rather than pulled
from `prod.spline.design`, so a CDN outage or a deleted scene can't blank the hero. The
scene is authored as glowing particles on opaque black, which a white page can't show
through — so it's rendered under `invert(1) hue-rotate(180deg)`: the invert turns glow into
ink on paper, and the paired hue-rotate puts the magenta/violet back where a bare invert
would have swung it to green. It fades in over 1.2s on `onLoad` rather than popping.

---

#### `HowItWorks`

Three scroll-revealed "beats" explaining ingestion, the graph and chat. `useReveal()` is a
small `IntersectionObserver` hook that flips a flag once at a 0.35 threshold and never
resets, so scrolling back up doesn't replay the animation.

---

### UI primitives (`components/ui/`)

Standard shadcn components, copied in rather than imported, built on `@base-ui/react`
primitives with `class-variance-authority` variants.

| Component | Variants / notes |
|---|---|
| `Button` | `variant`: default, outline, secondary, ghost, destructive, link. `size`: default, xs, sm, lg, icon, icon-xs, icon-sm, icon-lg. Also exports `buttonVariants` for styling a `<Link>` as a button. |
| `Badge` | `variant`: default, secondary, destructive, outline, ghost, link. Uses `useRender`/`mergeProps` so it can render as any element. |
| `Card` | `Card`, `CardHeader`, `CardTitle`, `CardContent` |
| `Input`, `Textarea` | Styled form controls |
| `Table` | `Table`, `TableHeader`, `TableBody`, `TableRow`, `TableHead`, `TableCell` |

---

## 7. End-to-End Walkthroughs

### 7.1 A user drags `handbook.pdf` onto the Knowledge page

1. `Dropzone`'s `onDrop` fires → `handleFiles(e.dataTransfer.files)`.
2. A `crypto.randomUUID()` is minted per file and `addUpload({ id, label: "handbook.pdf",
   status: "processing" })` pushes a row into the Zustand store.
3. `UploadStatusList` re-renders. `UploadRow` mounts, `useStage(true)` starts a 4-second
   interval, and the row shows "Scraping the page…" with a sweeping gradient behind it.
4. `api.ingestFiles([file])` builds a `FormData` with one `files` part and `POST`s to
   `/api/v1/ingest/file`. See
   [BACKEND.md §7.1](BACKEND.md#71-a-user-uploads-handbookpdf) for what happens server-side.
5. Meanwhile the stage label advances every 4s and holds on "Building the knowledge
   graph…" until the response lands.
6. Response arrives. Each `result` is matched back to its file **by name** and
   `updateUpload(id, { status: "done" })` flips the badge to "Indexed successfully". Each
   `error` becomes a red badge carrying the backend's message.
7. `onIngested()` → the page's `refresh()` → `listKnowledgeBase()` →
   `setDocuments(...)` → `InventoryTable` re-renders with the new row, its badge icon
   chosen by `symbolFor("source", "pdf")`.

---

### 7.2 A user asks a question and inspects where the answer came from

1. The composer's `<form onSubmit>` (or Enter without Shift) calls `send(input)`.
2. `ensureSession()` — if there's no active session, `createSession()` mints one and
   prepends it to the sidebar. A failure here returns `null` and chat proceeds
   **unsaved**: persistence is best-effort, not a prerequisite.
3. Two messages are appended immediately — the user's, and an empty assistant message with
   `streaming: true`. The bubble renders "Thinking…".
4. `streamChat(trimmed, history, handlers, controller.signal, sessionId)`.
5. Frames arrive and each patches the assistant message by id:
   - `sources` → `patchMessage(id, { sources })` → the collapsible source list appears.
   - `evidence` → `{ evidence }` → each source chip that has a chain grows a route button.
   - `token` (many) → appended to a local `buffer`, then `{ content: buffer }` → React
     re-renders the Markdown. The `useEffect` on `[messages, atBottom]` scrolls to the
     bottom — **only if the user was already there**, so scrolling up to read mid-answer
     isn't yanked back down.
   - `grounding` → `{ grounding }` → the amber "N of M statements not backed by the
     retrieved sources" panel appears above the sources.
   - `done` → `{ streaming: false }`.
6. `finally` → `refreshSessions()`, because the title is generated server-side on the first
   exchange and the sidebar only learns it after the turn lands.
7. The user clicks a source chip's route icon → `setOpenEvidence(chain)` →
   `<ProvenancePanel>` slides in and renders `source → chunk → entity → relationship →
   Answer` down a continuous spine.
8. Clicking an entity in the panel navigates to
   `/dashboard/graph?focus=<encoded id>`, and `GraphExplorer` reads that off
   `window.location.search` on mount.

---

### 7.3 A user explores a scanned repository in the graph

1. `/dashboard/graph` renders `<GraphExplorer />`. `viewMode` is `"hop"`, so the load
   effect calls `getSources()`. A second effect calls `listKnowledgeBase()` to populate the
   focus picker; a third calls `warmIconCache()` so the first paint already has glyphs.
2. The canvas shows one node per ingestion. Every one wears a ring (`hiddenCounts` is its
   full store degree, since no edges are drawn). A hint reads "3 sources — double-click one
   to open it".
3. Double-click `source:backend` → `handleNodeClick` sees two clicks inside 400 ms and
   calls `hop("source:backend")` → `expandNode(...)` → one arrival, the folder root
   `backend`, placed 72 units away.
4. `settle` bumps. `useSettle`'s layout effect pins the Source node at its current `x`/`y`
   before d3 can reheat, so the folder lands beside it and nothing else moves.
5. Double-click down through `backend` → `backend/app` → `backend/app/services` →
   `services/code_intel.py`. Each hop pins everything placed and arcs only the arrivals.
   The hint disappears after the first expansion — it taught the gesture and retired.
6. Double-click `code_intel.py` → its `DEFINES` edges bring in `plan_calls`, `project`,
   `extract`, and the `Symbol`/`Call`/`FileSymbols` classes.
7. Click "Code only" → `view` drops every non-code node and every `semantic` edge. What's
   left is the call graph, uncluttered by people, dates and organizations.
8. Click `project` → the detail panel shows its signature and `qualified_name`. `trace`
   computes callers and callees from the `CALLS` edges' resolved direction, so the panel
   lists "Calls (12)" and "Called by (1)". Everything outside that neighbourhood dims;
   `dimmedLinks` keeps only the call edges lit.
9. The panel's counts are the **stored project-wide totals** (`calls_out_count` /
   `calls_in_count`), while the lists show what's in the loaded subgraph — so the header can
   legitimately read a bigger number than the list has entries.
10. "Expand N more" on the panel calls the same `hop()`; when the ring is gone the button
    reads "Fully expanded" and is disabled.

---

## 8. Configuration & Setup

### Running it

```bash
cd frontend
npm install
cp .env.local.example .env.local     # then edit if the backend isn't on :8000
npm run dev                          # http://localhost:3000
```

The backend must be running and must list `http://localhost:3000` in its `CORS_ORIGINS`
(it does by default).

### Scripts

| Script | Command |
|---|---|
| `dev` | `next dev` |
| `build` | `next build` |
| `start` | `next start` (serves the production build) |
| `lint` | `eslint` |
| `test` | `node --test --experimental-strip-types "src/**/*.test.ts"` |

### Environment

One variable, and it must be `NEXT_PUBLIC_`-prefixed because it's read in the browser:

| Variable | Default | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Trailing slashes are stripped by `api.ts`. Baked in at **build** time — changing it needs a rebuild, not a restart. |

### Theming

`src/app/globals.css` holds the whole theme: Tailwind 4's `@import`, an `@theme inline`
block mapping design tokens to CSS variables, `@custom-variant dark (&:is(.dark *))`, and
the `sweep` keyframes the upload rows use. Dark mode is driven by a `.dark` class on
`<html>`; several components read it imperatively with
`document.documentElement.classList.contains("dark")` because a canvas can't use a CSS
variable. **There is no theme toggle in the UI** — see §9.

### Fonts

`Geist` and `Geist_Mono` via `next/font/google`, exposed as `--font-geist-sans` and
`--font-geist-mono` and bound to Tailwind's `--font-sans` / `--font-mono` in `globals.css`.

---

## 9. Known Limitations & Open TODOs

### Acknowledged ceilings (each marked `ponytail:` in the source)

| Where | Limitation | The upgrade path |
|---|---|---|
| `upload-status-list.tsx` | Stage labels are on a **4-second timer**, not measured. A slow ingest sits on "Building the knowledge graph…" indefinitely; a fast one flashes through stages it never really performed. | Real SSE progress stages from the ingest endpoint. |
| `node-icons.ts` | Icon decoding is **async** and the first frames after mount may skip the glyph. Data URIs decode in a frame or two while the layout is still settling, so it's never visible in practice. | `createImageBitmap` + an explicit await, if a static graph ever renders before the cache is warm. |

### Functional gaps

- **No dark-mode toggle.** The entire theme, the palette in `entity-colors.ts` and the
  fixed colours in `symbols.ts` all support dark mode, and several components read the
  `.dark` class — but nothing in the UI ever sets it. Dark mode is currently unreachable.
- **No polling for folder-ingest completion.** `POST /ingest/folder` returns
  `status: "processing"` with `documents_pending: N`, and the Source node's status flips to
  `completed` server-side, but the frontend never checks. You find out by refreshing.
- **`clearFinishedUploads()` is defined and never called.** The upload list grows for the
  life of the page.
- **Uploads are matched to results by file name.** Two files with the same name in one
  batch would cross their status rows.
- **No pagination or search on the inventory table.** Fine at tens of documents; a thousand
  would render as a thousand rows.
- **`GraphExplorer` reads `?focus=` once, on mount.** Navigating from the provenance panel
  to the graph when the graph is *already* mounted won't re-focus it.
- **Search dims rather than filters**, and only matches on `node.id` — not on
  `description`, `qualified_name` or `file_path`.
- **The 3D mode is feature-poor** relative to 2D: no custom node paint, so no rings, no
  glyphs and no labels beyond the hover tooltip.
- **No error boundary.** A render error in the graph canvas takes the page down.
- **Accessibility gaps in the canvas.** The force graph is not keyboard-navigable and has
  no text alternative; the rest of the app is reasonable (aria-labels throughout,
  `focus-within` reveals, `role="status"` with `aria-live` on hop notices).

### Housekeeping

- `public/file.svg`, `globe.svg`, `next.svg`, `vercel.svg`, `window.svg` are Next.js starter
  assets and are unused.
- `next.config.ts` is an empty config object.
- `frontend/nextdev.log` is a committed dev-server log.
- `tsconfig.tsbuildinfo` is committed; it's a build cache.
- Only one test file exists. The pure, testable logic that isn't covered:
  `buildEntityColorScale`, `symbolFor`, and `hop`'s dedupe/merge.

---

## 10. See Also

- [`docs/GLOSSARY.md`](GLOSSARY.md) — every term used here
- [`docs/BACKEND.md`](BACKEND.md) — every endpoint this app calls
  - [§4 How This Fits](BACKEND.md#4-how-this-fits-into-the-bigger-picture) — the reverse
    view of the table in §4 above
  - [§6 Function & Component Reference](BACKEND.md#6-function--component-reference) — the
    endpoint contracts
- [`docs/EXTENSION.md`](EXTENSION.md) — the Chrome clipper, which reimplements a subset of
  `lib/api.ts` against the same endpoints
- [`docs/agents/README.md`](agents/README.md) — what produces the `TableResult`,
  `EvidenceSource` and `GroundingVerdict` payloads this app renders
- [`docs/README.md`](README.md) — index and suggested reading order
