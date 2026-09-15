import { useState } from 'react'
import { useStudioV2Store } from '../store/useStudioV2Store'

const KIND_STYLE = {
  success: 'border-emerald-500/40 bg-emerald-950/70 text-emerald-200',
  error: 'border-red-500/40 bg-red-950/70 text-red-200',
  info: 'border-blue-500/40 bg-blue-950/70 text-blue-200'
}
const KIND_ICON = { success: '✓', error: '✕', info: 'ℹ' }

/** Toast stack for the v1.2 studio store. */
export default function StudioToasts() {
  const toasts = useStudioV2Store((s) => s.toasts)
  const dismiss = useStudioV2Store((s) => s.dismissToast)

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto flex items-start gap-2 rounded-lg border px-3 py-2 text-xs shadow-lift backdrop-blur ${
            KIND_STYLE[t.kind] || KIND_STYLE.info
          }`}
        >
          <span className="mt-px font-bold">{KIND_ICON[t.kind] || 'ℹ'}</span>
          <span className="flex-1 leading-relaxed">{t.message}</span>
          <button
            type="button"
            className="opacity-60 hover:opacity-100"
            aria-label="Dismiss notification"
            onClick={() => dismiss(t.id)}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

/** v1.2 URL ingestion bar — calls /api/stream-info/ then auto-creates a clip. */
export function StudioUrlBar() {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const busy = useStudioV2Store((s) => s.streamInfoBusy)
  const ingestUrl = useStudioV2Store((s) => s.ingestUrl)

  const onSubmit = async (e) => {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed) return
    if (!/^https?:\/\/\S+$/.test(trimmed)) {
      setError('Enter a valid http(s) video URL.')
      return
    }
    setError('')
    try {
      await ingestUrl(trimmed)
      setUrl('')
    } catch (err) {
      setError(err.message || 'Failed to extract stream info.')
    }
  }

  return (
    <section className="panel px-4 py-3" aria-label="Add a clip by URL">
      <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <span
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
            aria-hidden="true"
          >
            🔗
          </span>
          <input
            type="url"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              if (error) setError('')
            }}
            placeholder="Paste a video URL (YouTube, TikTok, direct file…)"
            aria-label="Video URL"
            className="field pl-9"
            data-testid="studio-url-input"
            disabled={busy}
          />
        </div>
        <button
          type="submit"
          disabled={busy || url.trim().length === 0}
          className="btn-primary sm:w-44"
          data-testid="studio-url-submit"
        >
          {busy ? (
            <span className="flex items-center gap-2">
              <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              Extracting…
            </span>
          ) : (
            'Add to Ranking'
          )}
        </button>
      </form>
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
    </section>
  )
}
