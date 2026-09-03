// Everything that talks to the backend. Paths are same-origin; vite proxies them to
// uvicorn on 8000 (see vite.config.js), so no base URL lives in the app.
const fragment = new URLSearchParams(typeof window === 'undefined' ? '' : window.location.hash.slice(1))
export const connection = fragment.has('bridge') ? {
  base: fragment.get('bridge'), ticket: fragment.get('ticket'), mode: fragment.get('mode') || 'preview',
  parent: fragment.get('parent'),
} : null
const apiOrigin = (import.meta.env.VITE_NODERELS_API_URL || 'http://localhost:8000').replace(/\/$/, '')
const origins = import.meta.env.VITE_NODERELS_API_URL ? [apiOrigin] : [apiOrigin, 'http://127.0.0.1:8000']
if (connection && !origins.some(origin => connection.base.startsWith(origin + '/api/v1/artifacts/') &&
    /^[0-9a-f-]{36}$/.test(connection.base.slice((origin + '/api/v1/artifacts/').length)))) {
  connection.error = 'Unrecognized nodeRels connection. Configure VITE_NODERELS_API_URL.'
}
const request = (url, init = {}) => connection?.error ? Promise.reject(new Error(connection.error)) : connection
  ? fetch(connection.base + '/studio' + url, { ...init, headers: { ...init.headers, Authorization: 'Bearer ' + connection.ticket } })
  : fetch(url, init)

/** One place errors are turned from a Response into something a person can read. */
async function ok(r) {
  if (r.ok) return r.json()
  const detail = await r.json().catch(() => ({}))
  throw new Error(detail.detail || `Request failed (HTTP ${r.status}).`)
}

const json = (method, url, body) =>
  request(url, { method, headers: { 'Content-Type': 'application/json' },
               body: JSON.stringify(body) }).then(ok)

export const getMeta = () => request('/api/meta').then(ok)
export const getDecks = () => request('/api/decks').then(ok)
export const getDeck = id => request('/api/deck/' + id).then(ok)
export const createDeck = (deck_title, template) =>
  json('POST', '/api/deck', { deck_title, template })
export const deleteDeck = id => request('/api/deck/' + id, { method: 'DELETE' })
export const downloadUrl = id => connection ? connection.base + '/file/deck.pptx?download=1&ticket=' + encodeURIComponent(connection.ticket) : '/api/file/' + id + '.pptx'
export const getVideo = id => request(`/api/deck/${id}/video`).then(ok)
export const createVideo = (id, voice = 'en-US-AriaNeural') =>
  json('POST', `/api/deck/${id}/video`, { voice })

/** Autosave. Writes the document only; the .pptx is built when it is downloaded, so a
 *  save is fast and an export is never one edit behind. */
const saves = new Map(), revisions = new Map()
export function saveDeck(id, deck) {
  const next = (saves.get(id) || Promise.resolve()).catch(() => {}).then(async () => {
    const saved = await json('PUT', '/api/deck/' + id, { deck: connection ? { ...deck, revision: revisions.get(id) ?? deck.revision } : deck })
    if (connection) revisions.set(id, saved.revision)
    return saved
  })
  saves.set(id, next)
  return next
}

export const askAI = (id, ask, selection) =>
  json('POST', `/api/deck/${id}/ai`, { ask, selection })

export const searchImages = (query, count = 8) =>
  json('POST', '/api/images', { query, count })

export const getVersions = id => request(`/api/deck/${id}/versions`).then(ok)
export const restoreVersion = (id, version) =>
  request(`/api/deck/${id}/restore/${version}`, { method: 'POST' }).then(ok)

/** An image by its address. The backend fetches it, checks it really is a picture and
 *  stores it, so what comes back is our own asset -- a saved deck never points at
 *  somebody else's CDN, which is the only version of this that still works next month. */
export const imageFromUrl = url => json('POST', '/api/upload/url', { url })

export function uploadImage(file) {
  const body = new FormData()
  body.append('file', file)
  return request('/api/upload', { method: 'POST', body }).then(ok)
}

/**
 * POST /api/generate and hand back every SSE event as it lands.
 *
 * EventSource cannot POST a body, so this is fetch + a two-line frame parser. Events
 * arrive as `event: name\ndata: {...}\n\n`; a chunk can split a frame anywhere, hence
 * the buffer.
 */
export async function generate(body, onEvent, signal) {
  const r = await request('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}))
    throw new Error(detail.detail || `Generation failed (HTTP ${r.status}).`)
  }
  const reader = r.body.getReader()
  const decode = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decode.decode(value, { stream: true })
    let cut
    while ((cut = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, cut)
      buf = buf.slice(cut + 2)
      const name = /^event: (.*)$/m.exec(frame)
      const data = /^data: (.*)$/m.exec(frame)
      if (name && data) onEvent(name[1], JSON.parse(data[1]))
    }
  }
}
