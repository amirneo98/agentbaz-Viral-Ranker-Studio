import { useEffect, useState } from 'react'
import { useStudioStore } from './store/useStudioStore'
import FetchBar from './components/FetchBar'
import ClipLibrary from './components/ClipLibrary'
import Timeline from './components/Timeline'
import GlobalControls from './components/GlobalControls'
import RenderButton from './components/RenderButton'
import RenderModal from './components/RenderModal'
import Toasts from './components/Toasts'
import StudioPage from './components/StudioPage'
import StudioToasts from './components/StudioToasts'

/**
 * App shell: top nav switches between the v1.1 Dashboard and the v1.2
 * Studio. Both pages keep their own stores, so state survives switching.
 */
export default function App() {
  const [page, setPage] = useState(() =>
    typeof window !== 'undefined' && window.location.hash === '#studio' ? 'studio' : 'dashboard'
  )

  const loadVideos = useStudioStore((s) => s.loadVideos)
  const loadBgm = useStudioStore((s) => s.loadBgm)

  useEffect(() => {
    loadVideos()
    loadBgm()
  }, [loadVideos, loadBgm])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const onHash = () =>
      setPage(window.location.hash === '#studio' ? 'studio' : 'dashboard')
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const go = (p) => {
    setPage(p)
    if (typeof window !== 'undefined') {
      window.location.hash = p === 'studio' ? '#studio' : ''
      if (p === 'studio') window.scrollTo(0, 0)
    }
  }

  return (
    <div className="min-h-screen bg-base text-ink">
      {/* ---------- header ---------- */}
      <header className="sticky top-0 z-40 border-b border-edge bg-base/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-4 py-3 sm:px-6">
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
                Paste · Rank · Trim · Render compilations
              </p>
            </div>
          </div>

          {/* nav switch */}
          <nav
            className="flex overflow-hidden rounded-lg border border-edge"
            aria-label="Page switch"
          >
            <button
              type="button"
              className={`px-4 py-2 text-sm font-semibold transition ${
                page === 'dashboard'
                  ? 'bg-panel text-ink shadow-panel'
                  : 'text-muted hover:text-ink'
              }`}
              onClick={() => go('dashboard')}
              data-testid="nav-dashboard"
            >
              Dashboard
            </button>
            <button
              type="button"
              className={`px-4 py-2 text-sm font-semibold transition ${
                page === 'studio'
                  ? 'bg-gradient-to-r from-accent-start to-accent-end text-white shadow-panel'
                  : 'text-muted hover:text-ink'
              }`}
              onClick={() => go('studio')}
              data-testid="nav-studio"
            >
              ⚡ Studio
            </button>
          </nav>

          {page === 'dashboard' && <RenderButton />}
        </div>
      </header>

      {/* ---------- body ---------- */}
      {page === 'studio' ? (
        <StudioPage />
      ) : (
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
      )}

      {/* ---------- footer ---------- */}
      <footer className="mx-auto max-w-[1600px] px-4 pb-8 pt-2 sm:px-6">
        <p className="text-center text-xs text-muted/70">
          Viral Ranker Studio — countdown-style compilations rendered locally. Nothing leaves your
          machine.
        </p>
      </footer>

      <RenderModal />
      <Toasts />
      <StudioToasts />
    </div>
  )
}
