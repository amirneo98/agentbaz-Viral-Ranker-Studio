import { create } from 'zustand'
import request from '../api/client'

export const DEFAULT_SETTINGS = {
  masterTitle: '',
  videoHeightPct: 80,
  backgroundBlur: true,
  bgmId: null,
  bgmVolume: 0.4
}

let toastSeq = 0

function moveItem(list, from, to) {
  const next = list.slice()
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

const isActive = (t) => t.status === 'PENDING' || t.status === 'PROCESSING'

export const useStudioStore = create((set, get) => ({
  videos: [],
  videosLoading: false,
  bgm: [],
  bgmLoading: false,

  timeline: [],
  settings: { ...DEFAULT_SETTINGS },

  fetchTasks: {},
  lastFetchId: null,
  renderTask: null,

  toasts: [],

  // ---------- library data ----------
  loadVideos: async () => {
    set({ videosLoading: true })
    try {
      const data = await request('/api/videos/')
      set({ videos: Array.isArray(data.videos) ? data.videos : [] })
    } catch (err) {
      get().pushToast('error', `Couldn't load clips: ${err.message}`)
    } finally {
      set({ videosLoading: false })
    }
  },

  loadBgm: async () => {
    set({ bgmLoading: true })
    try {
      const data = await request('/api/bgm/')
      set({ bgm: Array.isArray(data.bgm) ? data.bgm : [] })
    } catch (err) {
      get().pushToast('error', `Couldn't load BGM library: ${err.message}`)
    } finally {
      set({ bgmLoading: false })
    }
  },

  deleteVideo: async (id) => {
    try {
      await request(`/api/videos/${id}/`, { method: 'DELETE' })
    } catch (err) {
      get().pushToast('error', `Couldn't delete clip: ${err.message}`)
      return
    }
    set((state) => ({
      videos: state.videos.filter((v) => v.id !== id),
      timeline: state.timeline.filter((t) => t.videoId !== id),
      // drop finished fetch tasks; keep in-flight ones
      fetchTasks: Object.fromEntries(
        Object.entries(state.fetchTasks).filter(([, t]) => isActive(t))
      )
    }))
    get().pushToast('success', 'Clip deleted')
  },

  // ---------- fetch flow ----------
  fetchUrl: async (url) => {
    const data = await request('/api/fetch/', { method: 'POST', body: { url } })
    const id = data.task_id
    set((state) => ({
      fetchTasks: {
        ...state.fetchTasks,
        [id]: { id, status: 'PENDING', progress: 0, error: '', url }
      },
      lastFetchId: id
    }))
    return id
  },

  // Called by useTaskPolling with each GET /api/tasks/{id}/ response.
  syncTask: (id, payload) => {
    const status = payload.status || 'PROCESSING'
    const progress = typeof payload.progress === 'number' ? payload.progress : 0
    const error = payload.error || ''
    const result = payload.result || null

    const fetchTask = get().fetchTasks[id]
    if (fetchTask) {
      set((state) => ({
        fetchTasks: {
          ...state.fetchTasks,
          [id]: { ...state.fetchTasks[id], status, progress, error, result }
        }
      }))
      if (status === 'SUCCESS') {
        const title = result && result.title ? result.title : 'New clip'
        get().pushToast('success', `Fetched “${title}” — added to library`)
        get().loadVideos()
      }
      return
    }

    const renderTask = get().renderTask
    if (renderTask && renderTask.id === id) {
      set({ renderTask: { ...renderTask, status, progress, error, result } })
    }
  },

  clearFetchTask: (id) =>
    set((state) => {
      const fetchTasks = { ...state.fetchTasks }
      delete fetchTasks[id]
      return {
        fetchTasks,
        lastFetchId: state.lastFetchId === id ? null : state.lastFetchId
      }
    }),

  // ---------- timeline ----------
  addToTimeline: (videoId) => {
    const { videos, timeline } = get()
    if (timeline.some((t) => t.videoId === videoId)) return
    const video = videos.find((v) => v.id === videoId)
    if (!video) return
    const end = video.duration > 0 ? video.duration : 1
    set((state) => ({
      timeline: [...state.timeline, { videoId, start: 0, end, title: video.title }]
    }))
  },

  removeFromTimeline: (videoId) =>
    set((state) => ({ timeline: state.timeline.filter((t) => t.videoId !== videoId) })),

  reorderTimeline: (from, to) =>
    set((state) => {
      if (
        from === to ||
        from < 0 || to < 0 ||
        from >= state.timeline.length || to >= state.timeline.length
      ) {
        return state
      }
      return { timeline: moveItem(state.timeline, from, to) }
    }),

  setTrim: (videoId, start, end) =>
    set((state) => ({
      timeline: state.timeline.map((t) => {
        if (t.videoId !== videoId) return t
        const video = state.videos.find((v) => v.id === videoId)
        const duration = video && video.duration > 0 ? video.duration : Math.max(end, 1)
        const lo = duration > 1 ? Math.min(start, end - 1) : Math.min(start, end)
        const hi = duration > 1 ? Math.max(end, lo + 1) : Math.max(end, lo)
        return {
          ...t,
          start: Math.min(Math.max(lo, 0), duration),
          end: Math.min(Math.max(hi, 0), duration)
        }
      })
    })),

  // ---------- settings ----------
  updateSettings: (patch) =>
    set((state) => ({ settings: { ...state.settings, ...patch } })),

  // ---------- render ----------
  render: async () => {
    const { timeline, settings } = get()
    if (timeline.length === 0) return
    const n = timeline.length
    const payload = {
      master_title: settings.masterTitle,
      clips: timeline.map((t, i) => ({
        video_id: t.videoId,
        rank: n - i, // top item = N (plays first), bottom = 1 (finale)
        start: t.start,
        end: t.end,
        title: t.title
      })),
      settings: {
        video_height_pct: settings.videoHeightPct,
        background_blur: settings.backgroundBlur,
        bgm_id: settings.bgmId,
        bgm_volume: settings.bgmVolume
      }
    }
    try {
      const data = await request('/api/render/', { method: 'POST', body: payload })
      set({
        renderTask: { id: data.task_id, status: 'PENDING', progress: 0, error: '', result: null }
      })
    } catch (err) {
      get().pushToast('error', `Couldn't start render: ${err.message}`)
    }
  },

  closeRenderModal: () => set({ renderTask: null }),

  // ---------- toasts ----------
  pushToast: (kind, message) => {
    const id = ++toastSeq
    set((state) => ({ toasts: [...state.toasts, { id, kind, message }] }))
  },

  dismissToast: (id) =>
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  // ---------- reset ----------
  reset: () => {
    set({
      videos: [],
      bgm: [],
      timeline: [],
      settings: { ...DEFAULT_SETTINGS },
      fetchTasks: {},
      lastFetchId: null,
      renderTask: null
    })
    get().loadVideos()
    get().loadBgm()
    get().pushToast('info', 'Studio reset')
  }
}))

export const selectTotalDuration = (state) =>
  state.timeline.reduce((sum, t) => sum + Math.max(0, t.end - t.start), 0)
