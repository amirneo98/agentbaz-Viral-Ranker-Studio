// services/api.js — typed wrappers over the raw `request` client.
// v1.1 endpoints preserved; v1.2 adds stream-info, clips, presets,
// download-section and the new render payload.

import request from '../api/client'

// ---------- v1.1 (dashboard) ----------
export const getVideos = () => request('/api/videos/')
export const deleteVideo = (id) => request(`/api/videos/${id}/`, { method: 'DELETE' })
export const getBgm = () => request('/api/bgm/')
export const getTask = (id) => request(`/api/tasks/${id}/`)
export const postFetch = (url) =>
  request('/api/fetch/', { method: 'POST', body: { url } })
export const getHealth = () => request('/api/health/')

// ---------- v1.2 (studio) ----------
// POST /api/stream-info/ {url} -> {title, duration, thumbnail, stream_url, stream_type}
export const postStreamInfo = (url) =>
  request('/api/stream-info/', { method: 'POST', body: { url } })

// GET /api/clips/ -> {clips: [...]} ; POST creates a clip
export const getClips = () => request('/api/clips/')
export const postClip = (clip) =>
  request('/api/clips/', { method: 'POST', body: clip })

// PATCH /api/clips/<id>/ — partial update (debounced by callers)
export const patchClip = (id, patch) =>
  request(`/api/clips/${id}/`, { method: 'PATCH', body: patch })

export const deleteClip = (id) =>
  request(`/api/clips/${id}/`, { method: 'DELETE' })

// POST /api/clips/reorder/ {ids: []}
export const postReorderClips = (ids) =>
  request('/api/clips/reorder/', { method: 'POST', body: { ids } })

// POST /api/download-section/ {clip_id} -> {task_id}
export const postDownloadSection = (clipId) =>
  request('/api/download-section/', { method: 'POST', body: { clip_id: clipId } })

// ---------- presets ----------
export const getPresets = () => request('/api/presets/')
export const postPreset = (preset) =>
  request('/api/presets/', { method: 'POST', body: preset })
export const putPreset = (id, preset) =>
  request(`/api/presets/${id}/`, { method: 'PUT', body: preset })
export const deletePreset = (id) =>
  request(`/api/presets/${id}/`, { method: 'DELETE' })

// ---------- render (v1.2 payload) ----------
export const postRender = (payload) =>
  request('/api/render/', { method: 'POST', body: payload })

export default {
  getVideos,
  deleteVideo,
  getBgm,
  getTask,
  postFetch,
  getHealth,
  postStreamInfo,
  getClips,
  postClip,
  patchClip,
  deleteClip,
  postReorderClips,
  postDownloadSection,
  getPresets,
  postPreset,
  putPreset,
  deletePreset,
  postRender
}
