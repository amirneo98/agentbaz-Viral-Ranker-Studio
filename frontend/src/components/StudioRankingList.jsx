import { useEffect, useMemo, useRef, useState } from 'react'
import { DragDropContext, Droppable, Draggable } from '@hello-pangea/dnd'
import { useStudioV2Store, DEFAULT_STYLE } from '../store/useStudioV2Store'
import useV2TaskPolling from '../hooks/useV2TaskPolling'
import { fmtTime } from '../lib/format'

const FONTS = ['Archivo Black', 'Bebas Neue', 'Rubik', 'Montserrat', 'Vazirmatn']

const HD_LABEL = {
  pending: 'Not fetched',
  downloading: 'Downloading',
  ready: 'HD ready',
  failed: 'Failed'
}
const HD_CLASS = {
  pending: 'bg-edge text-muted',
  downloading: 'bg-blue-500/20 text-blue-300',
  ready: 'bg-emerald-500/20 text-emerald-300',
  failed: 'bg-red-500/20 text-red-300'
}

/* ---------------- trim slider (dual native ranges) ---------------- */
function TrimRange({ duration, start, end, onChange }) {
  const max = duration > 0 ? duration : 1
  const lo = Math.min(Math.max(start, 0), max)
  const hi = Math.min(Math.max(end, lo), max)
  const loPct = (lo / max) * 100
  const hiPct = (hi / max) * 100
  return (
    <div className="w-full" data-testid={`trim-${Math.round(lo)}-${Math.round(hi)}`}>
      <div className="relative h-7 select-none">
        <div className="absolute left-0 right-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-edge" />
        <div
          className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-gradient-to-r from-accent-start to-accent-end"
          style={{ left: `${loPct}%`, width: `${Math.max(0, hiPct - loPct)}%` }}
        />
        <input
          type="range"
          className="range-dual"
          min={0}
          max={max}
          step={0.1}
          value={lo}
          aria-label="Trim start"
          onChange={(e) => onChange(Math.min(Number(e.target.value), hi - 0.5), hi)}
        />
        <input
          type="range"
          className="range-dual"
          min={0}
          max={max}
          step={0.1}
          value={hi}
          aria-label="Trim end"
          onChange={(e) => onChange(lo, Math.max(Number(e.target.value), lo + 0.5))}
        />
      </div>
      <div className="flex items-center justify-between font-mono text-[11px] text-muted">
        <span>
          in <span className="text-ink">{fmtTime(lo)}</span>
        </span>
        <span className="text-accent-end">{fmtTime(Math.max(0, hi - lo))}</span>
        <span>
          out <span className="text-ink">{fmtTime(hi)}</span>
        </span>
      </div>
    </div>
  )
}

/* ---------------- typography toolbar ---------------- */
function TypographyToolbar({ clip, onStyle }) {
  const s = { ...DEFAULT_STYLE, ...(clip.style || {}) }
  const set = (patch) => onStyle({ ...s, ...patch })

  return (
    <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-edge bg-base/60 p-3">
      {/* font */}
      <div className="col-span-2">
        <label className="label !mb-1" htmlFor={`font-${clip.id}`}>
          Font
        </label>
        <select
          id={`font-${clip.id}`}
          className="field !py-1.5 text-xs"
          value={s.font}
          onChange={(e) => set({ font: e.target.value })}
          data-testid="font-select"
        >
          {FONTS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </div>

      {/* font size */}
      <div>
        <span className="label !mb-1">Font size</span>
        <div className="flex items-center gap-2">
          <input
            type="range"
            className="slider slider-fill"
            style={{ '--fill': `${((s.fontSize - 24) / 96) * 100}%` }}
            min={24}
            max={120}
            step={1}
            value={s.fontSize}
            aria-label="Font size"
            data-testid="fontsize-slider"
            onChange={(e) => set({ fontSize: Number(e.target.value) })}
          />
          <span className="w-8 text-right font-mono text-[11px] text-ink">{s.fontSize}</span>
        </div>
      </div>

      {/* text color */}
      <div>
        <span className="label !mb-1">Text color</span>
        <div className="flex items-center gap-2">
          <input
            type="color"
            className="h-7 w-10 cursor-pointer rounded border border-edge bg-base"
            value={s.textColor}
            aria-label="Text color"
            data-testid="textcolor-picker"
            onChange={(e) => set({ textColor: e.target.value })}
          />
          <span className="font-mono text-[11px] text-muted">{s.textColor}</span>
        </div>
      </div>

      {/* stroke */}
      <div>
        <span className="label !mb-1">Stroke</span>
        <div className="flex items-center gap-2">
          <input
            type="color"
            className="h-7 w-10 cursor-pointer rounded border border-edge bg-base"
            value={s.strokeColor}
            aria-label="Stroke color"
            onChange={(e) => set({ strokeColor: e.target.value })}
          />
          <input
            type="range"
            className="slider slider-fill"
            style={{ '--fill': `${(s.strokeWidth / 12) * 100}%` }}
            min={0}
            max={12}
            step={1}
            value={s.strokeWidth}
            aria-label="Stroke width"
            data-testid="strokewidth-slider"
            onChange={(e) => set({ strokeWidth: Number(e.target.value) })}
          />
          <span className="w-8 text-right font-mono text-[11px] text-ink">{s.strokeWidth}</span>
        </div>
      </div>

      {/* shadow + position */}
      <div className="flex items-end gap-4">
        <div>
          <span className="label !mb-1">Shadow</span>
          <button
            type="button"
            role="switch"
            aria-checked={!!s.shadow}
            aria-label="Text shadow"
            data-testid="shadow-toggle"
            onClick={() => set({ shadow: !s.shadow })}
            className={`relative h-6 w-11 rounded-full transition ${
              s.shadow ? 'bg-gradient-to-r from-accent-start to-accent-end' : 'bg-edge'
            }`}
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
                s.shadow ? 'left-[22px]' : 'left-0.5'
              }`}
            />
          </button>
        </div>
        <div className="flex-1">
          <span className="label !mb-1">Position</span>
          <div
            className="flex overflow-hidden rounded-lg border border-edge"
            role="group"
            aria-label="Text position"
            data-testid="position-group"
          >
            {['top', 'center', 'bottom'].map((p) => (
              <button
                key={p}
                type="button"
                className={`flex-1 px-2 py-1 text-[11px] font-semibold capitalize transition ${
                  s.textPosition === p ? 'bg-accent-start text-white' : 'text-muted hover:bg-white/5'
                }`}
                onClick={() => set({ textPosition: p })}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* badge */}
      <div className="col-span-2 flex items-center gap-4">
        <div>
          <span className="label !mb-1">Badge color</span>
          <div className="flex items-center gap-2">
            <input
              type="color"
              className="h-7 w-10 cursor-pointer rounded border border-edge bg-base"
              value={s.badgeBg}
              aria-label="Badge background color"
              data-testid="badgebg-picker"
              onChange={(e) => set({ badgeBg: e.target.value })}
            />
            <span className="font-mono text-[11px] text-muted">{s.badgeBg}</span>
          </div>
        </div>
        <div className="flex-1">
          <span className="label !mb-1">Badge opacity</span>
          <div className="flex items-center gap-2">
            <input
              type="range"
              className="slider slider-fill"
              style={{ '--fill': `${s.badgeOpacity * 100}%` }}
              min={0}
              max={1}
              step={0.05}
              value={s.badgeOpacity}
              aria-label="Badge opacity"
              data-testid="badgeopacity-slider"
              onChange={(e) => set({ badgeOpacity: Number(e.target.value) })}
            />
            <span className="w-8 text-right font-mono text-[11px] text-ink">
              {Math.round(s.badgeOpacity * 100)}%
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ---------------- one ranking card ---------------- */
function RankingCard({ clip, index, active, onSelect, onDelete }) {
  const updateClipLocal = useStudioV2Store((s) => s.updateClipLocal)
  const patchClipRemote = useStudioV2Store((s) => s.patchClipRemote)
  const lockAndFetchHd = useStudioV2Store((s) => s.lockAndFetchHd)
  const downloadTasks = useStudioV2Store((s) => s.downloadTasks)
  const syncDownloadTask = useStudioV2Store((s) => s.syncDownloadTask)

  const [showType, setShowType] = useState(false)
  const timerRef = useRef(null)

  const dlTask = downloadTasks[clip.id]
  const activePoll = dlTask && (dlTask.status === 'PENDING' || dlTask.status === 'PROCESSING')
  useV2TaskPolling(
    activePoll ? dlTask.taskId : null,
    !!activePoll,
    (_id, payload) => syncDownloadTask(clip.id, payload),
    null
  )

  // debounced server sync (800ms)
  const queuePatch = (patch) => {
    updateClipLocal(clip.id, patch)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      patchClipRemote(clip.id, patch)
    }, 800)
  }
  // flush on unmount
  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    },
    []
  )

  const setStyle = (style) => queuePatch({ style })
  const duration = clip.duration > 0 ? clip.duration : Math.max(clip.end_time, 1)

  const hdPct = dlTask ? Math.round(dlTask.progress) : 0

  return (
    <Draggable draggableId={String(clip.id)} index={index}>
      {(provided, snapshot) => (
        <article
          ref={provided.innerRef}
          {...provided.draggableProps}
          className={`card rounded-xl border p-3 transition ${
            snapshot.isDragging ? 'ring-2 ring-accent-start shadow-lift' : ''
          } ${active ? 'border-accent-start/60' : 'border-edge'} ${active ? 'bg-[#232323]' : 'bg-[#2A2A2A]'} ${
            snapshot.isDragging ? 'opacity-90' : ''
          }`}
          onClick={() => onSelect(clip.id)}
          data-testid={`clip-card-${clip.rank}`}
        >
          {/* header row: drag handle + rank + thumb + meta */}
          <div className="flex items-start gap-3">
            <div
              {...provided.dragHandleProps}
              className="mt-1 cursor-grab select-none px-1 text-muted hover:text-ink active:cursor-grabbing"
              title="Drag to reorder"
              aria-label={`Drag handle for rank ${clip.rank}`}
              data-testid={`drag-handle-${clip.rank}`}
            >
              ⠿
            </div>
            <div
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg font-black text-white"
              style={{ background: 'linear-gradient(135deg,#FF4D4D,#FF8A4D)' }}
              title={`Rank ${clip.rank}`}
            >
              {clip.rank}
            </div>
            <div className="relative h-14 w-24 shrink-0 overflow-hidden rounded bg-base">
              {clip.thumbnail ? (
                <img
                  src={clip.thumbnail}
                  alt=""
                  className="h-full w-full object-cover"
                  onError={(e) => {
                    e.currentTarget.style.display = 'none'
                  }}
                />
              ) : (
                <div className="flex h-full w-full items-center justify-center text-white/30">
                  ▶
                </div>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <input
                className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-sm font-semibold text-ink hover:border-edge focus:border-accent-start/60 focus:bg-base focus:outline-none"
                value={clip.title || ''}
                aria-label="Clip title"
                placeholder="Title"
                onChange={(e) => queuePatch({ title: e.target.value })}
              />
              <input
                className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-xs text-muted hover:border-edge focus:border-accent-start/60 focus:bg-base focus:outline-none"
                value={clip.subtitle || ''}
                aria-label="Clip subtitle"
                placeholder="Subtitle (optional)"
                onChange={(e) => queuePatch({ subtitle: e.target.value })}
              />
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${HD_CLASS[clip.hd_status] || HD_CLASS.pending}`}
                  data-testid={`hd-status-${clip.rank}`}
                >
                  {HD_LABEL[clip.hd_status] || HD_LABEL.pending}
                  {clip.hd_status === 'downloading' && dlTask ? ` ${hdPct}%` : ''}
                </span>
                <span className="font-mono text-[10px] text-muted">{fmtTime(duration)}</span>
                <span className="font-mono text-[10px] text-muted">{clip.stream_type}</span>
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <button
                type="button"
                className="btn-ghost !px-1.5 !py-0.5 text-xs"
                title="Delete this clip"
                aria-label={`Delete clip ${clip.rank}`}
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(clip.id)
                }}
              >
                🗑
              </button>
            </div>
          </div>

          {/* trim */}
          <div className="mt-3">
            <TrimRange
              duration={duration}
              start={clip.start_time}
              end={clip.end_time}
              onChange={(lo, hi) => queuePatch({ start_time: lo, end_time: hi })}
            />
          </div>

          {/* volume + HD + typography toggle */}
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <div className="flex min-w-[140px] flex-1 items-center gap-2">
              <span className="text-[11px] text-muted" title="Clip volume">
                🔊
              </span>
              <input
                type="range"
                className="slider slider-fill"
                style={{ '--fill': `${(clip.volume ?? 1) * 100}%` }}
                min={0}
                max={1}
                step={0.05}
                value={clip.volume ?? 1}
                aria-label="Clip volume"
                data-testid={`volume-slider-${clip.rank}`}
                onChange={(e) => queuePatch({ volume: Number(e.target.value) })}
              />
              <span className="w-8 text-right font-mono text-[11px] text-ink">
                {Math.round((clip.volume ?? 1) * 100)}%
              </span>
            </div>
            <button
              type="button"
              className="btn-secondary !px-2.5 !py-1 text-xs"
              disabled={clip.hd_status === 'downloading'}
              onClick={(e) => {
                e.stopPropagation()
                lockAndFetchHd(clip.id)
              }}
              data-testid={`hd-button-${clip.rank}`}
            >
              {clip.hd_status === 'downloading'
                ? `Fetching ${hdPct}%…`
                : clip.hd_status === 'ready'
                  ? '✓ HD ready'
                  : clip.hd_status === 'failed'
                    ? 'Retry HD'
                    : 'Lock & Fetch HD'}
            </button>
            <button
              type="button"
              className="btn-ghost !px-2 !py-1 text-xs"
              onClick={(e) => {
                e.stopPropagation()
                setShowType((v) => !v)
              }}
              aria-expanded={showType}
              data-testid={`typography-toggle-${clip.rank}`}
            >
              {showType ? '▾' : '▸'} Typography
            </button>
          </div>

          {showType && <TypographyToolbar clip={clip} onStyle={setStyle} />}
        </article>
      )}
    </Draggable>
  )
}

/* ---------------- the list ---------------- */
export default function StudioRankingList() {
  const clips = useStudioV2Store((s) => s.clips)
  const clipsLoading = useStudioV2Store((s) => s.clipsLoading)
  const reorderClips = useStudioV2Store((s) => s.reorderClips)
  const selectClip = useStudioV2Store((s) => s.selectClip)
  const deleteClip = useStudioV2Store((s) => s.deleteClip)
  const activeClipId = useStudioV2Store((s) => s.activeClipId)

  const ordered = useMemo(() => [...clips].sort((a, b) => a.rank - b.rank), [clips])

  return (
    <section aria-label="Ranking" data-testid="ranking-list" className="flex flex-col gap-3">
      {clipsLoading && <p className="text-sm text-muted">Loading clips…</p>}
      {!clipsLoading && ordered.length === 0 && (
        <div className="panel flex flex-col items-center gap-2 p-8 text-center">
          <p className="text-sm font-semibold text-ink">No clips yet</p>
          <p className="text-xs text-muted">
            Paste a video URL above — the clip lands here as rank #1.
          </p>
        </div>
      )}
      <DragDropContext
        onDragEnd={(result) => {
          if (!result.destination) return
          reorderClips(result.source.index, result.destination.index)
        }}
      >
        <Droppable droppableId="ranking">
          {(provided) => (
            <div
              ref={provided.innerRef}
              {...provided.droppableProps}
              className="flex flex-col gap-3"
            >
              {ordered.map((clip, i) => (
                <RankingCard
                  key={clip.id}
                  clip={clip}
                  index={i}
                  active={clip.id === activeClipId}
                  onSelect={selectClip}
                  onDelete={deleteClip}
                />
              ))}
              {provided.placeholder}
            </div>
          )}
        </Droppable>
      </DragDropContext>
    </section>
  )
}
