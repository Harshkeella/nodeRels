# Agent: Spreadsheet SQL Agent

> `backend/app/services/spreadsheet_query.py` — natural language to validated DuckDB SQL to
> exact rows. The model writes the query; the database does the arithmetic.

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

This agent exists because of one non-negotiable rule: **no cell value is ever answered by a
language model.** A model asked to sum a column will produce a plausible number, and a
plausible number that is wrong is worse than no answer. So when a spreadsheet is ingested
its rows go into DuckDB — a real database with real types — and when you ask a question
about them, the model's only job is to write a `SELECT`. DuckDB executes it and the rows
come back exactly as they are.

Three guardrails stand between the model's output and your data, in order. The statement
must **parse as exactly one `SELECT`**. Every table and column it names must **bind against
the real catalog** — checked with `DESCRIBE`, which resolves names without executing
anything, so a hallucinated column fails before a single row is read. And the result is
**row-capped** before it's serialised.

Two things make it more than a text-to-SQL wrapper. First, **routing is free**: whether a
question is about tabular data at all is decided by retrieval, not by asking a model — the
worksheet and column nodes carry indexed descriptions, so naming a column hits its card.
Second, it **self-heals**: a rejected query is handed back to the model along with the exact
error, up to three attempts, so a misspelled column name is usually fixed on the second try
rather than surfacing as a failure.

It also has one write path — adding a computed column — held to the same standard: never
general SQL, only `ADD COLUMN` plus `UPDATE` with an expression the binder has already
checked, and reversible through a dedicated undo endpoint.

---

## 2. Tech Stack

| Thing | Why |
|---|---|
| **DuckDB** ≥1.1 | An embedded analytical database. Real column types, real aggregates, and — crucially — `extract_statements()` and `DESCRIBE`, which are what make validating generated SQL possible without executing it. |
| **`query_llm_func`** | The SQL writer. Uses the *answering* model rather than the extraction one, because writing correct SQL against a schema is closer to reasoning than to labelling. See [`llm-router`](llm-router.md). |
| **`rag.entities_vdb`** | The router. Querying the entity vector store with the raw question is how "is this about the data?" gets answered without an LLM call. |
| **`re`** (stdlib) | Two patterns: the `ADD COLUMN` command form, and stripping markdown fences a model adds despite being told not to. |

The system prompt is 8 lines. There is no SQL-generation library, no ORM, and no query
builder.

---

## 3. Folder & File Structure

```
backend/app/services/
├── spreadsheet_query.py       # 285 lines
│   ├── _SYSTEM_PROMPT                  # the 3 allowed reply forms
│   ├── _ADD_COLUMN / _FENCE            # regexes
│   ├── SpreadsheetError                # the user-facing exception
│   │
│   ├── relevant_tables(rag, question)  # THE ROUTER — retrieval, no LLM
│   ├── schema_context(tables)          # DDL for the retrieved tables only
│   ├── list_tables()
│   │
│   ├── _validate_select(sql)           # THE GUARD — parse, bind, forbid
│   ├── run_select(sql)                 # validate + execute + cap
│   ├── _cell(value)                    # JSON-safe scalars
│   │
│   ├── add_computed_column(table, column, expression)   # the write path
│   ├── drop_computed_column(table, column)              # its undo
│   │
│   ├── _generate(question, schema, previous_error)      # one LLM call
│   └── answer(question, tables)        # THE ENTRY POINT — the 3-attempt loop
│
├── parsers/spreadsheet.py     # get_connection(), record_columns() — see BACKEND.md
├── tabular_graph.py           # writes the schema cards this agent routes on
└── test_spreadsheet.py        # 236 lines
```

---

## 4. How This Fits Into the Bigger Picture

```
   query-orchestrator (_chat_stream)
        │
        │ 1. tables = await relevant_tables(rag, message)
        ▼
   ┌──────────────────────────────────────────────────────────┐
   │ relevant_tables:                                         │
   │   rag.entities_vdb.query(question, top_k=RERANK_TOP_N)   │ ← hybrid dense+sparse
   │   keep hits whose graph node label ∈ TABULAR_LABELS      │
   │   collect their `table` property                         │
   └──────────────────────────────────────────────────────────┘
        │ []                                    │ ["workbook_a1b2__sales"]
        ▼                                       ▼
   document RAG path              answer(question, tables)
                                       │
                                       │ schema_context(tables)
                                       │ _generate(...)  ──────► llm-router (query role)
                                       │      │
                                       │      ├─ "NO_SQL"        → return None → doc path
                                       │      ├─ "ADD COLUMN …"  → add_computed_column()
                                       │      └─ "SELECT …"      → run_select()
                                       │             │
                                       │             └─ _validate_select → SpreadsheetError
                                       │                     │
                                       │                     └─► retry with the error text
                                       ▼
                              frames: table, token(summary)
```

### Who produces the thing it routes on

The router only works because [`tabular_graph.project`](../BACKEND.md#tabular_graphprojectrag-workbook-doc_id)
wrote a [schema card](../GLOSSARY.md#schema-card) as each column node's description, and
`graph_schema.upsert_node` indexed it. Naming a column in a question hits that card exactly
on the **sparse** half of hybrid search — the half that exists for exact tokens. The routing
and the storage design are the same decision.

### Who consumes its output

| Consumer | What it does with it |
|---|---|
| [`query-orchestrator`](query-orchestrator.md) | Emits the `table` SSE frame and a one-line summary token |
| [Frontend `DataResultTable`](../FRONTEND.md#dataresulttable-result) | Renders the rows: sort, page, CSV export, undo, and a collapsible SQL disclosure |
| [`DELETE …/spreadsheet/{table}/columns/{column}`](../BACKEND.md#delete-apiv1knowledge-basespreadsheettablecolumnscolumn) | The undo endpoint, calling `drop_computed_column` |

The **extension does not render `table` frames** — see [EXTENSION.md §9](../EXTENSION.md#9-known-limitations--open-todos).

---

## 5. Core Concepts & Key Components

### Routing by retrieval, not by asking

The docstring on `relevant_tables` states the change plainly: an empty list means the
question isn't about the tabular data and **no SQL call is made at all** — which is the
common case, and used to cost an LLM round-trip to discover.

Previously, *any* existing spreadsheet sent *every* message through the SQL router first.
That's a coin flip with a per-message price. Now the decision falls out of a vector query
the system performs anyway.

The check has two parts: the hit must correspond to a graph node, **and** that node's
`entity_type` must be in `graph_schema.TABULAR_LABELS` (`workbook`, `worksheet`, `column`).
A document entity that happens to be named "Revenue" doesn't route.

### Scoped schema context

`schema_context(tables)` renders DDL for **only the tables the router found**:

```
TABLE workbook_a1b2c3d4__sales  -- worksheet "Sales" of q3.xlsx
  customer VARCHAR  -- categorical
  revenue DOUBLE  -- currency
  cost DOUBLE  -- currency
  margin DOUBLE  -- percentage, derived from Cost, Revenue
```

Two wins, both stated in the source: exact column-name matches land via the sparse vector,
and **the prompt stops growing with every workbook uploaded**. Fifty workbooks and one
workbook produce the same prompt size.

The `-- categorical` / `-- currency` annotations are the
[semantic types](../GLOSSARY.md#semantic-type) the parser inferred from Excel number
formats, and `derived from Cost, Revenue` is the captured
[formula lineage](../GLOSSARY.md#formula-lineage). Both help the model pick the right column
without seeing a single row.

### Three guardrails, in order

`_validate_select` runs before any row is read:

1. **`con.extract_statements(sql)`** — must parse, and must yield **exactly one** statement.
   Rejects `SELECT 1; DROP TABLE x`.
2. **`statements[0].type == StatementType.SELECT`** — rejects `UPDATE`, `DELETE`, `ATTACH`,
   `COPY`, `INSTALL`, and everything else.
3. **`"_node_rels_columns" not in sql.lower()`** — the internal metadata table is not
   queryable.
4. **`con.execute(f"DESCRIBE {sql}")`** — binds every table and column name against the real
   catalog **without running the query**. A hallucinated column fails here rather than
   halfway through a scan, and the error message names it.

There is a fifth guardrail that isn't in this file at all: `get_connection()` opens DuckDB
with **`enable_external_access=False`**, so a generated `SELECT` cannot reach the local
filesystem through `read_csv` or `read_text`. That's why CSV ingestion has to use a separate
throwaway connection.

Then `run_select` executes with `LIMIT {SPREADSHEET_MAX_ROWS + 1}` and keeps the first
`SPREADSHEET_MAX_ROWS`, so `truncated` is knowable from the count rather than guessed.

### Self-healing, bounded at three

```python
for attempt in range(3):
    generated = await _generate(question, schema, error)
    …
    except SpreadsheetError as e:
        error = f"-- attempt: {generated}\n-- error: {e}"
```

The model is handed **its own previous SQL and the exact database error**, as SQL comments,
and asked to fix it. `Referenced column "revenu" not found` is enough for a model to correct
a typo on the next attempt. Three attempts, then a `SpreadsheetError` the user sees:

> Couldn't turn that into a valid query over your spreadsheets. Try naming the worksheet and
> columns you mean.

That message is advice, not a stack trace — and it's actionable, because naming the columns
is exactly what makes the router and the schema context work better.

### Three reply forms, and `NO_SQL` is a routing correction

The system prompt allows exactly one of:

1. A single `SELECT` statement.
2. `ADD COLUMN <table>.<new_column> = <sql_expression>` — only when asked to add or compute.
3. `NO_SQL` — when the question isn't about the data in these tables.

`NO_SQL` makes `answer()` return `None`, and the orchestrator's `if result is not None`
guard fails, so **execution falls through to the document RAG path**. The router matched a
schema card, but the question turned out to be about something else. That's not a failure;
it's the second half of the routing decision, made by the one component that can see both
the question and the schema.

### The write path is a different shape from the read path

`add_computed_column` never runs generated SQL as a statement. It:

1. Sanitises the column name (`re.sub(r"\W+", "_", column)`, lowercased) — the name is
   interpolated into DDL, so it must be an identifier.
2. Refuses an unknown table, and refuses to overwrite an existing column.
3. Runs `DESCRIBE SELECT ({expression}) AS {column} FROM "{table}"` — which **binds the
   expression against the real columns and tells us its type**, without executing it.
4. Only then `ALTER TABLE … ADD COLUMN` with that type, and `UPDATE … SET`.
5. Records the column in `_node_rels_columns` with `semantic="derived"`,
   `formula=<expression>`, `derived_from=<columns the expression mentions>`, and
   **`added_later=True`**.

That last flag is the whole undo mechanism: `drop_computed_column` refuses any column
without it, so the workbook's own data is not deletable through this feature.

---

## 6. Function & Component Reference

---

### `relevant_tables(rag, question)`

**What it does:** Decides which tables a question is actually about, by retrieval rather
than by asking a model.

**Input:** `rag: LightRAG`, `question: str`.

**Output:** `list[str]` — DuckDB table names, in relevance order. `[]` means "not a data
question".

**Example:**
```python
await relevant_tables(rag, "What's the total revenue by quarter?")
# => ["workbook_a1b2c3d4__sales"]

await relevant_tables(rag, "What is the leave policy?")
# => []
```

**How:**
```python
hits = await rag.entities_vdb.query(question, top_k=RERANK_TOP_N)   # 10
for hit in hits:
    node = await graph.get_node(hit["entity_name"])
    if node and node["entity_type"] in gs.TABULAR_LABELS and node.get("table"):
        collect node["table"]      # deduped, order preserved
```

**Notes:** `TABULAR_LABELS` is `{workbook, worksheet, column}`. A `workbook` node has no
`table` property (only worksheets and columns do), so a hit on the workbook alone
contributes nothing — which is correct, since a workbook isn't queryable. Imports
`graph_schema` inside the function to keep the module's import graph shallow.

---

### `answer(question, tables=None)`

**What it does:** The entry point. Generates SQL, validates it, executes it, and retries on
rejection.

**Input:** `question: str`, `tables: list[str] | None` — from `relevant_tables`.

**Output:** `dict | None`. `None` means "not a spreadsheet question — use the document
path". Raises `SpreadsheetError` after three failed attempts.

**Example:**
```python
await answer("What's the total revenue by quarter?", ["workbook_a1b2c3d4__sales"])
# => {"columns": ["quarter", "total"],
#     "rows": [["Q1", 412000.0], ["Q2", 388500.0], ["Q3", 501200.0], ["Q4", 470900.0]],
#     "total_row_count": 4, "truncated": False,
#     "sql": "SELECT quarter, SUM(revenue) AS total FROM workbook_a1b2c3d4__sales GROUP BY quarter"}

await answer("Who sent me this spreadsheet?", ["workbook_a1b2c3d4__sales"])
# => None      the model replied NO_SQL

await answer("What's the total revenue?", [])
# => None      schema_context("") is empty — nothing to query
```

**Notes:** An empty `schema_context` returns `None` before any LLM call. A failure in
`_generate` itself (the provider chain exhausted) logs a warning and returns `None` rather
than raising — a model outage falls back to the document path rather than failing the turn.
Only a `SpreadsheetError` triggers a retry; any other exception propagates.

---

### `run_select(sql)`

**What it does:** Validates a statement, then executes it with a hard row cap.

**Input:** `sql: str`. **Output:** `dict`. Raises `SpreadsheetError`.

**Example:**
```python
run_select('SELECT customer, revenue FROM workbook_a1b2c3d4__sales LIMIT 2')
# => {"columns": ["customer", "revenue"],
#     "rows": [["Acme Corp", 41200.0], ["Globex", 38850.0]],
#     "total_row_count": 2, "truncated": False,
#     "sql": "SELECT customer, revenue FROM workbook_a1b2c3d4__sales LIMIT 2"}
```

**Notes:** Wraps the query as `SELECT * FROM ({sql}) LIMIT {cap + 1}` — fetching one extra
row is how `truncated` is determined without a second `COUNT(*)`. Every cell goes through
`_cell`, which converts dates/times to ISO strings, `Decimal` to `float`, and bytes to
UTF-8 with replacement, so the result is JSON-serialisable.

---

### `_validate_select(sql)`

**What it does:** Rejects anything that isn't one `SELECT`, and anything naming something
that doesn't exist. Raises `SpreadsheetError`, returns `None` on success.

**Example:**
```python
_validate_select("SELECT * FROM workbook_a1b2c3d4__sales")          # ok

_validate_select("SELECT 1; DROP TABLE workbook_a1b2c3d4__sales")
# SpreadsheetError: Only a single statement is allowed.

_validate_select("DELETE FROM workbook_a1b2c3d4__sales")
# SpreadsheetError: Only SELECT queries are allowed here (got DELETE).

_validate_select("SELECT * FROM _node_rels_columns")
# SpreadsheetError: The internal metadata table is not queryable.

_validate_select("SELECT revenu FROM workbook_a1b2c3d4__sales")
# SpreadsheetError: Query references something that does not exist:
#   Binder Error: Referenced column "revenu" not found in FROM clause!
```

**Notes:** That last message is what gets fed back to the model on the retry, and it's why
the self-healing works — it names the bad identifier and often suggests the right one.

---

### `schema_context(tables=None)`

**What it does:** Renders the DDL the model is allowed to reference.

**Input:** `tables: list[str] | None` — `None` or `[]` means every table.

**Output:** `str`. **Empty string means there is nothing to query.**

**Example:**
```python
schema_context(["workbook_a1b2c3d4__sales"])
# => 'TABLE workbook_a1b2c3d4__sales  -- worksheet "Sales" of q3.xlsx\n'
#    '  customer VARCHAR  -- categorical\n'
#    '  revenue DOUBLE  -- currency\n'
#    '  margin DOUBLE  -- percentage, derived from Cost, Revenue'

schema_context([])            # => ""   when nothing is ingested
```

**Notes:** Reads `_node_rels_columns` ordered by `table_name, rowid`, so columns appear in
their original spreadsheet order rather than alphabetically — which matters for a model
trying to understand what the sheet is.

---

### `add_computed_column(table, column, expression)`

**What it does:** Adds a derived column to a worksheet's table.

**Input:**

| Param | Type | Example |
|---|---|---|
| `table` | `str` | `"workbook_a1b2c3d4__sales"` |
| `column` | `str` | `"Margin %"` → sanitised to `margin` |
| `expression` | `str` | `"(revenue - cost) / revenue"` |

**Output:** the full `run_select` result for the table, plus `added_column` and `table`.
Raises `SpreadsheetError`.

**Example:**
```python
add_computed_column("workbook_a1b2c3d4__sales", "margin", "(revenue - cost) / revenue")
# => {"columns": ["customer", "revenue", "cost", "margin"],
#     "rows": [["Acme Corp", 41200.0, 28900.0, 0.2985…], …],
#     "total_row_count": 120, "truncated": False,
#     "sql": 'SELECT * FROM "workbook_a1b2c3d4__sales"',
#     "added_column": "margin", "table": "workbook_a1b2c3d4__sales"}
```

**Notes:** `derived_from` is computed by word-boundary matching each existing column name in
the expression — approximate, but good enough to populate the lineage the schema context
shows on the next question. **The graph is not updated**: no `Column` node is created for a
computed column and no `DERIVED_FROM` edge is written, so a column added through chat is
queryable but invisible in the Graph Explorer until the workbook is re-uploaded.

---

### `drop_computed_column(table, column)`

**What it does:** The undo. Removes a column **only if** this feature added it.

**Input:** `table: str`, `column: str`. **Output:** `bool`.

**Example:**
```python
drop_computed_column("workbook_a1b2c3d4__sales", "margin")    # => True
drop_computed_column("workbook_a1b2c3d4__sales", "revenue")   # => False — original data
drop_computed_column("workbook_a1b2c3d4__sales", "nope")      # => False — no such column
```

**Notes:** The `added_later` flag in `_node_rels_columns` is the only authority. Called by
[`DELETE …/spreadsheet/{table}/columns/{column}`](../BACKEND.md#delete-apiv1knowledge-basespreadsheettablecolumnscolumn),
which turns `False` into a 404.

---

### `list_tables()` / `_cell(value)` / `_generate(question, schema, previous_error)`

| Function | Behaviour |
|---|---|
| `list_tables()` | `SELECT DISTINCT table_name FROM _node_rels_columns ORDER BY table_name` |
| `_cell(value)` | date/datetime/time → `.isoformat()`; `Decimal` → `float`; `bytes` → UTF-8 with replacement; everything else unchanged |
| `_generate(...)` | One `query_llm_func` call; strips markdown fences with `_FENCE` and a trailing `;`. On a retry, appends the previous attempt and its error to the prompt. |

The fence-stripping exists because models add ` ```sql ` fences despite the prompt saying
"no markdown fences" — a rule stated *and* enforced.

---

### `SpreadsheetError`

The user-facing exception. Its docstring is a constraint: *"Surfaced to the user; never
contains raw SQL errors alone."* Every raise wraps the database error in a sentence of
context.

---

## 7. End-to-End Walkthroughs

### 7.1 A clean aggregate

**"What's the total revenue by quarter?"** with `q3.xlsx` ingested.

1. `relevant_tables`: `entities_vdb.query` hits the `revenue` column card
   (*"Column revenue (header 'Revenue', currency, DOUBLE) of worksheet 'Sales'…"*) on the
   sparse half. Node label is `column`, `table` property is
   `workbook_a1b2c3d4__sales`. → `["workbook_a1b2c3d4__sales"]`
2. `answer(question, tables)` → `schema_context(tables)` renders four columns.
3. `_generate` → one `query_llm_func` call →
   `SELECT quarter, SUM(revenue) AS total FROM workbook_a1b2c3d4__sales GROUP BY quarter`
4. Not `NO_SQL`, doesn't match `_ADD_COLUMN` → `run_select`.
5. `_validate_select`: one statement ✓, type `SELECT` ✓, no metadata table ✓,
   `DESCRIBE` binds `quarter` and `revenue` ✓.
6. Execute `SELECT * FROM (…) LIMIT 501` → 4 rows, `truncated=False`.
7. Orchestrator: `table` frame with the rows, `token` frame with `"4 rows."`,
   `_persist(…, evidence=[])`, `done`.
8. The frontend renders `DataResultTable` — sortable, with the SQL in a `<details>`.

---

### 7.2 A hallucinated column, healed

**"Show me revenue minus cost per customer."**

1. Router → `["workbook_a1b2c3d4__sales"]`.
2. Attempt 1: `SELECT customer, revenue - costs AS profit FROM workbook_a1b2c3d4__sales`
   — the column is `cost`, not `costs`.
3. `_validate_select` passes 1–3, then `DESCRIBE` raises
   `Binder Error: Referenced column "costs" not found in FROM clause!` → `SpreadsheetError`.
   **No rows were read.**
4. Caught, logged at `info`: `Attempt 1 rejected (Query references something that does not
   exist: …)`. `error` is set to:
   ```
   -- attempt: SELECT customer, revenue - costs AS profit FROM workbook_a1b2c3d4__sales
   -- error: Query references something that does not exist: Binder Error: Referenced
   --        column "costs" not found in FROM clause!
   ```
5. Attempt 2: the prompt now carries the schema **and** the failure. →
   `SELECT customer, revenue - cost AS profit FROM workbook_a1b2c3d4__sales`
6. Validates, executes, returns 120 rows.

The user sees a correct answer. The only trace of the correction is one `info` line.

---

### 7.3 Adding a column, then undoing it

**"Add a margin column showing profit as a percentage of revenue."**

1. Router → `["workbook_a1b2c3d4__sales"]`.
2. The model recognises reply form 2 →
   `ADD COLUMN workbook_a1b2c3d4__sales.margin = (revenue - cost) / revenue`
3. `_ADD_COLUMN` matches, capturing table, column and expression.
4. `add_computed_column`:
   - Table exists ✓. Column name sanitises to `margin` ✓. Not already present ✓.
   - `DESCRIBE SELECT ((revenue - cost) / revenue) AS margin FROM "…"` → type `DOUBLE`.
     **This is the guardrail** — an expression naming a nonexistent column fails here.
   - `ALTER TABLE … ADD COLUMN "margin" DOUBLE`, then `UPDATE … SET "margin" = (…)`.
   - `record_columns` writes the row with `semantic="derived"`, `formula` set,
     `derived_from=["revenue", "cost"]`, **`added_later=True`**.
   - `run_select('SELECT * FROM "workbook_a1b2c3d4__sales"')` returns the whole table with
     the new column, plus `added_column: "margin"`.
5. Orchestrator: `table` frame, then
   `"Added the **margin** column to \`workbook_a1b2c3d4__sales\`."`
6. `DataResultTable` sees `added_column` and renders an **Undo margin** button.
7. Clicking it → `DELETE /api/v1/knowledge-base/spreadsheet/workbook_a1b2c3d4__sales/columns/margin`
   → `drop_computed_column` finds `added_later=True`, drops the column and its metadata row.
   The table hides the column client-side without refetching.
8. The **next** question about this sheet sees the schema without `margin`, because
   `schema_context` reads `_node_rels_columns` fresh.

---

## 8. Configuration & Setup

| Setting | Default | Effect |
|---|---|---|
| `SPREADSHEET_MAX_ROWS` | `500` | Hard cap on a result set. Exceeding it sets `truncated`. |
| `RERANK_TOP_N` | `10` | `top_k` for the router's vector query — how many candidates are examined for a tabular label. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | The SQL writer, via `query_llm_func`. |
| `STORAGE_DIR` | `./storage` | `spreadsheets.duckdb` lives here. |

Two settings that shape this agent live elsewhere:

- **`enable_external_access=False`** — hardcoded in `parsers/spreadsheet.get_connection()`,
  not configurable, deliberately.
- **`SPREADSHEET_MAX_GRAPH_VALUES`** — caps `HAS_VALUE` edges during projection; affects the
  graph, not queries.

### Tests

```bash
cd backend
pytest app/services/test_spreadsheet.py -v
```

Covers type inference and formula lineage in the parser, the validation rules here, and the
computed-column round trip.

### Inspecting the store directly

```bash
cd backend
python -c "
from app.services.parsers.spreadsheet import get_connection
con = get_connection()
print(con.execute('SELECT * FROM _node_rels_columns').fetchdf())
"
```

### What a working routing decision looks like

```
INFO app.spreadsheet_query: Attempt 1 rejected (Query references something that does not exist: …)
INFO app.spreadsheet_query: Added derived column workbook_a1b2c3d4__sales.margin = (revenue - cost) / revenue
```

**No log line at all** for a question that didn't route — the router is silent by design.

---

## 9. Known Limitations & Open TODOs

| Limitation | Detail |
|---|---|
| **The two answer paths are exclusive** (`ponytail:` in `chat.py`) | A spreadsheet answer runs no document retrieval. "What does the contract say about the customers in this sheet?" gets rows or prose, never both. |
| **No cross-workbook joins in practice** | Nothing forbids a join, but each worksheet is its own table with generated names and no foreign keys, so the model has no relationship to join on. |
| **A computed column never reaches the graph** | `add_computed_column` writes DuckDB and `_node_rels_columns` but creates no `Column` node and no `DERIVED_FROM` edge. It's queryable but invisible in the Graph Explorer until re-upload. |
| **`derived_from` detection is word-boundary matching** | A column named `cost` matches inside `cost_center`… actually no — `\b` prevents that — but a column whose name appears as a string literal in the expression is falsely credited. |
| **Three attempts is a fixed constant** | Not configurable. A model that needs a fourth round fails. |
| **The row cap is applied after execution** | `LIMIT 501` wraps the query, so DuckDB still evaluates the full aggregate before limiting. Fine at spreadsheet scale, not at database scale. |
| **One shared DuckDB connection** (`ponytail:` in `spreadsheet.py`) | Serialized by the event loop. `con.cursor()` per request if concurrent queries ever matter. |
| **No query timeout** | A pathological generated query (a cross join over two large sheets) runs to completion. |
| **`add_computed_column` has no undo *within* the turn** | If `UPDATE` fails after `ALTER TABLE` succeeded, the column exists and is empty. There's no transaction wrapping the pair. |
| **Sanitising is one-way** | "Margin %" becomes `margin`, and the user is never told the name changed. Two differently-spelled requests can collide on one identifier. |
| **The router can only see what was indexed** | A worksheet whose columns have generic names (`col_1`, `col_2`) produces schema cards with nothing distinctive to match, so questions about it rarely route. |

---

## 10. See Also

- [`GLOSSARY.md`](../GLOSSARY.md) — [DuckDB](../GLOSSARY.md#duckdb),
  [Schema card](../GLOSSARY.md#schema-card), [SQL router](../GLOSSARY.md#sql-router),
  [Semantic type](../GLOSSARY.md#semantic-type),
  [Computed column](../GLOSSARY.md#computed-column),
  [`_node_rels_columns`](../GLOSSARY.md#_node_rels_columns),
  [Formula lineage](../GLOSSARY.md#formula-lineage)
- [`query-orchestrator`](query-orchestrator.md) — the caller, and the `None` fall-through
- [`llm-router`](llm-router.md) — `query_llm_func`, which writes the SQL
- [`BACKEND.md`](../BACKEND.md) —
  [`load_spreadsheet`](../BACKEND.md#load_spreadsheetdata-file_name) (the parser),
  [`tabular_graph.project`](../BACKEND.md#tabular_graphprojectrag-workbook-doc_id) (the
  schema cards), [`get_connection`](../BACKEND.md#get_connection--drop_workbook_tablesfile_name--record_columns)
- [`FRONTEND.md`](../FRONTEND.md#dataresulttable-result) — how the rows are rendered
- [`agents/README.md`](README.md)
