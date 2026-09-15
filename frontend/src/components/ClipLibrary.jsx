import { useStudioStore } from '../store/useStudioStore'
import ClipCard from './ClipCard'

export default function ClipLibrary() {
  const videos = useStudioStore((s) => s.videos)
  const loading = useStudioStore((s) => s.videosLoading)
  const timeline = useStudioStore((s) => s.timeline)
  const addToTimeline = useStudioStore((s) => s.addToTimeline)
  const deleteVideo = useStudioStore((s) => s.deleteVideo)

  const onTimeline = new Set(timeline.map((t) => t.videoId))
  const actions = { add: addToTimeline, remove: deleteVideo }

  return (
    <section className="panel flex min-h-[320px] flex-col p-4" aria-label="Clip library">
      <header className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-bold uppercase tracking-wider text-ink">Clip Library</h2>
        <span className="text-xs text-muted">
          {videos.length} clip{videos.length === 1 ? '' : 's'}
        </span>
      </header>

      {loading && videos.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-16 text-sm text-muted">
          <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-edge border-t-accent-end" />
          Loading clips…
        </div>
      ) : videos.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-16 text-center">
          <span className="text-3xl" aria-hidden="true">🎬</span>
          <p className="text-sm font-medium text-ink">No clips yet</p>
          <p className="max-w-xs text-xs text-muted">
            Paste a video URL above to get started. Fetched clips show up here, ready to rank.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 overflow-y-auto pr-1 sm:grid-cols-2 xl:grid-cols-3">
          {videos.map((v) => (
            <ClipCard key={v.id} video={v} onTimeline={onTimeline.has(v.id)} actions={actions} />
          ))}
        </div>
      )}
    </section>
  )
}
