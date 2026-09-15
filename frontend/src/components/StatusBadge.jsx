const STYLES = {
  PENDING: 'bg-white/10 text-muted border-white/10',
  PROCESSING: 'bg-secondary/15 text-blue-400 border-secondary/30',
  SUCCESS: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  FAILED: 'bg-red-500/15 text-red-400 border-red-500/30'
}

const LABELS = {
  PENDING: 'Queued',
  PROCESSING: 'Working',
  SUCCESS: 'Done',
  FAILED: 'Failed'
}

export default function StatusBadge({ status }) {
  const cls = STYLES[status] || STYLES.PENDING
  const label = LABELS[status] || status
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${cls}`}
    >
      {(status === 'PROCESSING' || status === 'PENDING') && (
        <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" aria-hidden="true" />
      )}
      {label}
    </span>
  )
}
