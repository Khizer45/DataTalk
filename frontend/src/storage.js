// Uploaded datasets this browser knows about, with their access tokens. Kept in localStorage as a convenience:
// it can be empty (private windows, cleared site data), and the server is the source of truth.
const KEY = 'datatalk.datasets'

function store() {
  try { return globalThis.localStorage ?? null } catch { return null }
}

export function loadDatasets(now = Date.now()) {
  try {
    const saved = JSON.parse(store()?.getItem(KEY) ?? '[]')
    return Array.isArray(saved) ? saved.filter(item => item?.id && item?.access_token && Date.parse(item.expires_at) > now) : []
  } catch {
    return []
  }
}

export function saveDatasets(datasets) {
  try { store()?.setItem(KEY, JSON.stringify(datasets)) } catch { /* storage unavailable; keep working in memory */ }
}

export function hoursLeft(expiresAt, now = Date.now()) {
  return Math.max(0, Math.round((Date.parse(expiresAt) - now) / 3_600_000))
}

// Access keys for connected databases, kept only for this browser tab (sessionStorage), never in localStorage.
const SOURCE_KEYS = 'datatalk.sourceKeys'

function tabStore() {
  try { return globalThis.sessionStorage ?? null } catch { return null }
}

export function loadSourceKeys() {
  try {
    const saved = JSON.parse(tabStore()?.getItem(SOURCE_KEYS) ?? '{}')
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {}
  } catch {
    return {}
  }
}

export function saveSourceKeys(keys) {
  try { tabStore()?.setItem(SOURCE_KEYS, JSON.stringify(keys)) } catch { /* storage unavailable; keep in memory */ }
}
