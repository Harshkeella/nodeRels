# Agent: Code Intelligence Agent

> `backend/app/services/code_intel.py` — classes, functions, methods, imports, inheritance
> and who-calls-whom, extracted from a scanned repository and written into the graph.

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

When a folder scan finds source files, this agent parses them and hangs their structure off
the file nodes:

```
(:CodeFile) -[:DEFINES]->        (:Class | :Function)
(:Class)    -[:DEFINES_METHOD]-> (:Method)
(:Class)    -[:INHERITS]->       (:Class)
(:Function | :Method) -[:CALLS]-> (:Function | :Method)
(:CodeFile) -[:IMPORTS]->        (:CodeFile)
```

The result is a call graph you can navigate: click a function in the Graph Explorer and see
what it calls and what calls it, with the true project-wide counts.

The design is defined by what it **refuses** to do. Call resolution is by name, scoped
file-first then project-wide, and **only when exactly one candidate matches**. Zero or
several candidates means the call is recorded as unresolved rather than wired to a guess —
because a wrong `CALLS` edge is worse than a missing one, since nothing downstream can tell
it's wrong. There is no type inference, no import aliasing, no method dispatch on receiver
type. Two functions named `save` in one project make every bare `save()` unresolvable, and
that is the correct outcome.

Two parser backends behind one interface. Python uses the standard library's `ast` — no
dependency, no grammar download, correct by construction for the language this backend is
written in. Everything else uses tree-sitter, whose grammars are an **optional** install:
without them, code files are still ingested as `CodeFile` leaves and simply carry no
symbols. Nothing raises.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **`ast`** (stdlib) | Python parsing. Zero install, and `ast.unparse` gives a real signature string for free. |
| **`tree-sitter-language-pack`** (optional) | 100+ grammars. A new language is a row in a node-type set plus its grammar, not new walking code. |
| **`builtins`** (stdlib) | `frozenset(dir(builtins))`. A node per unique builtin would wire half the codebase to itself and drown the call graph it exists to make readable. |
| **`dataclasses`** (stdlib) | `Call`, `Symbol`, `FileSymbols`, `CallEdge`. |
| **`graph_schema`** | Every write goes through the validated `upsert_node`/`upsert_edge`, same as prose entities. |

No LSP, no compiler, no language server. 842 lines.

---

## 3. Folder & File Structure

```
backend/app/services/
├── code_intel.py              # 842 lines
│   ├── _BUILTINS
│   ├── _TS_CLASS / _TS_FUNCTION / _TS_METHOD / _TS_ARROW   # node types by role
│   │
│   ├── @dataclass Call        # name, line, root (the receiver) — .dotted
│   ├── @dataclass Symbol      # kind, name, qualified_name, lines, signature,
│   │                          #   bases, class_qualified_name, calls, implements
│   ├── @dataclass FileSymbols # symbols, imports, imported{name -> module}
│   ├── @dataclass CallEdge    # caller, target, line, resolved, confidence, count
│   │
│   ├── ── Python: stdlib ast ──
│   │   _called_names(node) / _extract_python(source) / _dotted(node)
│   ├── ── Everything else: tree-sitter ──
│   │   _parser_for(language) / _text / _named_child / _ts_calls
│   │   _extract_tree_sitter(source, language)
│   ├── extract(source, language)        # THE PARSER ENTRY — never raises
│   │
│   ├── ── Resolution and writing ──
│   │   symbol_node(file_node, qualified_name)   # "<file>::<qname>"
│   │   external_node(name)                      # "external:<dotted>"
│   │   _import_target(spec, file_rel, by_rel_path)
│   │   build_index(parsed)              # name -> [every node answering to it]
│   │   resolve_call(name, local, index) # THE REFUSAL
│   │   is_external_symbol(call, imported)
│   │   plan_calls(parsed, index)        # resolve everything BEFORE writing
│   │   project(rag, parsed, file_meta, source_name, doc_id)   # THE WRITE
│   └── __main__ self-check
│
└── test_code_intel.py         # 215 lines
```

---

## 4. How This Fits Into the Bigger Picture

Called from exactly one place, at a specific moment:

```python
# folder_ingest.ingest_folder — AFTER every file node exists, BEFORE the flush
parsed = {node: code_intel.extract(text, by_id[node]["language"])
          for node, text in sources.items()}
parsed = {k: v for k, v in parsed.items() if v.symbols or v.imports}
code_counts = await code_intel.project(rag, parsed, {k: by_id[k] for k in parsed},
                                       name, doc_id)
```

**Why last:** a call in the first file can land in the last, so the whole tree has to be
parsed before any `CALLS` edge can be resolved — and `calls_in_count` isn't knowable until
every edge is.

**Consumed by:**

| Consumer | Uses |
|---|---|
| [`GET /graph/expand`](../BACKEND.md#get-apiv1graphexpand) | Returns a symbol's neighbours with `rel_from`/`rel_to` intact |
| [Graph Explorer `trace`](../FRONTEND.md#graphexplorer) | Splits `CALLS` edges into callers and callees — two filters, not a traversal, because direction is on the edge |
| Graph Explorer "Code only" | Filters to `CODE_ENTITY_TYPES`, mirroring `gs.CODE_LABELS` |
| Detail panel | `signature`, `qualified_name`, `calls_in_count`, `calls_out_count` |

---

## 5. Core Concepts & Key Components

### Resolution refuses more than it accepts

```python
def resolve_call(name, local, index):
    for candidates in (local.get(name, []), index.get(name, [])):
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return None       # ambiguous in this scope; an outer scope cannot fix it
    return None
```

Both scopes hold a **list**, and both require exactly one entry. The comment explains why a
dict would be a bug: a `name -> node` map would silently let the last definition win, so a
file with `Base.save` and `Child.save` would resolve every bare `save()` to whichever class
was parsed second — an edge indistinguishable from a correct one and wrong half the time.

Ambiguity in the inner scope returns `None` immediately rather than falling through to the
outer one. If a name is ambiguous *here*, a project-wide match is not the answer.

### Confidence records how much of a guess an edge is

| Value | Meaning |
|---|---|
| `1.0` | File-local resolution — a certainty |
| `0.8` | Project-wide unique name — a strong guess a same-named import could fool |
| `0.0` | `ExternalSymbol` — known to be outside the tree |

Stored on the `CALLS` edge alongside `call_site_line`, `call_count` and `resolved`.

### `ExternalSymbol` exists so an unresolved call is still visible

The rule: a call gets an `ExternalSymbol` node **only if it's traceable to an import**.

```python
def is_external_symbol(call, imported):
    if call.root is not None:
        return call.root in imported          # httpx.get() where httpx was imported
    return call.name in imported and call.name not in _BUILTINS
```

`httpx.get()` where `httpx` was imported is a real library call worth seeing. `len()`,
`items()`, `self.save()` are builtins and methods on locals — a node per unique one of those
would add hundreds of hubs like `len` and `append` wired to half the codebase, making the
call graph **less** readable, not more.

Anything that fails this test goes into `calls_unresolved` on the caller node as a
comma-joined string. Kept because "calls something named X we could not place" is real
information — but it's no longer where calls go to disappear.

### Only module-level definitions and class methods become nodes

A closure or a callback is **not** its own node; its calls are attributed to the enclosing
function. Otherwise a React file yields a node per inline arrow function and the graph is
unreadable. This is one of three ceilings the module's docstring states up front, alongside
name-based resolution and the absence of type inference — *"that is the Hybrid-LSP layer
this does not have, and it is where to start if these edges ever need to be authoritative."*

### Direction is data on the edge

The graph store is undirected NetworkX: it keeps the pair, not the order. For `RELATED_TO`
that never mattered. For `CALLS` it is the **entire content** of the edge — "A calls B" read
back as "B calls A" is not a weaker answer, it's a wrong one. So `graph_schema.upsert_edge`
writes `rel_from` and `rel_to` as properties, where the store cannot lose them, and the graph
API resolves them back on read.

### Counts are computed before anything is written

`plan_calls` runs first and returns every edge, so `out_count` and `in_count` can be totalled
across the whole project before the first node is upserted. These are the **true
project-wide** numbers. They can't be derived from the loaded subgraph, which is usually
truncated — counting its edges would under-report "who calls this".

Repeated calls to the same target collapse into one edge (the store keeps one edge per pair):
the first line is kept and `call_count` records the repeats.

### Code symbols are graph-only

`upsert_node(..., index=False)` for every symbol and every `ExternalSymbol`. A repo
contributes thousands of these, and indexing them would bury the documents under symbol
cards in the entity vector store. Only the file-level nodes above them get a card.

### Import resolution tries hard, then gives up cleanly

`_import_target` resolves in three passes: relative specifiers against the importing file's
directory (with extension and `/index` candidates), dotted module paths against the tree's
own paths (root-relative first, then relative to the importing file), and finally a **unique
path suffix** — trying progressively shorter tails of the module path.

That last pass exists because a scan almost never starts at the `sys.path` root: point it at
`backend/app` and every `from app.services.x import y` has a leading component no file path
under it can match. Longest tail first so the most specific answer wins, and **a tail
matching more than one file resolves to nothing** — a coin flip is not an import edge.

---

## 6. Function & Component Reference

---

### `extract(source, language)`

**What it does:** One file's symbols. Dispatches to `ast` for Python and tree-sitter for
everything else. **Never raises.**

**Input:** `source: str`, `language: str` (from `folder_ingest.CODE_LANGUAGES`).

**Output:** `FileSymbols` — `symbols`, `imports`, `imported`.

**Example:**
```python
extract('''
import httpx
from app.services import manifest

class Base:
    def save(self): pass

def helper(): return 1

def main():
    helper()
    httpx.get("http://x")
''', "python")
# => FileSymbols(
#      symbols=[Symbol(kind="class",    name="Base",   qualified_name="Base",   bases=[]),
#               Symbol(kind="method",   name="save",   qualified_name="Base.save",
#                      signature="save(self)", class_qualified_name="Base"),
#               Symbol(kind="function", name="helper", qualified_name="helper",
#                      signature="helper()"),
#               Symbol(kind="function", name="main",   qualified_name="main",
#                      signature="main()",
#                      calls=[Call("helper", 11), Call("get", 12, root="httpx")])],
#      imports=["httpx", "app.services"],
#      imported={"httpx": "httpx", "manifest": "app.services"})
```

**Notes:** Catches `SyntaxError`, `ValueError` and `RecursionError` and returns an empty
`FileSymbols` — **one syntax error must not abandon a folder ingest**. A missing tree-sitter
grammar is handled inside `_parser_for` and produces the same empty result.

---

### `plan_calls(parsed, index)`

**What it does:** Resolves every call site across the whole project **before** anything is
written.

**Input:** `parsed: dict[str, FileSymbols]`, `index: dict[str, list[str]]` from
`build_index`.

**Output:** `(edges, externals, unresolved)`:

| Return | Type | Meaning |
|---|---|---|
| `edges` | `list[CallEdge]` | Deduped per `(caller, target)`, with counts |
| `externals` | `dict[str, str]` | `external:httpx.get` → `"httpx"` (module guess) |
| `unresolved` | `dict[str, list[str]]` | caller node → sorted dotted names that couldn't be placed |

**Example:**
```python
edges, externals, unresolved = plan_calls(parsed, build_index(parsed))
# edges     => [CallEdge(caller="proj/app.py::main", target="proj/util.py::helper",
#                        line=11, resolved=True, confidence=0.8, count=1)]
# externals => {"external:httpx.get": "httpx"}
# unresolved=> {"proj/app.py::main": ["len"]}
```

**Notes:** Self-recursion is skipped — `target == caller` would be a self-loop. `local` is
built per file from non-class symbols, so a class name never shadows a function name.

---

### `resolve_call(name, local, index)` / `build_index(parsed)` / `is_external_symbol(call, imported)`

```python
build_index({"proj/util.py": FileSymbols(symbols=[
    Symbol(kind="function", name="helper", qualified_name="helper", …)])})
# => {"helper": ["proj/util.py::helper"]}      classes are excluded

resolve_call("helper", local={}, index={"helper": ["proj/util.py::helper"]})
# => "proj/util.py::helper"
resolve_call("save", local={"save": ["a::Base.save", "a::Child.save"]}, index={})
# => None       ambiguous — not a guess
resolve_call("nope", {}, {})
# => None

is_external_symbol(Call("get", 12, root="httpx"), {"httpx": "httpx"})   # => True
is_external_symbol(Call("len", 3), {})                                  # => False
is_external_symbol(Call("save", 4, root="self"), {})                    # => False
```

`build_index` **excludes classes** deliberately: a call to a class name is a constructor, and
resolving it to the class node would make every instantiation look like a function call.

---

### `project(rag, parsed, file_meta, source_name, doc_id)`

**What it does:** Writes every file's symbols and every edge between them.

**Input:**

| Param | Type | Note |
|---|---|---|
| `parsed` | `dict[str, FileSymbols]` | keyed by **CodeFile node id** |
| `file_meta` | `dict[str, dict]` | same keys; needs `rel_path` and `language` |
| `source_name` | `str` | the folder's name — becomes `file_path` on every node |
| `doc_id` | `str` | becomes `source_id` |

**Output:** `dict` of counts.

**Example:**
```python
await code_intel.project(rag, parsed, file_meta, "backend", "doc-9c2f…")
# => {"classes": 12, "functions": 214, "methods": 63,
#     "calls": 486, "external_symbols": 41, "unresolved": 130}
```

**Write order** (it matters):
1. `plan_calls` — every edge resolved, `out_count`/`in_count` totalled.
2. `ExternalSymbol` nodes, with `calls_in_count` already known.
3. Per file, per symbol: the node (`index=False`) with `qualified_name`, `signature`,
   `start_line`, `end_line`, `language`, both counts, and `calls_unresolved` if any.
4. `DEFINES_METHOD` from the class if it's a method with a known class, else `DEFINES` from
   the file.
5. `INHERITS` / `IMPLEMENTS` per base, matched by the bare tail of a dotted name, within the
   file only.
6. `IMPORTS`, one per unique specifier that `_import_target` resolved.
7. **`CALLS` last**, with `call_site_line`, `call_count`, `resolved`, `confidence`.

**Notes:** `IMPLEMENTS` is only ever emitted for languages that actually have interfaces (TS,
Java). Python has no such concept, so a Python base class is always `INHERITS`. `project`
does **not** flush — `folder_ingest` does, once, after this returns.

---

### `symbol_node(file_node, qualified_name)` / `external_node(name)` / `_import_target(spec, file_rel, by_rel_path)`

```python
symbol_node("proj/src/util.py", "Base.save")   # => "proj/src/util.py::Base.save"
external_node("httpx.get")                      # => "external:httpx.get"

by_rel = {"src/util.py": "proj/src/util.py", "src/app.py": "proj/src/app.py"}
_import_target("./util",        "src/app.py", by_rel)  # => "proj/src/util.py"
_import_target("app.services.x","src/app.py", by_rel)  # => None  (nothing matches)
```

---

### The dataclasses

| Class | Fields |
|---|---|
| `Call` | `name`, `line`, `root` (the receiver, `None` for a bare call). `.dotted` → `"httpx.get"` or `"helper"` |
| `Symbol` | `kind`, `name`, `qualified_name`, `start_line`, `end_line`, `signature`, `bases`, `class_qualified_name`, `calls`, `implements` |
| `FileSymbols` | `symbols`, `imports`, `imported` (`{bound name: module}`) |
| `CallEdge` | `caller`, `target`, `line`, `resolved`, `confidence`, `count` |

`Call.root` is what lets an unresolved call be tied back to an import instead of guessed at:
`self.save()` and `db.save()` both yield the name `save`, and which receiver it was is
exactly the type information this pass doesn't have.

---

## 7. End-to-End Walkthroughs

### 7.1 A two-file Python project

`src/util.py`:
```python
def helper(): return 1
class Base:
    def save(self): pass
```
`src/app.py`:
```python
import httpx
from util import helper
def main():
    helper()
    httpx.get("http://x")
    len([1])
```

1. `folder_ingest` reads both files, calls `extract(text, "python")` on each.
2. `_extract_python` walks `tree.body` — **module level only**, so a nested `def` inside
   `main` would not become its own node.
   - `util.py`: `helper` (function), `Base` (class), `Base.save` (method).
   - `app.py`: `main` with `calls=[Call("helper", 4), Call("get", 5, root="httpx"),
     Call("len", 6)]`; `imports=["httpx", "util"]`;
     `imported={"httpx": "httpx", "helper": "util"}`.
3. `build_index` → `{"helper": ["proj/src/util.py::helper"], "save": [...], "main": [...]}`.
   `Base` is excluded.
4. `plan_calls` for `main`:
   - `helper` — not in `main`'s local scope; project-wide **one** candidate →
     `confidence=0.8, resolved=True`.
   - `get` — unresolvable, but `root="httpx"` is in `imported` → `external:httpx.get`,
     `confidence=0.0`.
   - `len` — no root, and `"len"` isn't in `imported` → `unresolved["…::main"] = ["len"]`.
     **No node, no edge**, correctly.
5. Counts: `out_count["…::main"] = 2`, `in_count["…util.py::helper"] = 1`,
   `in_count["external:httpx.get"] = 1`.
6. `project` writes: the external node, four symbol nodes (all `index=False`), `DEFINES`
   from each file, `DEFINES_METHOD` from `Base` to `save`, `IMPORTS` from `app.py` to
   `util.py` (via `_import_target("util", "src/app.py", …)`), and two `CALLS` edges.
7. In the Graph Explorer, double-clicking `main` shows `helper` and `httpx.get`. Selecting
   `helper` shows "Called by (1)". Selecting `main` shows `calls_unresolved: "len"` in its
   properties.

---

### 7.2 An ambiguous call, correctly refused

```python
class Base:
    def save(self): ...
class Child:
    def save(self): ...
def run(db):
    db.save()
```

1. `_called_names` yields `Call("save", 6, root="db")`.
2. `local` holds `{"save": ["f::Base.save", "f::Child.save"]}` — a **list**, two entries.
3. `resolve_call`: `len(candidates) == 2`, so the `if candidates: return None` branch fires.
   No project-wide fallback — ambiguity here can't be fixed by an outer scope.
4. `is_external_symbol`: `root="db"`, not in `imported` → `False`.
5. `unresolved["f::run"] = ["db.save"]`. **No `CALLS` edge.**

Knowing which `save` this is requires knowing `db`'s type. That's type inference, which this
pass doesn't do, so it says so rather than picking one and being wrong half the time.

---

### 7.3 A TypeScript file with no grammar installed

1. `folder_ingest.classify(".ts")` → `codefile`, `language="typescript"`. The **file node is
   created normally** with its `loc`, `mime_type` and `size_bytes`.
2. `extract(source, "typescript")` → `_extract_tree_sitter` → `_parser_for("typescript")`
   → `ImportError` (no `tree-sitter-language-pack`) → caught → empty `FileSymbols`.
3. `folder_ingest` filters it out: `if v.symbols or v.imports`. It never reaches `project`.
4. The file is in the graph, findable, containment-linked and countable. It simply has no
   symbols.

`pip install -r requirements-codeintel.txt` and re-run
`python -m scripts.reingest_folders --apply` to fill them in.

---

## 8. Configuration & Setup

**No environment variables.** One optional install:

```bash
pip install -r backend/requirements-codeintel.txt   # tree-sitter-language-pack, pillow
```

**Which languages become `CodeFile`** is `folder_ingest.CODE_LANGUAGES` — 30 extensions
across Python, JS/TS, Java, Go, Rust, Ruby, C/C++, C#, PHP, Swift, Kotlin, Scala, Bash, SQL,
Lua, R, Perl, Vue, Svelte. A file whose extension is listed is a `CodeFile` even with no
parser for that language.

**Degradation, by design:**

| Missing | Effect |
|---|---|
| tree-sitter grammars | Non-Python files: leaf node only, no symbols. Python unaffected. |
| Nothing at all | Python classes/functions/methods/calls work with a bare install. |

### Tests

```bash
cd backend
pytest app/services/test_code_intel.py -v
python -m app.services.code_intel        # self-check on an inline 2-file project
```

The self-check parses a small module with a class, an inheriting subclass, a helper and a
caller, and asserts symbol kinds, qualified names, the resolved edge and the refusals.

### Adding a language

Add its extension to `folder_ingest.CODE_LANGUAGES` with the tree-sitter language string,
and add its node type names to `_TS_CLASS` / `_TS_FUNCTION` / `_TS_METHOD` / `_TS_ARROW` if
they differ from the sets already there. That's it — a new language is rows in a set plus its
grammar, not new walking code.

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **Name-based resolution, no type inference** (`ponytail:`) | `self.save()` and `db.save()` are indistinguishable. An ambiguous name is left unresolved. → a Hybrid-LSP layer. |
| **An import is the only signal for `ExternalSymbol`** (`ponytail:`) | A library call reached through an un-imported alias stays in `calls_unresolved`. |
| **Only module-level and class-method definitions** | Closures, callbacks, inline arrow functions and nested defs are not nodes; their calls are attributed to the enclosing function. |
| **Repeated calls collapse** | One edge per `(caller, target)` because the store keeps one edge per pair. `call_count` records the repeats and `line` keeps the first. |
| **No decorators, type hints, docstrings or comments** | None reach the graph. A `Symbol` carries a signature, not semantics. |
| **`INHERITS` matches within the file only** | `class Child(base_module.Base)` where `Base` is imported from elsewhere gets no edge. |
| **`IMPORTS` resolution has a suffix-matching fallback** that can be wrong | It requires a *unique* match, but a unique wrong match is still wrong. |
| **`calls_unresolved` is a comma-joined string** | Not a list. Anything consuming it has to split on `", "`. |
| **Symbols are graph-only (`index=False`)** | Correct for noise, but it means "which function parses spreadsheets?" can't be answered by retrieval — only by browsing the graph. |
| **No cross-repo resolution** | Two folder ingests are two disjoint symbol namespaces, even for the same code. |
| **Deleted symbols persist across re-ingest** | Same limitation as [`folder-ingestion-agent`](folder-ingestion-agent.md#9-known-limitations--open-todos): a function removed from a file keeps its node. |
| **`_BUILTINS` is Python's** | `frozenset(dir(builtins))` is used to filter unresolved calls in **every** language, so a JS `Array` or a Go `len` is filtered by a Python list. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [Symbol](../GLOSSARY.md#symbol),
  [Call resolution](../GLOSSARY.md#call-resolution),
  [Confidence](../GLOSSARY.md#confidence),
  [`calls_in_count` / `calls_out_count`](../GLOSSARY.md#calls_in_count--calls_out_count),
  [Qualified name](../GLOSSARY.md#qualified-name),
  [ExternalSymbol](../GLOSSARY.md#externalsymbol),
  [`rel_from`/`rel_to`](../GLOSSARY.md#rel_from--rel_to), [tree-sitter](../GLOSSARY.md#tree-sitter)
- [`folder-ingestion-agent`](folder-ingestion-agent.md) — the only caller, and why the symbol
  pass runs last
- [`BACKEND.md`](../BACKEND.md) — [the graph layer](../BACKEND.md#the-graph-layer),
  [`upsert_node`'s `index=False`](../BACKEND.md#upsert_noderag-node_id-label-description-file_path-source_id-keep_existing_labelfalse-indextrue-properties),
  [walkthrough 7.3](../BACKEND.md#73-a-user-scans-the-folder-dcragbackend)
- [`entity-extraction-agent`](entity-extraction-agent.md) — the *probabilistic* counterpart,
  writing through the same chokepoint
- [`FRONTEND.md`](../FRONTEND.md#graphexplorer) — the `trace` view and "Code only" filter
- [`agents/README.md`](README.md)
