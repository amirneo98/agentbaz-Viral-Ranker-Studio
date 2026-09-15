import { fmtTime } from '../lib/format'

/**
 * Dual-thumb trim control built from two overlaid native <input type="range">
 * elements on a custom-painted track. Enforces a minimum 1s gap and stays
 * bounded by the clip duration. Updates the store live.
 */
export default function TrimSlider({ duration, start, end, onChange }) {
  const max = duration > 0 ? duration : 1
  const lo = Math.min(Math.max(start, 0), max)
  const hi = Math.min(Math.max(end, lo), max)
  const loPct = max > 0 ? (lo / max) * 100 : 0
  const hiPct = max > 0 ? (hi / max) * 100 : 0

  const clampStart = (v) => Math.min(v, hi - 1)
  const clampEnd = (v) => Math.max(v, lo + 1)

  return (
    <div className="w-full" aria-label="Trim clip">
      <div className="relative h-7 select-none">
        {/* base track */}
        <div className="absolute left-0 right-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-edge" />
        {/* selected region */}
        <div
          className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-gradient-to-r from-accent-start to-accent-end"
          style={{ left: `${loPct}%`, width: `${Math.max(0, hiPct - loPct)}%` }}
        />
        {/* the two native inputs (thumbs only are clickable) */}
        <input
          type="range"
          className="range-dual"
          min={0}
          max={max}
          step={0.1}
          value={lo}
          aria-label="Trim start"
          title={`Start: ${fmtTime(lo)}`}
          onChange={(e) => onChange(clampStart(Number(e.target.value)), hi)}
        />
        <input
          type="range"
          className="range-dual"
          min={0}
          max={max}
          step={0.1}
          value={hi}
          aria-label="Trim end"
          title={`End: ${fmtTime(hi)}`}
          onChange={(e) => onChange(lo, clampEnd(Number(e.target.value)))}
        />
      </div>
      <div className="flex items-center justify-between text-[11px] font-mono text-muted">
        <span>
          in <span className="text-ink">{fmtTime(lo)}</span>
        </span>
        <span className="text-accent-end">
          {fmtTime(Math.max(0, hi - lo))} selected
        </span>
        <span>
          out <span className="text-ink">{fmtTime(hi)}</span>
        </span>
      </div>
    </div>
  )
}
