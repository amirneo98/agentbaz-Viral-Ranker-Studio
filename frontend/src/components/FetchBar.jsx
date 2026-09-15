import { useEffect, useState } from 'react'
import { useStudioStore } from '../store/useStudioStore'
import useTaskPolling from '../hooks/useTaskPolling'

const STATUS_LABEL = {
  PENDING: 'Queued',
  PROCESSING: 'Fetching',
  SUCCESS: 'Fetched',
  FAILED: 'Failed'
}

/**
 * Invisible per-task watcher: drives polling for one fetch task and raises
 * an error toast when it fails. Lets several fetches run concurrently.
 */
function FetchTaskWatcher({ taskId }) {
  const task = useStudioStore((s) => s.fetchTasks[taskId])
  const pushToast = useStudioStore((s) => s.pushToast)
  useTaskPolling(taskId, true)

  const failed = task && task.status === 'FAILED'
  useEffect(() => {
    if (failed && task) {
      pushToast('error', `Fetch failed: ${task.error || 'unknown error'}`)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [failed])

  return null
}

export default function FetchBar() {
  const [url, setUrl] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [validation, setValidation] = useState('')

  const fetchUrl = useStudioStore((s) => s.fetchUrl)
  const fetchTasks = useStudioStore((s) => s.fetchTasks)
  const clearFetchTask = useStudioStore((s) => s.clearFetchTask)
  const pushToast = useStudioStore((s) => s.pushToast)

  const activeIds = Object.values(fetchTasks)
    .filter((t) => t.status === 'PENDING' || t.status === 'PROCESSING')
    .map((t) => t.id)
  const lastId = useStudioStore((s) => s.lastFetchId)
  const lastTask = lastId ? fetchTasks[lastId] : null

  const onSubmit = async (e) => {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed) return
    if (!/^https?:\/\/\S+$/.test(trimmed)) {
      setValidation('Enter a valid http(s) video URL.')
      return
    }
    setValidation('')
    setSubmitting(true)
    try {
      await fetchUrl(trimmed)
      setUrl('')
    } catch (err) {
      pushToast('error', `Couldn't start fetch: ${err.message}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="panel px-4 py-3.5" aria-label="Fetch a video">
      {/* watchers keep every in-flight task polling */}
      {activeIds.map((id) => (
        <FetchTaskWatcher key={id} taskId={id} />
      ))}

      <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden="true">
            🔗
          </span>
          <input
            type="url"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              if (validation) setValidation('')
            }}
            placeholder="Paste a video URL (YouTube, TikTok, direct file…)"
            aria-label="Video URL"
            aria-invalid={validation ? 'true' : 'false'}
            className="field pl-9"
          />
        </div>
        <button
          type="submit"
          disabled={submitting || url.trim().length === 0}
          className="btn-primary sm:w-36"
          title="Download and analyze the video"
        >
          {submitting ? 'Starting…' : 'Fetch Video'}
        </button>
      </form>

      {validation && <p className="mt-2 text-xs text-red-400">{validation}</p>}

      {lastTask && (
        <div className="mt-3" aria-live="polite">
          <div className="mb-1.5 flex items-center justify-between text-xs">
            <span className="font-medium text-muted">
              {STATUS_LABEL[lastTask.status] || lastTask.status}
              <span className="ml-1.5 text-muted/60">{lastTask.url}</span>
            </span>
            <span className="flex items-center gap-2">
              <span className="font-mono text-ink">{Math.round(lastTask.progress)}%</span>
              {(lastTask.status === 'SUCCESS' || lastTask.status === 'FAILED') && (
                <button
                  type="button"
                  className="btn-ghost !px-1.5 !py-0.5 text-xs"
                  title="Clear this status"
                  onClick={() => clearFetchTask(lastTask.id)}
                >
                  Clear
                </button>
              )}
            </span>
          </div>
          <div
            className="h-1.5 w-full overflow-hidden rounded-full bg-edge"
            role="progressbar"
            aria-valuenow={Math.round(lastTask.progress)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full bg-gradient-to-r from-accent-start to-accent-end transition-all duration-500"
              style={{ width: `${Math.min(100, Math.max(0, lastTask.progress))}%` }}
            />
          </div>
        </div>
      )}
    </section>
  )
}
