# Documentation

Technical documentation for **nodeRels**, a hybrid vector + knowledge-graph RAG application,
and for **Deck Studio**, the standalone deck-generation app that lives alongside it in
`Agents/`.

> **Generated 30 August 2026**, against the working tree as it stood that day. See
> [Keeping these current](#keeping-these-current).

- [Start here](#start-here)
- [What's in this folder](#whats-in-this-folder)
- [Suggested reading order](#suggested-reading-order)
- [How every document is organised](#how-every-document-is-organised)
- [What the system actually is](#what-the-system-actually-is)
- [Not documented here](#not-documented-here)
- [Keeping these current](#keeping-these-current)

---

## Start here

**Never seen this project before?** Read
[BACKEND.md Chapter 1](BACKEND.md#1-overview) — one page, and it gives you the whole mental
model. Then [FRONTEND.md Chapter 1](FRONTEND.md#1-overview) for what the user actually sees.

**Looking for a specific term?** [GLOSSARY.md](GLOSSARY.md). Every term is defined once,
there, and the other documents link back to it.

**Trying to change something?** Find the component in
[What's in this folder](#whats-in-this-folder), read its Chapter 5 (the map) and then its
Chapter 9 (what's already known to be wrong). Chapter 9 is the one people skip and shouldn't.

**Trying to understand why something is the way it is?** The design decisions are mostly in
Chapter 5 of each document, and most of them are load-bearing responses to a specific failure.

---

## What's in this folder

```
docs/
├── README.md              ← you are here
├── GLOSSARY.md            Shared vocabulary. Defined once, linked from everywhere.
├── BACKEND.md             The FastAPI service. The central document.
├── FRONTEND.md            The Next.js dashboard.
├── EXTENSION.md           The Chrome clipper.
└── agents/
    ├── README.md          Index of the 12 orchestration components, and why they're 12
    │
    ├── ── nodeRels backend orchestration ──
    ├── query-orchestrator.md        How a question gets routed and answered
    ├── llm-router.md                Which model, and what happens when it says no
    ├── multi-hop-planner.md         Questions that need two searches
    ├── spreadsheet-sql-agent.md     Natural language → validated SQL → exact rows
    ├── entity-extraction-agent.md   Prose → graph, and the name fold that resolves entities
    ├── folder-ingestion-agent.md    A directory tree → the graph
    ├── code-intelligence-agent.md   Source files → a call graph
    ├── grounding-verifier.md        What the answer rests on, and what it doesn't
    │
    └── ── Deck Studio (Agents/A1_pptx) ──
        ├── deck-planner.md          Notes → a deck plan → a .pptx
        ├── deck-assist.md           "Ask AI" — operations, never state
        ├── image-engine.md          A licensed, correctly-shaped photo per slide
        └── visual-coverage.md       The document model, the trust boundary, the repair pass
```

| Document | Covers | Length |
|---|---|---|
| [GLOSSARY.md](GLOSSARY.md) | ~90 terms across 8 groups | Reference |
| [BACKEND.md](BACKEND.md) | `backend/` — 13 endpoints, 24 services, 6 parsers, 7 ops scripts | Long |
| [FRONTEND.md](FRONTEND.md) | `frontend/` — 4 pages, 20 components, the typed API client | Long |
| [EXTENSION.md](EXTENSION.md) | `extension/` — MV3 popup, in-tab extraction, 3 endpoints | Medium |
| [agents/](agents/README.md) | 12 components in 2 families | 12 documents |

---

## Suggested reading order

### For a new engineer (about an hour)

1. **[BACKEND.md §1–2](BACKEND.md#1-overview)** — what the system does and what it's built
   from.
2. **[FRONTEND.md §1–2](FRONTEND.md#1-overview)** — what the user sees.
3. **[BACKEND.md §4](BACKEND.md#4-how-this-fits-into-the-bigger-picture)** — the endpoint map.
   This is the contract everything else is written against.
4. **[agents/README.md](agents/README.md)** — the twelve components, one paragraph each, and
   the two families they fall into.
5. **[BACKEND.md §7](BACKEND.md#7-end-to-end-walkthroughs)** — three real traces: a PDF
   upload, a chat question, a folder scan. If you read only one section after §1, read this
   one; walkthroughs build understanding that a reference chapter can't.
6. Then whichever agent document covers what you're about to touch.

### For a stakeholder (about fifteen minutes)

1. **[BACKEND.md §1](BACKEND.md#1-overview)**
2. **[FRONTEND.md §1](FRONTEND.md#1-overview)**
3. **[agents/README.md](agents/README.md)** — skim the twelve paragraphs and the
   [cross-family comparison](agents/README.md#cross-family-comparison) at the end.

### For someone debugging

1. The relevant Chapter 9 — **first**. It is honest about what's known to be broken, and the
   thing you're chasing may already be there.
2. That document's Chapter 6 for the exact contract.
3. Its Chapter 7 for the trace through the system.
4. Its Chapter 8 for the log lines and self-checks that make it observable.

---

## How every document is organised

Every document uses the **same ten chapters, in the same order**. Learn to navigate one and
you can navigate all of them.

| # | Chapter | What's in it |
|---|---|---|
| 1 | Overview | One page. Correct on its own, even if incomplete. |
| 2 | Tech Stack | What it's built from, and *why* each choice — from the real dependency files. |
| 3 | Folder & File Structure | A real directory tree with a line per meaningful file. |
| 4 | How This Fits Into the Bigger Picture | What it calls, what calls it, and the contracts between. |
| 5 | Core Concepts & Key Components | The map, and the design decisions. **Most of the "why" lives here.** |
| 6 | Function & Component Reference | The detailed reference: inputs, outputs, real examples, gotchas. |
| 7 | End-to-End Walkthroughs | 2–4 real scenarios traced through actual function calls, in order. |
| 8 | Configuration & Setup | Environment variables, how to run it, how to test it. |
| 9 | Known Limitations & Open TODOs | Honest and current. Includes every `ponytail:` marker in the source. |
| 10 | See Also | Cross-links to the glossary and sibling documents. |

**Two conventions worth knowing:**

- **Examples are real.** Every input/output example in a Chapter 6 was constructed by reading
  the function body, and many are lifted directly from the code's own tests and self-checks.
  Where an example couldn't be determined without running the code, the entry says so.
- **`ponytail:` markers are surfaced.** The codebase marks deliberate simplifications with a
  `ponytail:` comment naming the ceiling and the upgrade path. Every one of them appears in a
  Chapter 9, tagged. They are acknowledged trade-offs, not oversights.

---

## What the system actually is

Two independent applications in one repository.

### nodeRels — the knowledge base

```
   frontend/  (Next.js :3000)        extension/  (Chrome MV3)
        │                                   │
        └──────────── HTTP / SSE ───────────┘
                          │
                   backend/  (FastAPI :8000)
                          │
        ┌─────────┬───────┴────────┬──────────┐
     Qdrant   NetworkX          DuckDB     SQLite
   dense +    property        worksheet   manifest
   sparse       graph           tables    + chat
        └──────── all local files under backend/storage/ ────────┘
```

Feed it a PDF, a spreadsheet, a web article, a YouTube video, a folder of source code or a
block of text. It builds two things at once: searchable text chunks and a knowledge graph of
the entities and relationships inside them. Ask it a question and it searches both, streams
the answer, and shows you exactly what the answer was built from.

Everything runs locally by default — local embeddings, local entity extraction, local
reranking, an embedded vector store with no server. Only *answering* needs a network, and even
that falls back through three providers.

### Deck Studio — `Agents/A1_pptx/`

A separate application with its own FastAPI backend, its own React editor, and its own `.env`.
Notes in, deck out, then edit every box, word and colour in a browser and export a real
`.pptx`. It shares no code, no storage and no process with nodeRels.

---

## Not documented here

Things in the repository that these documents deliberately don't cover, and why:

| Path | What it is | Why not |
|---|---|---|
| `lightrag_hybrid/` | Four orphan files from an earlier design | **Dead code** — nothing imports it. The live vector store is `backend/app/services/qdrant_store.py`. Noted in [BACKEND.md §9](BACKEND.md#9-known-limitations--open-todos). |
| `rag-fullstack-toolkit/` | A Claude Code plugin — five skill markdown files | Tooling for building this, not part of it |
| `.serena/`, `.claude/`, `frontend/AGENTS.md`, `frontend/CLAUDE.md` | AI-assistant tooling config | Not application code |
| `PLAN.md` | The v2 storage/retrieval rebuild plan | Historically useful, and **fully implemented** — referenced from [BACKEND.md §1](BACKEND.md#1-overview) |
| `logo.png`, `newlogo.jpeg`, `Use-this-as-reference.png`, `scripts/gen_logo_assets.py` | Brand assets and the script that sizes them | Covered in one line in [FRONTEND.md §3](FRONTEND.md#3-folder--file-structure) |
| `README.md` (repo root), `extension/README.md`, `Agents/A1_pptx/README.md` | The project's own user-facing READMEs | These documents complement rather than replace them: the READMEs tell you how to run it, these tell you how it works |
| `backend/uvicorn*.log`, `frontend/nextdev.log` | Committed run logs | Noise; noted in the relevant Chapter 9 |

**One naming note.** The project renamed itself from **Crag** to **nodeRels**. The repository
directory is still `D:\Crag`, and a DuckDB metadata table carries a `_crag_columns` →
`_node_rels_columns` rename migration. Nothing is broken; it's just confusing on a first read.

---

## Keeping these current

**Documentation drifts from code by default.** These documents reflect the codebase as of
**30 August 2026**.

The right way to keep them current is to **re-run the relevant chapter or document through
the same generation process after a significant change to that component** — not to hand-edit
small drifts indefinitely until the whole document is stale enough to distrust.

Concretely:

| You changed | Re-generate |
|---|---|
| An API endpoint's shape | BACKEND.md §6 + §4, and the calling section of FRONTEND.md or EXTENSION.md §4 |
| A service's public functions | BACKEND.md §6, and that component's agent document |
| A component's decision logic | That agent document's §5, §6 and §7 |
| A dependency | The relevant §2, and §8 if it's configurable |
| The ontology (`ENTITY_LABELS`) | GLOSSARY.md, `entity-extraction-agent.md` §8, and FRONTEND.md's symbol table |
| Anything with a new `ponytail:` marker | That document's §9 |

**Signals a document has gone stale:**

- A Chapter 3 tree lists a file that no longer exists, or misses one that does.
- A Chapter 6 example's field names don't match the current response.
- A Chapter 9 entry describes a limitation that has since been fixed.
- A `ponytail:` marker exists in the source but appears in no Chapter 9.

Chapter 9 going *stale in the optimistic direction* — listing something already fixed — is the
most damaging kind, because it's the chapter people trust to be pessimistic.

There is deliberately **no automated freshness check** in this repository. That was a
considered non-goal: a CI job that fails on documentation drift trains people to satisfy the
job rather than the reader.

---

## See also

- [GLOSSARY.md](GLOSSARY.md) — the shared vocabulary
- [BACKEND.md](BACKEND.md) · [FRONTEND.md](FRONTEND.md) · [EXTENSION.md](EXTENSION.md)
- [agents/README.md](agents/README.md) — the twelve orchestration components
- The repository's own `README.md`, `extension/README.md` and `Agents/A1_pptx/README.md` for
  installation and usage
