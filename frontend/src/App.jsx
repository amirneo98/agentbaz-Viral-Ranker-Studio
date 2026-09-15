import { useEffect } from 'react'
import { useStudioStore } from './store/useStudioStore'
import FetchBar from './components/FetchBar'
import ClipLibrary from './components/ClipLibrary'
import Timeline from './components/Timeline'
import GlobalControls from './components/GlobalControls'
import RenderButton from './components/RenderButton'
import RenderModal from './components/RenderModal'
import Toasts from './components/Toasts'

export default function App() {
  const loadVideos = useStudioStore((s) => s.loadVideos)
  const loadBgm = useStudioStore((s) => s.loadBgm)

  useEffect(() => {
    loadVideos()
    loadBgm()
  }, [loadVideos, loadBgm])

  return (
    <div className="min-h-screen bg-base text-ink">
      {/* ---------- header ---------- */}
      <header className="sticky top-0 z-40 border-b border-edge bg-base/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <div
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-accent-start to-accent-end text-sm font-extrabold text-white shadow-panel"
              aria-hidden="true"
            >
              VR
            </div>
            <div>
              <h1 className="text-base font-bold leading-tight text-ink">
                Viral Ranker Studio
              </h1>
              <p className="hidden text-[11px] text-muted sm:block">
                Paste · Rank · Trim · Render 9:16 compilations
              </p>
            </div>
          </div>
          <RenderButton />
        </div>
      </header>

      {/* ---------- body ---------- */}
      <main className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5 sm:px-6">
        <FetchBar />

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[3fr_2fr]">
          <ClipLibrary />
          <div className="flex flex-col gap-4">
            <Timeline />
            <GlobalControls />
          </div>
        </div>
      </main>

      {/* ---------- footer ---------- */}
      <footer className="mx-auto max-w-7xl px-4 pb-8 pt-2 sm:px-6">
        <p className="text-center text-xs text-muted/70">
          Viral Ranker Studio — countdown-style compilations rendered locally. Nothing leaves your
          machine.
        </p>
      </footer>

      <RenderModal />
      <Toasts />
    </div>
  )
}
