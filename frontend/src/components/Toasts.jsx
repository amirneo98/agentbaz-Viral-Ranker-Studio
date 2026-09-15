import { useEffect } from 'react'
import { useStudioStore } from '../store/useStudioStore'

const KIND_STYLES = {
  success: 'border-emerald-500/40 bg-emerald-950/80 text-emerald-200',
  error: 'border-red-500/40 bg-red-950/80 text-red-200',
  info: 'border-secondary/40 bg-blue-950/80 text-blue-200'
}

const KIND_ICONS = { success: '✓', error: '✕', info: 'ℹ' }

function Toast({ id, kind, message }) {
  const dismiss = useStudioStore((s) => s.dismissToast)

  useEffect(() => {
    const timer = setTimeout(() => dismiss(id), 6000)
    return () => clearTimeout(timer)
  }, [id, dismiss])

  return (
    <div
      role="status"
      className={`pointer-events-auto flex w-80 items-start gap-2.5 rounded-lg border px-3.5 py-2.5 text-sm shadow-lift backdrop-blur ${KIND_STYLES[kind] || KIND_STYLES.info}`}
    >
      <span className="mt-0.5 font-bold" aria-hidden="true">
        {KIND_ICONS[kind] || KIND_ICONS.info}
      </span>
      <p className="flex-1 leading-snug">{message}</p>
      <button
        type="button"
        title="Dismiss notification"
        aria-label="Dismiss notification"
        onClick={() => dismiss(id)}
        className="rounded p-0.5 text-current/60 hover:text-current focus:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
      >
        ✕
      </button>
    </div>
  )
}

export default function Toasts() {
  const toasts = useStudioStore((s) => s.toasts)
  if (toasts.length === 0) return null
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex flex-col gap-2">
      {toasts.map((t) => (
        <Toast key={t.id} {...t} />
      ))}
    </div>
  )
}
