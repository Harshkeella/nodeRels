# Extension

> The Chrome extension in `extension/`. A Manifest V3 popup that clips the page you're
> looking at into the knowledge base, and lets you chat with that knowledge base without
> leaving the tab.

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

The extension exists to solve one problem the dashboard can't: **a server-side fetch of a
URL is not the same page you are looking at.** Anything behind a login, anything rendered
by JavaScript after load, anything a paywall shows you but not an anonymous crawler — the
backend's URL ingestion gets a login wall or an empty app shell, while your browser has the
real article on screen right now.

So the extension extracts *in the tab*. It injects Defuddle — the same article-extraction
engine Obsidian Web Clipper uses — into the live, fully-rendered DOM, gets Markdown back,
and shows it to you in a **preview panel**. Nothing reaches the backend until you've read
the extraction and pressed *Add to Knowledge Base*. You can edit it first.

Two page types skip the DOM entirely, because the backend has better tools for them: a
YouTube URL goes to the transcript API, and a PDF's bytes are downloaded and posted to the
same file endpoint the dashboard's uploader uses.

The second tab is a chat window against the same knowledge base — the same SSE endpoint the
dashboard uses, rendered with a ~75-line Markdown subset instead of a full library, because
a popup doesn't need one.

There is **no build step**. Plain HTML, CSS and ES modules loaded straight from the folder.
The one third-party file, `vendor/defuddle.js`, is the prebuilt UMD bundle copied in as-is.

---

## 2. Tech Stack

There is no `package.json` for the extension itself. It has **zero runtime dependencies**
beyond one vendored file.

| Thing | Version / detail | Why this one |
|---|---|---|
| **Manifest V3** | `manifest_version: 3`, extension version 1.1.0 | The only option Chrome accepts for new extensions. |
| **Plain ES modules** | `<script type="module" src="popup.js">` | No bundler, no transpiler, no build. `import`/`export` work natively in an extension popup, so a build step would buy nothing and cost a workflow. |
| **`defuddle`** (vendored) | `vendor/defuddle.js`, 750 KB, MIT | Article extraction. Chosen because it's the engine behind Obsidian Web Clipper, ships site-specific extractors for the usual awkward pages (ChatGPT, Claude, GitHub, Hacker News, Bluesky…), and — critically — its `markdown: true` option emits Markdown **directly**, so there's no Turndown or second HTML→Markdown converter to add. Copied in as the prebuilt UMD bundle rather than npm-installed and bundled, so there's still nothing to run before loading the extension. `vendor/LICENSE-defuddle` is included. |
| **`jsdom`** | ^26.1.0, **test-only** | Runs the shipped extraction code against saved page fixtures outside a browser. It lives in `tests/package.json`; the extension itself still has no dependencies. |
| **Node's test runner** | `node --test` | Same reasoning as the frontend: no framework for six assertions. |

**Chrome APIs used:** `chrome.tabs` (which page is active), `chrome.scripting`
(inject and execute), `chrome.storage.local` (the backend URL).

**Permissions requested** (`manifest.json`):

| Permission | Why |
|---|---|
| `activeTab` | Read the current tab's URL and title, and grant temporary access to its origin — which is also what lets `ingestPdfUrl` fetch a same-origin PDF. |
| `scripting` | Inject `vendor/defuddle.js` + `extract.js`, and run the selection reader. |
| `storage` | Persist the backend URL. |
| `host_permissions: 127.0.0.1:8000, localhost:8000` | Call the backend **without the backend needing any CORS changes**. |

Note what's *not* there: no `tabs` (the broad one), no `<all_urls>`, no background service
worker, no content scripts declared in the manifest. Scripts are injected on demand, only
into the tab you're looking at, only when you click a button.

---

## 3. Folder & File Structure

Generated from the repository.

```
extension/
├── manifest.json          # MV3: name, version, popup, icons, permissions, host_permissions
├── popup.html             # The whole UI: header + settings + three panels (clip/preview/chat)
├── popup.css              # ~300 lines. CSS custom properties, light + dark via
│                          #   prefers-color-scheme. Popup is a fixed 420 × 580.
├── popup.js               # THE CONTROLLER: tabs, settings, clip, preview, chat. No framework.
├── api.js                 # Backend client: URL storage + 4 calls incl. the SSE parser
├── extract.js             # Injected into the tab. Defines window.__nodeRelsExtract()
├── markdown-lite.js       # ~75-line markdown -> HTML subset for the chat panel
│
├── icons/
│   └── icon16 / 32 / 48 / 128.png     # Generated by scripts/gen_logo_assets.py
│
├── vendor/
│   ├── defuddle.js        # Prebuilt UMD bundle, copied as-is (750 KB, MIT)
│   └── LICENSE-defuddle
│
├── tests/
│   ├── package.json       # devDependency: jsdom. `npm test` -> node --test
│   ├── extract.test.mjs   # Runs the SHIPPED vendor/defuddle.js + extract.js under jsdom
│   └── fixtures/
│       ├── article.html   # A standard article
│       ├── paywall.html   # Subscriber-visible paywalled article
│       └── spa.html       # An SPA-style app shell
│
└── README.md              # User-facing install and usage notes
```

**File-count note:** seven source files, ~550 lines total excluding the vendored bundle.
The whole extension is smaller than any single one of the frontend's three largest
components.

---

## 4. How This Fits Into the Bigger Picture

The extension talks to the same FastAPI backend the dashboard does, over the same
endpoints, but reaches **three** of them instead of thirteen.

```
  Chrome popup                                  FastAPI  (chrome.storage.local backendUrl)
  ────────────                                  ─────────────────────────────────────────
  Clip tab
    YouTube URL      ── ingestUrl() ─────────▶ POST /api/v1/ingest/url
    PDF URL          ── ingestPdfUrl() ──────▶ POST /api/v1/ingest/file   (as multipart)
    anything else    ── in-tab extraction ──▶  (preview)
      then           ── ingestText(…, "article_clipper") ─▶ POST /api/v1/ingest/text
    "Add selection"  ── ingestText(…, "paste") ────────────▶ POST /api/v1/ingest/text

  Chat tab
    Send             ── streamChat() ────────▶ POST /api/v1/chat/stream   (SSE)

  Injected into the active tab (not the backend):
    vendor/defuddle.js + extract.js  →  window.__nodeRelsExtract()
    an inline function               →  window.getSelection()
```

### What it deliberately doesn't do

- **No session persistence.** `streamChat` is called with no `session_id`, so the backend
  answers identically and writes nothing. Chat history lives in a module-level `history`
  array that dies when the popup closes.
- **No knowledge-base management.** No inventory, no delete, no graph. Those are the
  dashboard's job.
- **No `evidence`, `table` or `grounding` frames.** Its `streamChat` handles `sources`,
  `token`, `error` and `done` and ignores the rest — which works precisely because the
  backend's frame types are additive and an unknown type falls through every branch. See
  [BACKEND.md §4](BACKEND.md#4-how-this-fits-into-the-bigger-picture).

### The one contract that's extension-specific

`source_type: "article_clipper"` on `POST /api/v1/ingest/text`. The backend allowlists
`source_type` to `{"paste", "article_clipper"}` — anything else becomes `"paste"` — so the
extension can mark a reviewed, in-browser clip as distinct from a plain paste without a
client being able to invent source types. That value flows all the way through: the
manifest row, the [Source Supernode](GLOSSARY.md#source-supernode)'s `source_type`
property, and the frontend's icon lookup, where
[`SUPERNODE_BY_SOURCE_TYPE`](FRONTEND.md#symbolforentitytype-sourcetype) maps it to the
`web` globe glyph.

### Relationship to the dashboard's URL ingestion

They are **complementary, not duplicates**, and neither affects the other:

| | Dashboard "Add from URL" | Extension "Clip this page" |
|---|---|---|
| Where extraction runs | Server (ZenRows → trafilatura) | The user's own tab |
| Sees logged-in content | No | Yes |
| Sees JS-rendered content | Only via ZenRows `js_render` (costs credits) | Always — it's the live DOM |
| Review before saving | No | Yes, and editable |
| `source_type` | `article_zenrows` / `article` | `article_clipper` |

---

## 5. Core Concepts & Key Components

### In-tab extraction (`extract.js`)

The one genuinely load-bearing idea. `chrome.scripting.executeScript` injects the vendored
Defuddle bundle and then `extract.js`, which defines `window.__nodeRelsExtract`. A second
`executeScript` call invokes it and the return value crosses back into the popup. Because
this runs inside the page, it sees exactly what the user sees.

`extract.js` also carries `NODE_RELS_EXTRACTORS`, a per-domain override map for pages the
general pass gets wrong. It ships with one illustrative entry
(`datatracker.ietf.org`, whose plain-text `<pre>` pages Defuddle scores as boilerplate).
The comment is explicit that entries should be added only after seeing a specific site
extract badly — Defuddle already handles the usual suspects.

### The preview gate (`popup.js`, `#panel-preview`)

Nothing reaches the backend until you've seen it. The extracted title and Markdown land in
a **read-only** title input and textarea; *Edit* unlocks both; *Add to Knowledge Base*
sends. If extraction returns fewer than 200 characters — an app shell that hasn't rendered,
say — the popup says so and points at *Add selected text* instead of submitting a garbage
clip.

### Routing by URL shape (`popup.js`)

Two regexes decide the path before any DOM work happens: `YOUTUBE_HOST_RE` and
`PDF_PATH_RE`. YouTube and PDFs are handed to backend parsers that do a better job than any
DOM pass could. Everything else goes through Defuddle. This is URL-shape matching, not
content sniffing, and the code says so in a `ponytail:` comment.

### The API client (`api.js`)

Five functions and the backend-URL accessor. Structurally the same as the frontend's
`lib/api.ts` — including a near-identical SSE parser — but in plain JavaScript, with
`chrome.storage.local` supplying the base URL instead of a build-time environment variable,
and with a `ponytail:`-free but deliberately smaller surface.

### `markdown-lite.js`

Headings, bullets, numbered lists, paragraphs, inline code, bold, italic and links. **It
escapes HTML first**, before any inline transformation, so raw HTML in the source text — or
an LLM echoing ingested page content back — can't inject markup into the popup. That's the
reason this file exists rather than a 100 KB Markdown library: at this scale, escaping
first and handling eight constructs is the whole requirement.

---

## 6. Function & Component Reference

### `api.js`

---

#### `getBackendUrl()` / `setBackendUrl(url)`

**What they do:** Read and write the backend base URL from `chrome.storage.local`.

**Input:** `setBackendUrl(url: string)`.

**Output:** `getBackendUrl()` → `Promise<string>`.

**Example:**
```js
await getBackendUrl();                          // => "http://127.0.0.1:8000"  (default)
await setBackendUrl("http://192.168.1.20:8000");
await getBackendUrl();                          // => "http://192.168.1.20:8000"
```

**Notes:** `DEFAULT_BACKEND_URL` is `http://127.0.0.1:8000`. Pointing this at a different
host or port also requires adding that origin to `host_permissions` in `manifest.json` and
reloading the extension — `chrome.storage` will happily hold a URL the extension isn't
permitted to fetch.

---

#### `ingestUrl(url)`

**What it does:** Asks the backend to fetch and ingest a URL itself. Used only for YouTube,
where the transcript API is the right tool.

**Input:** `url: string`.

**Output:** `Promise<IngestResult>`.

**Example:**
```js
await ingestUrl("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
// => { doc_id: "doc-8ab3…", file_name: "YouTube: dQw4w9WgXcQ",
//      source_type: "youtube", chunk_count: 4, size_bytes: 9124,
//      date_added: "2026-08-30T09:20:41.002110+00:00", deduped: false }
```

**Notes:** Throws `Error(detail)` on non-OK, via the shared `parseErrorDetail` which
prefers the backend's `{"detail": …}` body over `res.statusText`.

---

#### `ingestText(text, title, sourceType)`

**What it does:** Posts text to the knowledge base.

**Input:**

| Param | Type | Example |
|---|---|---|
| `text` | `string` | `"# Boring Infrastructure\n\nNovelty has a carrying cost…"` |
| `title` | `string` | `"Boring Infrastructure"` |
| `sourceType` | `string` | `"article_clipper"` or `"paste"` |

**Output:** `Promise<IngestResult>`.

**Example:**
```js
await ingestText(markdown, "Boring Infrastructure", "article_clipper");
// => { doc_id: "doc-c71e…", file_name: "Boring Infrastructure",
//      source_type: "article_clipper", chunk_count: 6, size_bytes: 18402,
//      date_added: "2026-08-30T10:02:11.774310+00:00", deduped: false }
```

**Notes:** The third parameter is the only place `"article_clipper"` originates in the
whole system. A reviewed clip sends it; *Add selected text* sends `"paste"`, because a
highlighted fragment isn't a clipped article.

---

#### `ingestPdfUrl(url)`

**What it does:** Downloads a PDF in the popup and posts its bytes to the file endpoint, so
the backend's PyMuPDF parser handles it rather than a DOM extractor.

**Input:** `url: string`.

**Output:** `Promise<IngestResult>` — the first entry of `results`.

**Example:**
```js
await ingestPdfUrl("https://example.com/papers/graphrag.pdf");
// => { doc_id: "doc-2b90…", file_name: "graphrag.pdf", source_type: "pdf",
//      chunk_count: 44, size_bytes: 291044, deduped: false, … }
```

**Notes:** Three things are worth knowing.
1. `activeTab` grants access to the tab's own origin, which is what makes the `fetch`
   succeed at all.
2. The filename is decoded from the URL path and `.pdf` is appended if the path didn't end
   in it, so a content-disposition-driven name never arrives extensionless and gets
   rejected by the backend's extension check.
3. A download failure is wrapped in a plain-English message
   (`"Could not download the PDF (HTTP 403)."`), and a successful upload with an empty
   `results` array is turned into `errors[0].error` or a fallback — because the file
   endpoint returns HTTP 200 even when every file failed.

---

#### `streamChat(message, history, handlers, signal)`

**What it does:** Streams an answer from the backend and dispatches SSE frames to
callbacks.

**Input:**

| Param | Type | Example |
|---|---|---|
| `message` | `string` | `"What did I clip about infrastructure?"` |
| `history` | `{role, content}[]` | `[{role: "user", content: "…"}]` |
| `handlers` | `{onSources?, onToken?, onError?, onDone?}` | |
| `signal` | `AbortSignal?` | unused by the current caller |

**Output:** `Promise<void>`.

**Example:**
```js
let text = "";
await streamChat("What did I clip about infrastructure?", history, {
  onSources: (s) => console.log(s),
  // => [{ reference_id: "1", file_path: "Boring Infrastructure" }]
  onToken: (t) => (text += t),
  onDone:  () => console.log(text),
  // => "You clipped an article arguing that novelty has a carrying cost…"
});
```

**Notes:** Handles four frame types and ignores `evidence`, `table` and `grounding` — an
unknown `event.type` falls through every `else if`. **No `session_id` is sent**, so the
backend answers without persisting. Unlike the frontend's version, a network-level throw
from `fetch` is caught and routed to `onError` rather than propagating, because the popup
has no error boundary to catch it.

---

### `extract.js` (runs in the page, not the popup)

---

#### `window.__nodeRelsExtract()`

**What it does:** Extracts the current page's article as Markdown, plus its metadata.

**Input:** none — it reads the ambient `document` and `location`.

**Output:** an object:

| Field | Type | Example |
|---|---|---|
| `title` | `string` | `"Boring Infrastructure"` |
| `markdown` | `string` | `"## Novelty has a carrying cost\n\n- Every dependency…"` |
| `author` | `string` | `"Jordan Ellery"` |
| `published` | `string` | `"2025-03-11"` |
| `site` | `string` | `"Example Journal"` |
| `url` | `string` | `"https://journal.example.com/boring-infrastructure"` |
| `extractor` | `string` | `"defuddle"`, or `"custom:datatracker.ietf.org"` |

**Example:** (these values are asserted by `tests/extract.test.mjs` against
`fixtures/article.html`)
```js
window.__nodeRelsExtract();
// => { title: "Boring Infrastructure", author: "Jordan Ellery",
//      published: "2025-03-11", site: "Example Journal",
//      url: "https://journal.example.com/boring-infrastructure",
//      markdown: "## Novelty has a carrying cost\n\n- Every dependency…\n\n> Choose boring technology…",
//      extractor: "defuddle" }
```

**Notes:** A custom extractor short-circuits Defuddle entirely and returns only `title`,
`markdown`, `site`, `url` and `extractor` — no `author` or `published`, since a custom
extractor's contract is `(document) => ({ title?, markdown })`. `vendor/defuddle.js` must
be the **"full" build**, whose `markdown: true` option emits Markdown straight into
`content`; the slim build doesn't, and swapping it would silently produce HTML.

#### `nodeRelsCustomExtractor(host)`

Returns the override function for a host, matching the domain exactly or as a suffix
(`docs.datatracker.ietf.org` matches `datatracker.ietf.org`), or `null`. `www.` is stripped
before the lookup.

---

### `markdown-lite.js`

---

#### `renderMarkdownLite(source)`

**What it does:** Converts a small Markdown subset to HTML for the chat panel.

**Input:** `source: string`. **Output:** `string` of HTML.

**Supported:** `#`/`##`/`###` headings (rendered as `h3`/`h4`/`h5` to keep popup-scale
type sane), `-`/`*` bullets, `1.`/`1)` numbered lists, paragraphs, `` `code` ``, `**bold**`,
`*italic*`, and `[text](https://…)` links.

**Example:**
```js
renderMarkdownLite("## Sources\n\n- One `thing`\n- **Two**");
// => "<h4>Sources</h4><ul><li>One <code>thing</code></li><li><strong>Two</strong></li></ul>"

renderMarkdownLite("<img src=x onerror=alert(1)>");
// => "<p>&lt;img src=x onerror=alert(1)&gt;</p>"
```

**Notes:** `escapeHtml` runs **first**, before any inline transformation — so raw HTML in
the source text, or an LLM echoing ingested page content, becomes visible text rather than
live markup. Links are restricted to `https?://` by the regex and get
`target="_blank" rel="noopener noreferrer"`. The italic pattern uses lookarounds
(`(?<!\*)\*([^*]+)\*(?!\*)`) so `**bold**` isn't matched as two italics. Blank lines are
dropped rather than becoming empty paragraphs; a list is closed by any non-list line.

---

### `popup.js`

The controller. It has no exports — it wires DOM events on load — so this section covers
its internal functions and the UI regions they drive.

---

#### `showPanel(name)` and the tab bar

Toggles a `hidden` class across the three panels (`clip`, `preview`, `chat`) and sets
`aria-selected` on the two visible tab buttons. Note there are **three panels but two
tabs** — `preview` isn't reachable from the tab bar, only from a successful clip.

---

#### `loadActiveTab()`

Queries the active tab, fills the header with its title and URL, and **disables both clip
buttons for any non-`http(s)` URL** — `chrome://`, `file://` and the new-tab page can't be
scripted, so the buttons say "No page to clip" rather than failing on click. Runs once on
popup open.

---

#### `extractPage(tabId)`

**What it does:** Injects the extraction code into a tab and runs it.

**Input:** `tabId: number`. **Output:** `Promise<object>` — `__nodeRelsExtract`'s return.

**Example:**
```js
const extracted = await extractPage(activeTab.id);
// => { title: "Boring Infrastructure", markdown: "## Novelty has a carrying cost…", … }
```

**Notes:** **Two** `executeScript` calls, deliberately. The first injects files
(`vendor/defuddle.js`, then `extract.js`) — file injection can't return a value. The second
runs an inline `func` that calls the now-defined global and *can*. Both run in the page's
isolated world, so `window.__nodeRelsExtract` persists between them.

---

#### The Clip flow (`clipPageBtn` handler)

**What it does:** Routes the active page to one of three paths.

| Condition | Path | Result |
|---|---|---|
| `YOUTUBE_HOST_RE.test(hostname)` | `ingestUrl(tab.url)` | Ingested immediately, status shown |
| `PDF_PATH_RE.test(pathname)` | `ingestPdfUrl(tab.url)` | Ingested immediately, status shown |
| otherwise | `extractPage()` → `openPreview()` | **Preview panel — nothing sent yet** |

**Notes:** The button is disabled for the duration and re-enabled in a `finally`. Below
`MIN_CONTENT_CHARS` (200) the extraction is rejected with
`"Couldn't find article content on this page."` plus the selection hint, rather than
submitting an app shell. `describeIngestResult` distinguishes a fresh add
(`Added "X" — 6 chunks.`) from a dedupe (`Already in the knowledge base (X).`), because
those look identical otherwise and the difference matters.

---

#### The selection flow (`clipSelectionBtn` handler)

Runs `() => window.getSelection()?.toString() ?? ""` in the tab, and posts the trimmed
result through `ingestText(text, tab.title || tab.url, "paste")`. There's no preview — you
selected the text, so you've already reviewed it. An empty selection is reported rather
than sent.

---

#### `openPreview(extracted)` / `setPreviewEditable(editable)`

`openPreview` fills the title input, a metadata line (`site · author · published`, falling
back to the URL), and the body textarea; scrolls to the top; locks editing; clears status;
and switches to the preview panel. The metadata line's `title` attribute carries the URL
and which extractor ran, which is the only place `extractor` surfaces in the UI.

`setPreviewEditable` toggles `readOnly` on both fields, flips the button between
*Edit* / *Done editing*, keeps `aria-pressed` in sync, and focuses the body when unlocking.

---

#### The preview submit (`previewAdd` handler)

Sends the (possibly edited) body and title through
`ingestText(text, title, "article_clipper")`. On success the button **stays disabled** —
the clip is in, and pressing again would only produce a dedupe message. On failure it's
re-enabled so you can retry.

---

#### The Chat panel

A `history` array, a `sending` flag, and a submit handler. Each turn appends a user `div`
and an assistant `div` (initially "Thinking…"), streams tokens into
`assistantEl.innerHTML = renderMarkdownLite(text)`, and on `done` appends source badges and
pushes both messages onto `history`.

**Notes:** Re-rendering the whole answer through `renderMarkdownLite` on **every token** is
the simple thing, and at popup scale it's fine. Source badges are built with
`createElement` + `textContent`, not string concatenation, so a file name containing HTML
can't inject markup. The input is disabled during a turn and refocused after.

---

#### The settings panel

The ⚙ button toggles it and loads the current value; *Save* trims, strips a trailing slash,
persists via `setBackendUrl`, and shows "Saved." for 1.5 seconds.

---

### `manifest.json`

| Field | Value |
|---|---|
| `manifest_version` | 3 |
| `name` | `nodeRels — Knowledge Base Clipper` |
| `version` | `1.1.0` |
| `action.default_popup` | `popup.html` |
| `permissions` | `activeTab`, `scripting`, `storage` |
| `host_permissions` | `http://127.0.0.1:8000/*`, `http://localhost:8000/*` |

No background service worker, no declared content scripts, no `web_accessible_resources`.

---

## 7. End-to-End Walkthroughs

### 7.1 Clipping a paywalled article you're logged into

1. You're reading `https://meridian.example.com/grid-rebuild` in a logged-in tab. Click the
   extension icon.
2. `popup.html` loads; `popup.js` runs `loadActiveTab()`, which fills the header and leaves
   both buttons enabled (the URL is `https://`).
3. Click **Clip this page**. The handler parses the URL: not YouTube, path doesn't end
   `.pdf` → the DOM path. Status: "Extracting article…".
4. `extractPage(tabId)` → `chrome.scripting.executeScript({ files: ["vendor/defuddle.js",
   "extract.js"] })`. Both run in the tab's isolated world;
   `window.__nodeRelsExtract` now exists.
5. A second `executeScript({ func: () => window.__nodeRelsExtract() })` invokes it. Inside:
   `nodeRelsCustomExtractor("meridian.example.com")` → `null`, so
   `new Defuddle(document, { url, markdown: true }).parse()` runs **against the DOM the
   subscriber session rendered** — the full article, not the teaser a crawler gets.
6. The result crosses back. `markdown.length` is well over 200, so `openPreview(extracted)`
   switches to the preview panel showing the title, `Meridian · … · 2025-…`, and the
   Markdown body, read-only.
7. You click **Edit**, delete a stray pull-quote, click **Done editing**.
8. Click **Add to Knowledge Base** → `ingestText(body, title, "article_clipper")` →
   `POST /api/v1/ingest/text`.
9. Server-side this is the standard path — see
   [BACKEND.md §7.1](BACKEND.md#71-a-user-uploads-handbookpdf), from `ingest_text` onward.
   `source_type` is recorded as `article_clipper`.
10. The response returns; `describeIngestResult` sets
    `Added "Grid Rebuild" — 9 chunks.` and the button stays disabled.
11. Later, in the dashboard, that document appears in the inventory with a globe badge —
    `symbolFor("source", "article_clipper")` maps through `SUPERNODE_BY_SOURCE_TYPE` to the
    `web` variant. See [FRONTEND.md](FRONTEND.md#symbolforentitytype-sourcetype).

---

### 7.2 Clipping a PDF

1. You're viewing `https://example.com/papers/graphrag.pdf` in Chrome's built-in viewer.
2. Click **Clip this page**. `PDF_PATH_RE` matches the path → status "Sending the PDF to
   the backend…".
3. `ingestPdfUrl(url)`:
   - `fetch(url)` — succeeds because `activeTab` granted access to the tab's own origin.
   - `res.blob()` → the bytes.
   - The filename is decoded from the path (`graphrag.pdf`) and `.pdf` appended if missing.
   - A `FormData` with one `files` part is posted to `POST /api/v1/ingest/file`.
4. Server-side: `ingest_files` → `ingest_file_bytes` → `parsers/pdf.extract_pdf_text` →
   `ingest_text`.
5. `results[0]` comes back and the status reads `Added "graphrag.pdf" — 44 chunks.`

**Why not the DOM extractor:** Chrome's PDF viewer is a plugin, not a document — there is
no article DOM to defuddle. PyMuPDF is the right tool, and it's already in the backend.

---

### 7.3 Asking a question from the Chat tab

1. Click **Chat**. `showPanel("chat")`.
2. Type a question, press Send. `sending = true`, the input is disabled, a user `div` and an
   assistant `div` ("Thinking…") are appended.
3. `streamChat(message, history, handlers)`:
   - `getBackendUrl()` → `http://127.0.0.1:8000`.
   - `POST /api/v1/chat/stream` with `{ message, history }` — **no `session_id`**.
4. The backend runs its full query path — see
   [BACKEND.md §7.2](BACKEND.md#72-a-user-asks-compare-the-q3-and-q4-revenue-figures) and
   [`query-orchestrator`](agents/query-orchestrator.md). It builds evidence and runs the
   grounding check as usual; the extension just doesn't render those frames.
5. Frames arrive:
   - `sources` → stashed in a local `sources` variable, not rendered yet.
   - `evidence` → no branch matches; ignored.
   - `token` (many) → appended to `text`, and the whole answer is re-rendered through
     `renderMarkdownLite` into `assistantEl.innerHTML`.
   - `grounding` → ignored.
   - `done` → source badges are appended as `<span class="source-badge">` with
     `textContent` set from `file_path`, and both messages are pushed onto `history`.
6. `sending = false`, the input is re-enabled and refocused.
7. Closing the popup destroys `history`. Nothing was persisted server-side either, because
   no `session_id` was sent.

---

## 8. Configuration & Setup

### Loading it

1. Start the backend: `cd backend && uvicorn app.main:app --port 8000`.
2. Open `chrome://extensions` and enable **Developer mode** (top right).
3. **Load unpacked** → select the `extension/` folder.
4. Pin it (puzzle-piece icon → pin).

There is nothing to build or install first.

### Pointing at a different backend

Click the ⚙ in the popup header, enter the URL, Save. It persists in
`chrome.storage.local` under `backendUrl`.

**This is only half the job.** The manifest's `host_permissions` covers `127.0.0.1:8000`
and `localhost:8000` only — which is also why the backend needs no CORS changes for the
extension. For any other origin you must add it to `host_permissions` in `manifest.json`
and reload the extension, or every request fails on permissions regardless of what's in
storage.

### Tests

```bash
cd extension/tests
npm install       # jsdom, test-only
npm test          # node --test
```

Six assertions across three fixtures. What makes them worth having: they run
`vendor/defuddle.js` and `extract.js` — **the exact shipped code**, read off disk and
`eval`'d — over saved pages under jsdom, and assert that title, author, published date and
site all survive, that the Markdown keeps its heading/list/blockquote structure, that page
chrome ("All rights reserved", "Popular", "Archive") does *not* survive, and that no raw
`<div>`/`<script>`/`<nav>`/`<footer>` leaked into the output.

One workaround is worth knowing about: jsdom's selector engine rejects `:has()` nested
inside `:not()` — valid CSS that Chrome supports and Defuddle's removal selectors use. The
test patches `querySelector`/`querySelectorAll`/`matches`/`closest` to retry without that
clause, so the **real** extraction path runs instead of Defuddle's catch-all bailing out to
the raw `<body>` and every assertion passing for the wrong reason.

### Adding a per-site extractor

Add an entry to `NODE_RELS_EXTRACTORS` at the top of `extract.js`:

```js
const NODE_RELS_EXTRACTORS = {
  "example.com": (doc) => ({
    title: doc.querySelector("h1")?.textContent ?? doc.title,
    markdown: doc.querySelector("article")?.textContent ?? "",
  }),
};
```

Matching is exact-or-suffix on the hostname with `www.` stripped. Only add one after seeing
a specific site extract badly — Defuddle already ships extractors for ChatGPT, Claude,
GitHub, Hacker News, Bluesky and others, and a custom entry bypasses all of that.

### Regenerating the icons

`scripts/gen_logo_assets.py` at the repository root produces the four sizes in
`extension/icons/` (and `frontend/public/logo.png`) from the source logo.

---

## 9. Known Limitations & Open TODOs

### Acknowledged ceilings (marked `ponytail:` in the source)

| Where | Limitation | The upgrade path |
|---|---|---|
| `popup.js` | **PDF and YouTube detection is by URL shape, not content.** A PDF served from an extensionless URL (or behind a redirect) falls through to the DOM extractor, which will produce nothing usable. | Sniff the `Content-Type` before routing. |

### Functional gaps

- **Chat history is popup-lifetime only.** No `session_id` is sent, so nothing is persisted
  server-side either. Closing the popup loses the conversation — and it does **not** appear
  in the dashboard's session list.
- **No `evidence`, `table` or `grounding` rendering.** A spreadsheet question answered in
  the popup shows only the one-line summary token ("2 rows."), never the rows themselves —
  the `table` frame carries the data and nothing handles it. A grounding warning is likewise
  invisible here, so the popup can present an unsupported claim the dashboard would have
  flagged. This is the most consequential gap in the extension.
- **Only `http(s)://` pages can be clipped.** `chrome://`, `file://`, the new-tab page and
  the Chrome Web Store are all unscriptable.
- **`streamChat` accepts a `signal` that no caller passes.** There is no Stop button; a
  long answer runs to completion.
- **No keyboard shortcut and no context-menu entry.** Clipping requires opening the popup.
- **The backend URL and `host_permissions` can silently disagree.** Saving a URL outside
  the two permitted origins produces a permissions failure with no hint that the manifest is
  the problem.
- **HTTP-only host permissions.** An `https://` backend needs a manifest edit.
- **No retry on a transient network failure** — the error is shown and the action must be
  repeated by hand.
- **Selection clipping has no preview**, by design, but it also has no length floor — a
  three-word selection is accepted.

### Maintenance notes

- **`vendor/defuddle.js` is a pinned 750 KB copy with no version marker in the repo.**
  There's no lockfile and no update script, so upgrading it is a manual download-and-replace,
  and the tests are the only thing that would catch a regression. It must remain the **full**
  build — the slim build has no `markdown` option, and swapping it would silently produce
  HTML where the pipeline expects Markdown.
- **`extension/tests/node_modules/` is committed to the repository** (jsdom and ~30
  transitive packages). The root `.gitignore` has a `node_modules/` rule, so this predates it.
- The single `NODE_RELS_EXTRACTORS` entry is explicitly labelled "illustrative" and has
  never been needed in practice.

---

## 10. See Also

- [`docs/GLOSSARY.md`](GLOSSARY.md) — every term used here
- [`docs/BACKEND.md`](BACKEND.md) — the three endpoints this extension calls
  - [`POST /api/v1/ingest/text`](BACKEND.md#post-apiv1ingesttext) — where
    `source_type: "article_clipper"` is allowlisted
  - [`POST /api/v1/ingest/url`](BACKEND.md#post-apiv1ingesturl) — the YouTube path
  - [`POST /api/v1/ingest/file`](BACKEND.md#post-apiv1ingestfile) — the PDF path
  - [`POST /api/v1/chat/stream`](BACKEND.md#post-apiv1chatstream) — the full SSE contract,
    of which this implements a subset
- [`docs/FRONTEND.md`](FRONTEND.md) — the dashboard, whose `lib/api.ts` is this `api.js`'s
  fuller sibling
- [`docs/agents/query-orchestrator.md`](agents/query-orchestrator.md) — what runs behind
  the Chat tab
- [`docs/README.md`](README.md) — index and suggested reading order
- `extension/README.md` — the user-facing install and usage notes this document
  complements rather than replaces
