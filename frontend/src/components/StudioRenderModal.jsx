import { useEffect } from 'react'
import { useStudioV2Store } from '../store/useStudioV2Store'
import useV2TaskPolling from '../hooks/useV2TaskPolling'
import { fmtTime } from '../lib/format'

const STATUS_LABEL = {
  PENDING: 'Queued…',
  PROCESSING: 'Rendering',
  SUCCESS: 'Done',
  FAILED: 'Failed'
}

/**
 * v1.2 render modal: polls the task every 1.5s, shows progress, and on
 * SUCCESS presents the final video player + download link.
 */
export default function StudioRenderModal() {
  const task = useStudioV2Store((s) => s.renderTask)
  const close = useStudioV2Store((s) => s.closeRenderModal)
  const syncRenderTask = useStudioV2Store((s) => s.syncRenderTask)

  useV2TaskPolling(task ? task.id : null, !!task, (_id, payload) => syncRenderTask(payload))

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
  const p = Math.min(100, Math.max(0, task.progress))

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Render progress"
    >
      <div className="panel w-full max-w-md p-6 shadow-lift" data-testid="render-modal">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-bold text-ink">Rendering Compilation</h2>
          <span
            className={`rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
              failed
                ? 'bg-red-500/20 text-red-300'
                : done
                  ? 'bg-emerald-500/20 text-emerald-300'
                  : 'bg-blue-500/20 text-blue-300'
            }`}
          >
            {STATUS_LABEL[task.status] || task.status}
          </span>
        </div>

        {!done && !failed && (
          <div className="flex flex-col gap-3 py-2">
            <div
              className="h-2 w-full overflow-hidden rounded-full bg-edge"
              role="progressbar"
              aria-valuenow={Math.round(p)}
              aria-valuemin={0}
              aria-valuemax={100}
              data-testid="render-progress"
            >
              <div
                className="h-full rounded-full bg-gradient-to-r from-accent-start to-accent-end transition-all duration-500"
                style={{ width: `${p}%` }}
              />
            </div>
            <p className="text-center font-mono text-sm text-muted" aria-live="polite">
              {Math.round(p)}% — keep this tab open
            </p>
          </div>
        )}

        {failed && (
          <div className="rounded-lg border border-red-500/30 bg-red-950/40 p-4">
            <p className="mb-1 text-sm font-semibold text-red-300">Render failed</p>
            <p className="text-xs leading-relaxed text-red-200/80">
              {task.error || 'The renderer hit an unexpected error.'}
            </p>
          </div>
        )}

        {done && result && (
          <div className="flex flex-col gap-4 py-1">
            <div className="overflow-hidden rounded-lg bg-black ring-1 ring-edge">
              <video
                controls
                playsInline
                src={result.preview_url || result.download_url}
                className="mx-auto max-h-[38vh] w-auto"
                data-testid="render-result-video"
              >
                Your browser does not support embedded video.
              </video>
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              {typeof result.duration === 'number' && (
                <>
                  <dt className="text-muted">Duration</dt>
                  <dd className="text-right font-mono text-ink">{fmtTime(result.duration)}</dd>
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
                data-testid="render-download"
              >
                ⬇ Download MP4
              </a>
              <button type="button" className="btn-ghost" onClick={close}>
                Close
              </button>
            </div>
          </div>
        )}

        {!done && (
          <div className="mt-3 flex justify-end">
            <button type="button" className="btn-ghost" onClick={close}>
              {failed ? 'Close' : 'Hide'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
