// Empty in local dev (Vite proxies /api); set VITE_API_BASE_URL to the API's URL when the frontend is hosted separately.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

function errorMessage(data, status) {
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    if (detail.some(item => item?.loc?.includes('question'))) return 'Please enter a question of at least 3 characters.'
    return 'The request was not valid. Please check your question and try again.'
  }
  if (status === 429) return 'Too many questions in a short time. Please wait a minute.'
  if (status >= 500) return 'DataTalk is unavailable right now. Please try again shortly.'
  return 'Something went wrong. Please try again.'
}

export async function api(path, options) {
  let response
  try {
    response = await fetch(`${API_BASE}${path}`, options)
  } catch {
    throw new Error('Could not reach the DataTalk API. Check your connection and try again.')
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(errorMessage(data, response.status))
  if (data === null) throw new Error('The API returned an unexpected response.')
  return data
}
