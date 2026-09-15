import { useStudioStore } from '../store/useStudioStore'

function Toggle({ checked, onChange, label, title }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      title={title}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-end focus-visible:ring-offset-2 focus-visible:ring-offset-panel ${
        checked ? 'bg-gradient-to-r from-accent-start to-accent-end' : 'bg-edge'
      }`}
    >
      <span
        className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
          checked ? 'left-[22px]' : 'left-0.5'
        }`}
      />
    </button>
  )
}

function Slider({ value, min, max, step, onChange, label, format, disabled, title }) {
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0
  return (
    <div className={disabled ? 'opacity-50' : ''}>
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</span>
        <span className="font-mono text-xs text-ink">{format(value)}</span>
      </div>
      <input
        type="range"
        className="slider slider-fill"
        style={{ '--fill': `${pct}%` }}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        title={title}
        aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  )
}

export default function GlobalControls() {
  const settings = useStudioStore((s) => s.settings)
  const bgm = useStudioStore((s) => s.bgm)
  const updateSettings = useStudioStore((s) => s.updateSettings)

  const hasBgm = settings.bgmId != null

  return (
    <section className="panel p-4" aria-label="Global settings">
      <h2 className="mb-3 text-sm font-bold uppercase tracking-wider text-ink">
        Global Settings
      </h2>

      <div className="flex flex-col gap-4">
        <div>
          <label className="label" htmlFor="master-title">
            Master Title
          </label>
          <input
            id="master-title"
            type="text"
            className="field"
            placeholder="Top 5 Viral Clips of the Week"
            value={settings.masterTitle}
            title="Title card shown on the rendered compilation"
            onChange={(e) => updateSettings({ masterTitle: e.target.value })}
          />
        </div>

        <Slider
          label="Video Height"
          value={settings.videoHeightPct}
          min={30}
          max={100}
          step={1}
          onChange={(v) => updateSettings({ videoHeightPct: v })}
          format={(v) => `${v}%`}
          title="How much of the 9:16 frame the clip fills"
        />

        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-ink">Background Blur</p>
            <p className="text-xs text-muted">Blurry fill behind the video frame</p>
          </div>
          <Toggle
            checked={settings.backgroundBlur}
            onChange={(v) => updateSettings({ backgroundBlur: v })}
            label="Background blur"
            title="Toggle blurred background behind the video"
          />
        </div>

        <div>
          <label className="label" htmlFor="bgm-select">
            Background Music
          </label>
          <select
            id="bgm-select"
            className="field"
            value={settings.bgmId == null ? '' : String(settings.bgmId)}
            title="Choose a background music track"
            onChange={(e) =>
              updateSettings({ bgmId: e.target.value === '' ? null : Number(e.target.value) })
            }
          >
            <option value="">No BGM</option>
            {bgm.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </div>

        <Slider
          label="BGM Volume"
          value={Math.round(settings.bgmVolume * 100)}
          min={0}
          max={100}
          step={1}
          disabled={!hasBgm}
          onChange={(v) => updateSettings({ bgmVolume: v / 100 })}
          format={(v) => `${v}%`}
          title="Background music volume"
        />
      </div>
    </section>
  )
}
