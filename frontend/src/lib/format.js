// Format seconds (int or float) as M:SS
export function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

export function clamp(value, lo, hi) {
  return Math.min(hi, Math.max(lo, value))
}

// "1920×1080" style label, tolerant of missing dims
export function fmtDims(width, height) {
  if (!width || !height) return 'unknown size'
  return `${width}×${height}`
}
