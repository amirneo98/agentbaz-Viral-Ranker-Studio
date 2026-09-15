import { DragDropContext, Droppable, Draggable } from '@hello-pangea/dnd'
import { useStudioStore } from '../store/useStudioStore'
import TrimSlider from './TrimSlider'
import { fmtTime } from '../lib/format'

function Thumb({ src, title }) {
  return (
    <div className="relative h-14 w-24 shrink-0 overflow-hidden rounded-lg bg-base ring-1 ring-edge">
      {src ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          onError={(e) => {
            e.currentTarget.style.display = 'none'
          }}
          className="h-full w-full object-cover"
        />
      ) : null}
      <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-muted/60">
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor" aria-hidden="true">
          <path d="M8 5.14v14l11-7-11-7z" />
        </svg>
      </span>
    </div>
  )
}

function TimelineRow({ entry, rank, index, isFinale, video, onTrim, onRemove }) {
  return (
    <Draggable draggableId={`tl-${entry.videoId}`} index={index}>
      {(provided, snapshot) => (
        <li
          ref={provided.innerRef}
          {...provided.draggableProps}
          className={`panel flex items-center gap-3 p-3 transition ${
            snapshot.isDragging ? 'shadow-lift ring-1 ring-accent-end/50 !bg-[#232323]' : ''
          }`}
        >
          {/* drag handle */}
          <button
            type="button"
            className="flex h-9 w-6 cursor-grab flex-col items-center justify-center gap-0.5 rounded text-muted/70 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-end active:cursor-grabbing"
            title="Drag to reorder"
            aria-label={`Reorder ${entry.title} (currently rank ${rank})`}
            {...provided.dragHandleProps}
          >
            <svg viewBox="0 0 10 16" className="h-4 w-2.5 fill-current" aria-hidden="true">
              <circle cx="2" cy="2" r="1.3" />
              <circle cx="8" cy="2" r="1.3" />
              <circle cx="2" cy="8" r="1.3" />
              <circle cx="8" cy="8" r="1.3" />
              <circle cx="2" cy="14" r="1.3" />
              <circle cx="8" cy="14" r="1.3" />
            </svg>
          </button>

          {/* rank badge — top row is the highest rank (plays first), bottom is #1 (finale) */}
          <div
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-sm font-extrabold ${
              isFinale
                ? 'bg-gradient-to-br from-accent-start to-accent-end text-white shadow-panel'
                : 'bg-white/5 text-ink ring-1 ring-edge'
            }`}
            title={isFinale ? 'Finale — the #1 clip' : `Rank #${rank}`}
            aria-label={`Rank ${rank}`}
          >
            {rank}
          </div>

          <Thumb src={video ? video.thumbnail_url : null} title={entry.title} />

          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <p className="truncate text-sm font-semibold text-ink" title={entry.title}>
                {entry.title}
              </p>
              <span className="shrink-0 font-mono text-[11px] text-accent-end">
                {fmtTime(Math.max(0, entry.end - entry.start))}
              </span>
            </div>
            <div className="mt-1.5">
              <TrimSlider
                duration={video ? video.duration : Math.max(entry.end, 1)}
                start={entry.start}
                end={entry.end}
                onChange={(s, e) => onTrim(entry.videoId, s, e)}
              />
            </div>
          </div>

          <button
            type="button"
            className="btn-ghost !px-2 self-start"
            title="Remove from timeline"
            aria-label={`Remove ${entry.title} from timeline`}
            onClick={() => onRemove(entry.videoId)}
          >
            ✕
          </button>
        </li>
      )}
    </Draggable>
  )
}

export default function Timeline() {
  const timeline = useStudioStore((s) => s.timeline)
  const videos = useStudioStore((s) => s.videos)
  const reorderTimeline = useStudioStore((s) => s.reorderTimeline)
  const removeFromTimeline = useStudioStore((s) => s.removeFromTimeline)
  const setTrim = useStudioStore((s) => s.setTrim)

  const n = timeline.length
  const videoById = new Map(videos.map((v) => [v.id, v]))

  const onDragEnd = (result) => {
    if (!result.destination) return
    reorderTimeline(result.source.index, result.destination.index)
  }

  return (
    <section className="panel flex flex-col p-4" aria-label="Ranked timeline">
      <header className="mb-1 flex items-baseline justify-between">
        <h2 className="text-sm font-bold uppercase tracking-wider text-ink">Timeline</h2>
        <span className="text-xs text-muted">{n} clip{n === 1 ? '' : 's'}</span>
      </header>
      <p className="mb-3 text-[11px] text-muted">
        Top plays first — the countdown runs N → 1, and the bottom slot is the #1 finale. Drag to
        reorder.
      </p>

      {n === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
          <span className="text-3xl" aria-hidden="true">🏆</span>
          <p className="text-sm font-medium text-ink">Timeline is empty</p>
          <p className="max-w-xs text-xs text-muted">
            Add clips from the library on the left. The bottom clip is your #1 — save the best for
            last.
          </p>
        </div>
      ) : (
        <DragDropContext onDragEnd={onDragEnd}>
          <Droppable droppableId="timeline">
            {(provided) => (
              <ol
                ref={provided.innerRef}
                {...provided.droppableProps}
                className="flex flex-col gap-2.5"
              >
                {timeline.map((entry, i) => (
                  <TimelineRow
                    key={entry.videoId}
                    entry={entry}
                    index={i}
                    rank={n - i}
                    isFinale={i === n - 1}
                    video={videoById.get(entry.videoId)}
                    onTrim={setTrim}
                    onRemove={removeFromTimeline}
                  />
                ))}
                {provided.placeholder}
              </ol>
            )}
          </Droppable>
        </DragDropContext>
      )}
    </section>
  )
}
