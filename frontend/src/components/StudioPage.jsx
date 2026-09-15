import { useEffect } from 'react'
import { useStudioV2Store } from '../store/useStudioV2Store'
import StudioTopBar from './StudioTopBar'
import { StudioUrlBar } from './StudioToasts'
import StudioRankingList from './StudioRankingList'
import LiveCanvasPreview from './LiveCanvasPreview'
import StudioRenderModal from './StudioRenderModal'

/**
 * v1.2 Studio page: two-column Viblo-style layout.
 * LEFT (~55%): URL ingestion + draggable ranking cards with trim,
 * typography and HD controls.
 * RIGHT (~45%, sticky): live WYSIWYG preview of the active clip.
 */
export default function StudioPage() {
  const loadClips = useStudioV2Store((s) => s.loadClips)
  const loadPresets = useStudioV2Store((s) => s.loadPresets)
  const loadBgm = useStudioV2Store((s) => s.loadBgm)
  const render = useStudioV2Store((s) => s.render)
  const clips = useStudioV2Store((s) => s.clips)
  const totalDuration = clips.reduce(
    (sum, c) => sum + Math.max(0, c.end_time - c.start_time),
    0
  )

  useEffect(() => {
    loadClips()
    loadPresets()
    loadBgm()
  }, [loadClips, loadPresets, loadBgm])

  return (
    <main className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-5 sm:px-6">
      <StudioTopBar onRender={render} />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[55fr_45fr] xl:grid-cols-[11fr_9fr]">
        {/* LEFT column */}
        <div className="flex min-w-0 flex-col gap-4">
          <StudioUrlBar />
          <div className="flex items-center justify-between px-1">
            <h2 className="text-sm font-bold uppercase tracking-wider text-ink">
              Ranking <span className="text-muted">({clips.length})</span>
            </h2>
            {clips.length > 0 && (
              <span className="font-mono text-[11px] text-muted">
                total {Math.floor(totalDuration / 60)}:
                {String(Math.round(totalDuration % 60)).padStart(2, '0')}
              </span>
            )}
          </div>
          <StudioRankingList />
        </div>

        {/* RIGHT column (sticky preview) */}
        <div className="relative">
          <LiveCanvasPreview />
        </div>
      </div>

      <StudioRenderModal />
    </main>
  )
}
