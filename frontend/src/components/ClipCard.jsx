import { useState } from 'react'
import { fmtTime, fmtDims } from '../lib/format'

const GRADIENTS = [
  'from-rose-500/30 to-orange-500/30',
  'from-blue-500/30 to-violet-500/30',
  'from-emerald-500/30 to-teal-500/30',
  'from-amber-500/30 to-pink-500/30',
  'from-indigo-500/30 to-sky-500/30',
  'from-fuchsia-500/30 to-red-500/30'
]

function hashHue(seed) {
  let h = 0
  const s = String(seed)
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return h
}

export default function ClipCard({ video, onTimeline, actions }) {
  const [thumbFailed, setThumbFailed] = useState(false)
  const gradient = GRADIENTS[hashHue(video.id) % GRADIENTS.length]

  return (
    <article className="panel group flex flex-col overflow-hidden transition hover:border-muted/40">
      <div className="relative aspect-video w-full overflow-hidden bg-base">
        {video.thumbnail_url && !thumbFailed ? (
          <img
            src={video.thumbnail_url}
            alt={`Thumbnail for ${video.title}`}
            loading="lazy"
            onError={() => setThumbFailed(true)}
            className="h-full w-full object-cover"
          />
        ) : (
          <div
            className={`flex h-full w-full items-center justify-center bg-gradient-to-br ${gradient}`}
            aria-hidden="true"
          >
            <svg viewBox="0 0 24 24" className="h-8 w-8 text-white/50" fill="currentColor">
              <path d="M8 5.14v14l11-7-11-7z" />
            </svg>
          </div>
        )}
        <span className="absolute bottom-1.5 right-1.5 rounded bg-black/75 px-1.5 py-0.5 font-mono text-[11px] text-white">
          {fmtTime(video.duration)}
        </span>
        {!video.has_audio && (
          <span
            className="absolute left-1.5 top-1.5 rounded bg-black/75 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-300"
            title="This clip has no audio track"
          >
            No audio
          </span>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-1 p-3">
        <h3 className="line-clamp-2 text-sm font-semibold leading-snug text-ink" title={video.title}>
          {video.title}
        </h3>
        <p className="text-xs text-muted">
          {fmtDims(video.width, video.height)} · {fmtTime(video.duration)}
          {video.has_audio ? ' · audio' : ' · silent'}
        </p>
        <div className="mt-auto flex items-center gap-2 pt-2">
          <button
            type="button"
            className="btn-secondary flex-1 !py-1.5 text-xs"
            disabled={onTimeline}
            title={onTimeline ? 'Already on the timeline' : 'Add this clip to the timeline'}
            onClick={() => !onTimeline && actions.add(video.id)}
          >
            {onTimeline ? '✓ On Timeline' : '+ Add to Timeline'}
          </button>
          <button
            type="button"
            className="btn-ghost !px-2"
            title="Delete this clip from the library"
            aria-label={`Delete ${video.title}`}
            onClick={() => actions.remove(video.id)}
          >
            🗑
          </button>
        </div>
      </div>
    </article>
  )
}
