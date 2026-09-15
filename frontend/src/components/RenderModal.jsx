import { useEffect } from 'react'
import useTaskPolling from '../hooks/useTaskPolling'
import { useStudioStore } from '../store/useStudioStore'
import StatusBadge from './StatusBadge'
import { fmtTime } from '../lib/format'

const STATUS_LABEL = {
  PENDING: 'Queued…',
  PROCESSING: 'Rendering',
  SUCCESS: 'Done',
  FAILED: 'Failed'
}

function ProgressRing({ progress, failed }) {
  const R = 52
  const C = 2 * Math.PI * R
  const p = Math.min(100, Math.max(0, progress))
  return (
    <div className="relative h-32 w-32" role="progressbar" aria-valuenow={Math.round(p)} aria-valuemin={0} aria-valuemax={100}>
      <svg viewBox="0 0 120 120" className="h-full w-full -rotate-90">
        <circle cx="60" cy="60" r={R} fill="none" stroke="#2A2A2A" strokeWidth="8" />
        <circle
          cx="60"
          cy="60"
          r={R}
          fill="none"
          stroke={failed ? '#EF4444' : 'url(#ringGrad)'}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={C}
          strokeDashoffset={C - (C * p) / 100}
          className="transition-all duration-500"
        />
        <defs>
          <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#FF4D4D" />
            <stop offset="100%" stopColor="#FF8A4D" />
          </linearGradient>
        </defs>
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-extrabold text-ink">{Math.round(p)}%</span>
      </div>
    </div>
  )
}

export default function RenderModal() {
  const task = useStudioStore((s) => s.renderTask)
  const close = useStudioStore((s) => s.closeRenderModal)

  useTaskPolling(task ? task.id : null, !!task)

  // Close on Escape
  useEffect(() => {
    if (!task) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [task, close])

  if (!task) return null

  const failed = task.status === 'FAILED'
  const done = task.status === 'SUCCESS'
  const result = task.result

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Render progress"
    >
      <div className="panel w-full max-w-md p-6 shadow-lift">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-bold text-ink">Rendering Compilation</h2>
          <StatusBadge status={task.status} />
        </div>

        {!done && !failed && (
          <div className="flex flex-col items-center gap-4 py-2">
            <ProgressRing progress={task.progress} failed={false} />
            <p className="text-sm text-muted" aria-live="polite">
              {STATUS_LABEL[task.status] || task.status} — keep this tab open
            </p>
          </div>
        )}

        {failed && (
          <div className="flex flex-col gap-4 py-2">
            <div className="rounded-lg border border-red-500/30 bg-red-950/40 p-4">
              <p className="mb-1 text-sm font-semibold text-red-300">Render failed</p>
              <p className="text-xs leading-relaxed text-red-200/80">
                {task.error || 'The renderer hit an unexpected error. Check the server logs.'}
              </p>
            </div>
          </div>
        )}

        {done && result && (
          <div className="flex flex-col gap-4 py-1">
            <div className="overflow-hidden rounded-lg bg-black ring-1 ring-edge">
              <video
                controls
                playsInline
                src={result.preview_url}
                className="mx-auto aspect-[9/16] max-h-[38vh] w-auto"
              >
                Your browser does not support embedded video.
              </video>
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              <dt className="text-muted">Duration</dt>
              <dd className="text-right font-mono text-ink">{fmtTime(result.duration)}</dd>
              {result.encoder_used && (
                <>
                  <dt className="text-muted">Encoder</dt>
                  <dd className="text-right font-mono text-ink">{result.encoder_used}</dd>
                </>
              )}
              {result.output_file && (
                <>
                  <dt className="text-muted">Output</dt>
                  <dd className="truncate text-right font-mono text-ink" title={result.output_file}>
                    {result.output_file}
                  </dd>
                </>
              )}
            </dl>
            <div className="flex items-center justify-between">
              <a
                href={result.download_url}
                download
                className="btn-primary"
                title="Download the rendered compilation"
              >
                <svg viewBox="0 0 24 24" className="h-4 w-4 fill-current" aria-hidden="true">
                  <path d="M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z" />
                </svg>
                Download MP4
              </a>
              <button type="button" className="btn-ghost" onClick={close} title="Close this dialog">
                Close
              </button>
            </div>
          </div>
        )}

        {!done && (
          <div className="mt-2 flex justify-end">
            <button type="button" className="btn-ghost" onClick={close} title="Close this dialog">
              {failed ? 'Close' : 'Hide'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
