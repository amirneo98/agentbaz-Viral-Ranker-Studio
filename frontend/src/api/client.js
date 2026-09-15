// All media/API URLs from the backend are relative (e.g. /media/videos/x.mp4),
// same-origin — so the API base is empty and paths are used as-is.
export const API_BASE = ''

async function request(path, opts = {}) {
  const { body, ...rest } = opts
  const headers = { Accept: 'application/json', ...(rest.headers || {}) }
  const init = { ...rest, headers }
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }

  let res
  try {
    res = await fetch(API_BASE + path, init)
  } catch (err) {
    throw new Error(`Network error: ${err.message}`)
  }

  if (res.status === 204) return null

  let data = null
  const text = await res.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }

  if (!res.ok) {
    const detail =
      (data && (data.detail || data.error || data.message)) ||
      `Request failed (${res.status} ${res.statusText})`
    throw new Error(detail)
  }
  return data
}

export default request
