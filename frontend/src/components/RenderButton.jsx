import { useStudioStore, selectTotalDuration } from '../store/useStudioStore'
import { fmtTime } from '../lib/format'

export default function RenderButton() {
  const timeline = useStudioStore((s) => s.timeline)
  const total = useStudioStore(selectTotalDuration)
  const render = useStudioStore((s) => s.render)

  const disabled = timeline.length === 0

  return (
    <button
      type="button"
      className="btn-primary !px-5"
      disabled={disabled}
      onClick={render}
      title={
        disabled
          ? 'Add at least one clip to the timeline to render'
          : `Render a 9:16 compilation (${timeline.length} clips, ${fmtTime(total)})`
      }
    >
      <svg viewBox="0 0 24 24" className="h-4 w-4 fill-current" aria-hidden="true">
        <path d="M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm6 4v10l8-5-8-5z" />
      </svg>
      Render {disabled ? '' : `· ${fmtTime(total)}`}
    </button>
  )
}
