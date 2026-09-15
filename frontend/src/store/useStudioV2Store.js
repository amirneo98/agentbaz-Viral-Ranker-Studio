// Zustand store for the v1.2 Studio page: ranking clips, per-clip style,
// global render settings, presets, and async tasks (stream-info, HD download,
// render). v1.1 useStudioStore is untouched and still drives the dashboard.
import { create } from 'zustand'
import api from '../services/api'

export const DEFAULT_STYLE = {
  font: 'Archivo Black',
  fontSize: 56,
  textColor: '#FFFFFF',
  strokeColor: '#000000',
  strokeWidth: 4,
  shadow: true,
  badgeBg: '#FF4B4B',
  badgeOpacity: 0.9,
  textPosition: 'top',
  title: '',
  subtitle: ''
}

export const DEFAULT_SETTINGS = {
  masterTitle: '',
  masterTitleStyle: { ...DEFAULT_STYLE, fontSize: 72 },
  aspect: '9:16',
  videoScalePct: 80,
  backgroundBlur: true,
  blurSigma: 12,
  bgmId: null,
  bgmVolume: 0.4,
  duckingThreshold: 0.06,
  duckingRatio: 0.25
}

let toastSeq = 0

const sortClips = (clips) => [...clips].sort((a, b) => a.rank - b.rank)

export const useStudioV2Store = create((set, get) => ({
  // ---------- data ----------
  clips: [],
  clipsLoading: false,
  presets: [],
  presetsLoading: false,
  bgm: [],
  settings: { ...DEFAULT_SETTINGS },
  activeClipId: null,
  aspect: '9:16',

  // async task bookkeeping
  streamInfoBusy: false,
  downloadTasks: {}, // clipId -> {taskId, status, progress, error}
  renderTask: null,

  toasts: [],

  // ---------- boot ----------
  loadClips: async () => {
    set({ clipsLoading: true })
    try {
      const data = await api.getClips()
      const clips = sortClips(Array.isArray(data.clips) ? data.clips : [])
      set((s) => ({
        clips,
        activeClipId:
          s.activeClipId && clips.some((c) => c.id === s.activeClipId)
            ? s.activeClipId
            : clips.length
              ? clips[0].id
              : null
      }))
    } catch (err) {
      get().pushToast('error', `Couldn't load clips: ${err.message}`)
    } finally {
      set({ clipsLoading: false })
    }
  },

  loadPresets: async () => {
    set({ presetsLoading: true })
    try {
      const data = await api.getPresets()
      set({ presets: Array.isArray(data.presets) ? data.presets : [] })
    } catch (err) {
      get().pushToast('error', `Couldn't load presets: ${err.message}`)
    } finally {
      set({ presetsLoading: false })
    }
  },

  loadBgm: async () => {
    try {
      const data = await api.getBgm()
      set({ bgm: Array.isArray(data.bgm) ? data.bgm : [] })
    } catch (err) {
      get().pushToast('error', `Couldn't load BGM library: ${err.message}`)
    }
  },

  // ---------- URL ingestion ----------
  ingestUrl: async (url) => {
    set({ streamInfoBusy: true })
    try {
      const info = await api.postStreamInfo(url)
      const nextRank = get().clips.reduce((m, c) => Math.max(m, c.rank), 0) + 1
      const clip = {
        source_url: url,
        title: info.title || 'Untitled clip',
        subtitle: '',
        rank: nextRank,
        start_time: 0,
        end_time: info.duration > 0 ? info.duration : 10,
        stream_url: info.stream_url || '',
        stream_type: info.stream_type || 'none',
        thumbnail: info.thumbnail || '',
        duration: info.duration || 0,
        style: { ...DEFAULT_STYLE, title: info.title || 'Untitled clip' },
        volume: 1.0,
        hd_status: 'pending'
      }
      const created = await api.postClip(clip)
      const full = { ...clip, ...created }
      set((s) => ({
        clips: sortClips([...s.clips.filter((c) => c.id !== full.id), full]),
        activeClipId: full.id
      }))
      get().pushToast('success', `Added “${full.title}” as rank #${full.rank}`)
      return full
    } finally {
      set({ streamInfoBusy: false })
    }
  },

  // ---------- clip edits (server writes debounced in ClipCard) ----------
  updateClipLocal: (id, patch) =>
    set((s) => ({
      clips: s.clips.map((c) => (c.id === id ? { ...c, ...patch } : c))
    })),

  patchClipRemote: async (id, patch) => {
    try {
      const updated = await api.patchClip(id, patch)
      if (updated && typeof updated === 'object' && !updated.detail) {
        set((s) => ({
          clips: s.clips.map((c) => (c.id === id ? { ...c, ...updated } : c))
        }))
      }
    } catch (err) {
      get().pushToast('error', `Couldn't save clip: ${err.message}`)
    }
  },

  deleteClip: async (id) => {
    try {
      await api.deleteClip(id)
    } catch (err) {
      get().pushToast('error', `Couldn't delete clip: ${err.message}`)
      return
    }
    set((s) => {
      const clips = s.clips.filter((c) => c.id !== id)
      return {
        clips,
        activeClipId:
          s.activeClipId === id ? (clips.length ? clips[0].id : null) : s.activeClipId
      }
    })
    get().pushToast('info', 'Clip deleted')
  },

  selectClip: (id) => set({ activeClipId: id }),

  // ---------- drag reorder ----------
  reorderClips: async (fromIndex, toIndex) => {
    const { clips } = get()
    if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) return
    if (fromIndex >= clips.length || toIndex >= clips.length) return
    const next = clips.slice()
    const [moved] = next.splice(fromIndex, 1)
    next.splice(toIndex, 0, moved)
    const ranked = next.map((c, i) => ({ ...c, rank: i + 1 }))
    set({ clips: ranked })
    try {
      await api.postReorderClips(ranked.map((c) => c.id))
    } catch (err) {
      get().pushToast('error', `Couldn't save order: ${err.message}`)
      get().loadClips()
    }
  },

  // ---------- HD download ----------
  lockAndFetchHd: async (clipId) => {
    const existing = get().downloadTasks[clipId]
    if (existing && (existing.status === 'PENDING' || existing.status === 'downloading')) return
    get().updateClipLocal(clipId, { hd_status: 'downloading' })
    try {
      const data = await api.postDownloadSection(clipId)
      set((s) => ({
        downloadTasks: {
          ...s.downloadTasks,
          [clipId]: { taskId: data.task_id, status: 'PENDING', progress: 0, error: '' }
        }
      }))
    } catch (err) {
      get().updateClipLocal(clipId, { hd_status: 'failed' })
      get().pushToast('error', `Couldn't start HD download: ${err.message}`)
    }
  },

  syncDownloadTask: (clipId, payload) => {
    const status = payload.status || 'PROCESSING'
    const progress = typeof payload.progress === 'number' ? payload.progress : 0
    set((s) => ({
      downloadTasks: {
        ...s.downloadTasks,
        [clipId]: {
          ...((s.downloadTasks[clipId] || {})),
          status,
          progress,
          error: payload.error || ''
        }
      }
    }))
    // map generic task status onto the clip's hd_status
    if (status === 'SUCCESS') {
      const hd = payload.result && payload.result.hd_status ? payload.result.hd_status : 'ready'
      get().updateClipLocal(clipId, { hd_status: hd })
      get().pushToast('success', 'HD section ready')
    } else if (status === 'FAILED') {
      get().updateClipLocal(clipId, { hd_status: 'failed' })
      get().pushToast('error', 'HD download failed')
    } else {
      get().updateClipLocal(clipId, { hd_status: 'downloading' })
    }
  },

  // ---------- settings ----------
  updateSettings: (patch) => set((s) => ({ settings: { ...s.settings, ...patch } })),
  setAspect: (aspect) => set({ aspect }),

  // ---------- presets ----------
  savePreset: async (name, data) => {
    try {
      const created = await api.postPreset({ name, data })
      set((s) => ({ presets: [...s.presets, created] }))
      get().pushToast('success', `Preset “${name}” saved`)
      return created
    } catch (err) {
      get().pushToast('error', `Couldn't save preset: ${err.message}`)
      return null
    }
  },

  applyPreset: (presetId) => {
    const preset = get().presets.find((p) => p.id === presetId)
    if (!preset || !preset.data) return
    const active = get().clips.find((c) => c.id === get().activeClipId)
    const style = { ...(active ? active.style : DEFAULT_STYLE), ...preset.data }
    if (active) get().updateClipLocal(active.id, { style })
    // global settings that presets carry
    get().updateSettings({
      videoScalePct: preset.data.videoScalePct ?? get().settings.videoScalePct,
      backgroundBlur: preset.data.blurBg ?? get().settings.backgroundBlur,
      blurSigma: preset.data.blurSigma ?? get().settings.blurSigma,
      bgmVolume: preset.data.bgmVolume ?? get().settings.bgmVolume,
      duckingThreshold: preset.data.duckingThreshold ?? get().settings.duckingThreshold,
      duckingRatio: preset.data.duckingRatio ?? get().settings.duckingRatio
    })
    if (active) get().patchClipRemote(active.id, { style })
    get().pushToast('info', `Preset “${preset.name}” applied`)
  },

  deletePreset: async (presetId) => {
    try {
      await api.deletePreset(presetId)
      set((s) => ({ presets: s.presets.filter((p) => p.id !== presetId) }))
    } catch (err) {
      get().pushToast('error', `Couldn't delete preset: ${err.message}`)
    }
  },

  // ---------- render ----------
  render: async () => {
    const { clips, settings, aspect } = get()
    if (clips.length === 0) {
      get().pushToast('error', 'Add at least one clip before rendering')
      return
    }
    const payload = {
      master_title: settings.masterTitle,
      master_title_style: settings.masterTitleStyle,
      aspect,
      clips: clips.map((c) => ({
        clip_id: c.id,
        rank: c.rank,
        start: c.start_time,
        end: c.end_time,
        title: c.title,
        subtitle: c.subtitle,
        style: c.style,
        volume: c.volume
      })),
      settings: {
        video_scale_pct: settings.videoScalePct,
        background_blur: settings.backgroundBlur,
        blur_sigma: settings.blurSigma,
        bgm_id: settings.bgmId,
        bgm_volume: settings.bgmVolume,
        ducking_threshold: settings.duckingThreshold,
        ducking_ratio: settings.duckingRatio
      }
    }
    try {
      const data = await api.postRender(payload)
      set({
        renderTask: { id: data.task_id, status: 'PENDING', progress: 0, error: '', result: null }
      })
    } catch (err) {
      get().pushToast('error', `Couldn't start render: ${err.message}`)
    }
  },

  syncRenderTask: (payload) => {
    const t = get().renderTask
    if (!t) return
    set({
      renderTask: {
        ...t,
        status: payload.status || 'PROCESSING',
        progress: typeof payload.progress === 'number' ? payload.progress : t.progress,
        error: payload.error || '',
        result: payload.result || null
      }
    })
  },

  closeRenderModal: () => set({ renderTask: null }),

  // ---------- toasts ----------
  pushToast: (kind, message) => {
    const id = ++toastSeq
    set((s) => ({ toasts: [...s.toasts, { id, kind, message }] }))
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
      }, 4000)
    }
  },

  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
}))

export const selectTotalDuration = (state) =>
  state.clips.reduce((sum, c) => sum + Math.max(0, c.end_time - c.start_time), 0)
