import { useState } from 'react'
import { useStudioV2Store } from '../store/useStudioV2Store'

function Slider({ value, min, max, step, onChange, label, format, disabled }) {
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0
  return (
    <div className={disabled ? 'opacity-50' : ''}>
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</span>
        <span className="font-mono text-[11px] text-ink">{format(value)}</span>
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
        aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  )
}

function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition ${
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

/**
 * Studio top bar: master title, aspect switcher, global settings (BGM,
 * scale, blur, ducking), preset manager and the render button.
 */
export default function StudioTopBar({ onRender }) {
  const settings = useStudioV2Store((s) => s.settings)
  const bgm = useStudioV2Store((s) => s.bgm)
  const aspect = useStudioV2Store((s) => s.aspect)
  const clips = useStudioV2Store((s) => s.clips)
  const updateSettings = useStudioV2Store((s) => s.updateSettings)
  const setAspect = useStudioV2Store((s) => s.setAspect)
  const presets = useStudioV2Store((s) => s.presets)
  const savePreset = useStudioV2Store((s) => s.savePreset)
  const applyPreset = useStudioV2Store((s) => s.applyPreset)
  const deletePreset = useStudioV2Store((s) => s.deletePreset)

  const [showSettings, setShowSettings] = useState(false)
  const [showPresets, setShowPresets] = useState(false)
  const [presetName, setPresetName] = useState('')
  const [presetBusy, setPresetBusy] = useState(false)

  const activeClip = clips.find((c) => c.id === useStudioV2Store.getState().activeClipId)

  const onSavePreset = async () => {
    const name = presetName.trim()
    if (!name || !activeClip) return
    setPresetBusy(true)
    try {
      const st = activeClip.style || {}
      await savePreset(name, {
        font: st.font,
        fontSize: st.fontSize,
        textColor: st.textColor,
        strokeColor: st.strokeColor,
        strokeWidth: st.strokeWidth,
        shadow: st.shadow,
        badgeBg: st.badgeBg,
        badgeOpacity: st.badgeOpacity,
        textPosition: st.textPosition,
        videoScalePct: settings.videoScalePct,
        blurBg: settings.backgroundBlur,
        blurSigma: settings.blurSigma,
        bgmVolume: settings.bgmVolume,
        duckingThreshold: settings.duckingThreshold,
        duckingRatio: settings.duckingRatio
      })
      setPresetName('')
    } finally {
      setPresetBusy(false)
    }
  }

  return (
    <section className="panel px-4 py-3" aria-label="Studio controls">
      {/* row 1: master title + aspect + render */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="min-w-0 flex-1">
          <label className="label !mb-1" htmlFor="studio-master-title">
            Master title
          </label>
          <input
            id="studio-master-title"
            type="text"
            className="field"
            placeholder="Top 5 Viral Clips of the Week"
            value={settings.masterTitle}
            data-testid="master-title"
            onChange={(e) => updateSettings({ masterTitle: e.target.value })}
          />
        </div>

        <div>
          <span className="label !mb-1 block">Aspect</span>
          <div
            className="flex overflow-hidden rounded-lg border border-edge"
            role="group"
            aria-label="Aspect ratio"
            data-testid="aspect-switcher"
          >
            {['9:16', '16:9'].map((a) => (
              <button
                key={a}
                type="button"
                className={`px-3 py-1.5 text-xs font-bold transition ${
                  aspect === a ? 'bg-accent-start text-white' : 'text-muted hover:bg-white/5'
                }`}
                onClick={() => setAspect(a)}
                data-testid={`aspect-${a.replace(':', '-')}`}
              >
                {a}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-end gap-2">
          <button
            type="button"
            className="btn-ghost !py-2"
            onClick={() => setShowSettings((v) => !v)}
            aria-expanded={showSettings}
            data-testid="settings-toggle"
          >
            ⚙ Settings
          </button>
          <button
            type="button"
            className="btn-ghost !py-2"
            onClick={() => setShowPresets((v) => !v)}
            aria-expanded={showPresets}
            data-testid="presets-toggle"
          >
            ▤ Presets
          </button>
          <button
            type="button"
            className="btn-primary !py-2"
            onClick={onRender}
            disabled={clips.length === 0}
            data-testid="render-button"
          >
            ▶ Render
          </button>
        </div>
      </div>

      {/* row 2: global settings */}
      {showSettings && (
        <div className="mt-3 grid grid-cols-1 gap-x-6 gap-y-3 rounded-lg border border-edge bg-base/60 p-3 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <label className="label !mb-1" htmlFor="studio-bgm">
              Background music
            </label>
            <select
              id="studio-bgm"
              className="field !py-1.5 text-xs"
              value={settings.bgmId == null ? '' : String(settings.bgmId)}
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
            label="BGM volume"
            value={Math.round(settings.bgmVolume * 100)}
            min={0}
            max={100}
            step={1}
            disabled={settings.bgmId == null}
            onChange={(v) => updateSettings({ bgmVolume: v / 100 })}
            format={(v) => `${v}%`}
          />
          <Slider
            label="Ducking threshold"
            value={settings.duckingThreshold}
            min={0}
            max={0.5}
            step={0.01}
            onChange={(v) => updateSettings({ duckingThreshold: v })}
            format={(v) => v.toFixed(2)}
          />
          <Slider
            label="Ducking ratio"
            value={settings.duckingRatio}
            min={0}
            max={1}
            step={0.05}
            onChange={(v) => updateSettings({ duckingRatio: v })}
            format={(v) => `${Math.round(v * 100)}%`}
          />
          <Slider
            label="Video scale"
            value={settings.videoScalePct}
            min={30}
            max={100}
            step={1}
            onChange={(v) => updateSettings({ videoScalePct: v })}
            format={(v) => `${v}%`}
          />
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <span className="label !mb-1">Background blur</span>
              <Slider
                label="Blur sigma"
                value={settings.blurSigma}
                min={0}
                max={40}
                step={1}
                disabled={!settings.backgroundBlur}
                onChange={(v) => updateSettings({ blurSigma: v })}
                format={(v) => `${v}px`}
              />
            </div>
            <div className="pt-4">
              <Toggle
                checked={settings.backgroundBlur}
                onChange={(v) => updateSettings({ backgroundBlur: v })}
                label="Background blur"
              />
            </div>
          </div>
        </div>
      )}

      {/* row 3: presets */}
      {showPresets && (
        <div
          className="mt-3 rounded-lg border border-edge bg-base/60 p-3"
          data-testid="preset-panel"
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="flex-1">
              <label className="label !mb-1" htmlFor="preset-name">
                Save current style as preset
              </label>
              <input
                id="preset-name"
                type="text"
                className="field !py-1.5 text-xs"
                placeholder="e.g. Bold Phonk"
                value={presetName}
                data-testid="preset-name-input"
                onChange={(e) => setPresetName(e.target.value)}
              />
            </div>
            <button
              type="button"
              className="btn-secondary !py-1.5 text-xs"
              disabled={presetBusy || !presetName.trim() || !activeClip}
              onClick={onSavePreset}
              data-testid="preset-save"
            >
              {presetBusy ? 'Saving…' : 'Save preset'}
            </button>
          </div>
          <div className="mt-3 flex flex-col gap-2">
            {presets.length === 0 && (
              <p className="text-xs text-muted">No presets saved yet.</p>
            )}
            {presets.map((p) => (
              <div
                key={p.id}
                className="flex items-center justify-between gap-2 rounded-lg border border-edge bg-panel px-3 py-1.5"
                data-testid={`preset-row-${p.id}`}
              >
                <span className="truncate text-xs font-semibold text-ink">{p.name}</span>
                <span className="flex gap-1">
                  <button
                    type="button"
                    className="btn-ghost !px-2 !py-0.5 text-xs"
                    onClick={() => applyPreset(p.id)}
                    data-testid={`preset-apply-${p.id}`}
                  >
                    Load
                  </button>
                  <button
                    type="button"
                    className="btn-ghost !px-2 !py-0.5 text-xs text-red-400 hover:text-red-300"
                    onClick={() => deletePreset(p.id)}
                    aria-label={`Delete preset ${p.name}`}
                  >
                    🗑
                  </button>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
