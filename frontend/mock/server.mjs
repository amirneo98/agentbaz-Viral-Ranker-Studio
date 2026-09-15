// Dev-only mock backend for Viral Ranker Studio.
// Zero dependencies: node server.mjs  (port 8000)
// Serves /api/* fixtures plus tiny media files, with staged task progress.
import http from 'node:http'
import { readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PORT = Number(process.env.PORT) || 8000
const MIME = {
  '.mp4': 'video/mp4',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.m4a': 'audio/mp4',
  '.mp3': 'audio/mpeg',
  '.json': 'application/json'
}

// ---------------------------------------------------------------- fixtures
const VIDEOS = [
  {
    id: 1,
    title: 'Cat Falls Into Aquarium — Full Send',
    source_url: 'https://example.com/videos/cat-aquarium',
    duration: 14.2,
    width: 1920,
    height: 1080,
    has_audio: true,
    thumbnail_url: '/media/thumbs/thumb-1.jpg',
    video_url: '/media/videos/clip-1.mp4',
    created_at: '2026-09-14T10:12:00Z'
  },
  {
    id: 2,
    title: 'Drone Chase Through Abandoned Waterpark',
    source_url: 'https://example.com/videos/waterpark-drone',
    duration: 22.8,
    width: 1080,
    height: 1920,
    has_audio: true,
    thumbnail_url: '/media/thumbs/thumb-2.jpg',
    video_url: '/media/videos/clip-2.mp4',
    created_at: '2026-09-14T11:03:00Z'
  },
  {
    id: 3,
    title: 'Street Busker Nails Impossible Guitar Solo (silent cam)',
    source_url: 'https://example.com/videos/busker-solo',
    duration: 31.5,
    width: 1280,
    height: 720,
    has_audio: false,
    thumbnail_url: '/media/thumbs/thumb-3.jpg',
    video_url: '/media/videos/clip-3.mp4',
    created_at: '2026-09-14T12:47:00Z'
  }
]

const BGM = [
  { id: 1, name: 'Upbeat Phonk (Loop)', duration: 120, url: '/media/bgm/phonk.mp3' },
  { id: 2, name: 'Cinematic Rise', duration: 95, url: '/media/bgm/rise.mp3' },
  { id: 3, name: 'Lo-fi Chill Beat', duration: 180, url: '/media/bgm/lofi.mp3' }
]

// ---------------------------------------------------------------- v1.2 studio
const SAMPLE_STREAM = 'https://samplelib.com/mp4/sample-5s.mp4'

const DEFAULT_STYLE = {
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

const CLIPS = [
  {
    id: 101,
    source_url: 'https://example.com/videos/cat-aquarium',
    title: 'Cat Falls Into Aquarium — Full Send',
    subtitle: 'the splash was enormous',
    rank: 1,
    start_time: 0,
    end_time: 5,
    stream_url: SAMPLE_STREAM,
    stream_type: 'mp4',
    thumbnail: '/media/thumbs/thumb-1.jpg',
    duration: 5,
    style: { ...DEFAULT_STYLE, title: 'Cat Falls Into Aquarium — Full Send', subtitle: 'the splash was enormous' },
    volume: 1.0,
    hd_status: 'pending'
  },
  {
    id: 102,
    source_url: 'https://example.com/videos/waterpark-drone',
    title: 'Drone Chase Through Abandoned Waterpark',
    subtitle: '',
    rank: 2,
    start_time: 0,
    end_time: 5,
    stream_url: SAMPLE_STREAM,
    stream_type: 'mp4',
    thumbnail: '/media/thumbs/thumb-2.jpg',
    duration: 5,
    style: { ...DEFAULT_STYLE, title: 'Drone Chase Through Abandoned Waterpark' },
    volume: 0.8,
    hd_status: 'pending'
  }
]

const PRESETS = [
  {
    id: 1,
    name: 'Bold Phonk',
    data: {
      font: 'Bebas Neue',
      fontSize: 72,
      textColor: '#FFFFFF',
      strokeColor: '#000000',
      strokeWidth: 6,
      shadow: true,
      badgeBg: '#FF4B4B',
      badgeOpacity: 0.95,
      textPosition: 'center',
      videoScalePct: 90,
      blurBg: true,
      blurSigma: 16,
      bgmVolume: 0.5,
      duckingThreshold: 0.05,
      duckingRatio: 0.2
    }
  },
  {
    id: 2,
    name: 'Minimal Light',
    data: {
      font: 'Rubik',
      fontSize: 40,
      textColor: '#FFFFFF',
      strokeColor: '#111111',
      strokeWidth: 1,
      shadow: false,
      badgeBg: '#3B82F6',
      badgeOpacity: 0.7,
      textPosition: 'bottom',
      videoScalePct: 75,
      blurBg: false,
      blurSigma: 8,
      bgmVolume: 0.3,
      duckingThreshold: 0.08,
      duckingRatio: 0.35
    }
  }
]

// -------------------------------------------------- staged task simulation
// Each task id maps to a script: an array of (poll count -> response) stages.
const tasks = new Map()
let fetchSeq = 0
let renderSeq = 0
let videoSeq = 100
let presetSeq = 2
let clipSeq = 200

function nextClipId() {
  return ++clipSeq
}

// HD download-section: PENDING -> PROCESSING (increments) -> SUCCESS
// with the clip's hd_status in the result.
function downloadStages(clipId) {
  return [
    { status: 'PENDING', progress: 5 },
    { status: 'PROCESSING', progress: 30 },
    { status: 'PROCESSING', progress: 62 },
    {
      status: 'SUCCESS',
      progress: 100,
      result: { clip_id: clipId, hd_status: 'ready', file: `media/hd/clip-${clipId}.mp4` }
    }
  ]
}

function stagesFor(kind) {
  if (kind === 'FETCH') {
    const id = ++videoSeq
    return [
      { status: 'PENDING', progress: 5 },
      { status: 'PROCESSING', progress: 30 },
      { status: 'PROCESSING', progress: 72 },
      {
        status: 'SUCCESS',
        progress: 100,
        result: {
          video_id: id,
          title: 'Skater Lands Trick After 47 Tries',
          duration: 18.4,
          width: 1080,
          height: 1920,
          has_audio: true,
          thumbnail_url: '', // exercises the gradient fallback in ClipCard
          video_url: '/media/videos/fetched.mp4'
        },
        video: {
          id,
          title: 'Skater Lands Trick After 47 Tries',
          source_url: 'https://example.com/videos/fetched',
          duration: 18.4,
          width: 1080,
          height: 1920,
          has_audio: true,
          thumbnail_url: '',
          video_url: '/media/videos/fetched.mp4',
          created_at: new Date().toISOString()
        }
      }
    ]
  }
  return [
    { status: 'PENDING', progress: 3 },
    { status: 'PROCESSING', progress: 21 },
    { status: 'PROCESSING', progress: 45 },
    { status: 'PROCESSING', progress: 68 },
    { status: 'PROCESSING', progress: 89 },
    {
      status: 'SUCCESS',
      progress: 100,
      result: {
        download_url: '/media/renders/mock.mp4',
        preview_url: '/media/renders/mock.mp4',
        output_file: 'renders/rank_compilation_20260915.mp4',
        encoder_used: 'h264_nvenc',
        duration: 62.4
      }
    }
  ]
}

// ---------------------------------------------------------------- helpers
function json(res, code, body) {
  const payload = JSON.stringify(body)
  res.writeHead(code, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
    'Access-Control-Allow-Origin': '*'
  })
  res.end(payload)
}

async function body(req) {
  const chunks = []
  for await (const c of req) chunks.push(c)
  const raw = Buffer.concat(chunks).toString('utf8')
  if (!raw) return {}
  try {
    return JSON.parse(raw)
  } catch {
    return {}
  }
}

async function serveMedia(req, res, urlPath) {
  // Serve files from mock/media (mirrors the backend layout) with 404 fallback.
  const rel = urlPath.replace(/^\/media\//, '')
  const file = path.join(__dirname, 'media', rel)
  if (!existsSync(file)) {
    res.writeHead(404, { 'Content-Type': 'text/plain' })
    res.end('not found')
    return
  }
  const data = await readFile(file)
  res.writeHead(200, {
    'Content-Type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream',
    'Content-Length': data.length,
    'Accept-Ranges': 'bytes'
  })
  res.end(data)
}

// ---------------------------------------------------------------- server
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)
  const p = url.pathname

  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    })
    res.end()
    return
  }

  try {
    // ---- API ----
    if (p === '/api/health/' && req.method === 'GET') {
      return json(res, 200, { status: 'ok' })
    }

    if (p === '/api/videos/' && req.method === 'GET') {
      return json(res, 200, { videos: VIDEOS })
    }

    if (p === '/api/bgm/' && req.method === 'GET') {
      return json(res, 200, { bgm: BGM })
    }

    let m = p.match(/^\/api\/videos\/(\d+)\/$/)
    if (m && req.method === 'DELETE') {
      const idx = VIDEOS.findIndex((v) => v.id === Number(m[1]))
      if (idx === -1) return json(res, 404, { detail: 'Not found.' })
      VIDEOS.splice(idx, 1)
      res.writeHead(204, { 'Access-Control-Allow-Origin': '*' })
      return res.end()
    }

    if (p === '/api/fetch/' && req.method === 'POST') {
      const { url: videoUrl } = await body(req)
      const id = `fetch-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const stages = stagesFor('FETCH')
      // remember the source URL on the final stage's video entry
      if (stages.at(-1).video) stages.at(-1).video.source_url = videoUrl
      tasks.set(id, { kind: 'FETCH', stage: 0, stages })
      console.log(`[mock] POST /api/fetch/ url=${videoUrl} -> task ${id}`)
      return json(res, 202, { task_id: id, status: 'PENDING' })
    }

    if (p === '/api/render/' && req.method === 'POST') {
      const payload = await body(req)
      const id = `render-${++renderSeq}`
      tasks.set(id, { kind: 'RENDER', stage: 0, stages: stagesFor('RENDER') })
      console.log('[mock] POST /api/render/ payload:', JSON.stringify(payload))
      return json(res, 202, { task_id: id })
    }

    m = p.match(/^\/api\/tasks\/([^/]+)\/$/)
    if (m && req.method === 'GET') {
      const task = tasks.get(m[1])
      if (!task) return json(res, 404, { detail: 'Task not found.' })
      const stage = Math.min(task.stage, task.stages.length - 1)
      const s = task.stages[stage]
      const isFinal = stage >= task.stages.length - 1
      if (!isFinal) task.stage += 1 // advance each poll
      // when a FETCH task reaches SUCCESS, make the video visible in /api/videos/
      if (isFinal && s.status === 'SUCCESS' && s.video && !s.video.__added) {
        s.video.__added = true
        VIDEOS.push(s.video)
        console.log(`[mock] fetch task ${m[1]} complete — video ${s.video.id} added to library`)
      }
      console.log(`[mock] GET /api/tasks/${m[1]}/ -> ${s.status} ${s.progress}%`)
      return json(res, 200, {
        id: m[1],
        task_type: task.kind,
        status: s.status,
        progress: s.progress,
        error: s.error || '',
        result: s.result || null
      })
    }

    // ---- v1.2: stream info ----
    if (p === '/api/stream-info/' && req.method === 'POST') {
      const { url: videoUrl } = await body(req)
      console.log(`[mock] POST /api/stream-info/ url=${videoUrl}`)
      // small artificial delay to exercise the spinner
      await new Promise((r) => setTimeout(r, 700))
      return json(res, 200, {
        title: 'Sample Stream Clip',
        duration: 5,
        thumbnail: '/media/thumbs/thumb-4.jpg',
        stream_url: SAMPLE_STREAM,
        stream_type: 'mp4'
      })
    }

    // ---- v1.2: clips CRUD ----
    if (p === '/api/clips/' && req.method === 'GET') {
      return json(res, 200, { clips: [...CLIPS].sort((a, b) => a.rank - b.rank) })
    }

    if (p === '/api/clips/' && req.method === 'POST') {
      const payload = await body(req)
      const id = nextClipId()
      const clip = {
        id,
        source_url: payload.source_url || '',
        title: payload.title || 'Untitled clip',
        subtitle: payload.subtitle || '',
        rank: payload.rank ?? CLIPS.reduce((m, c) => Math.max(m, c.rank), 0) + 1,
        start_time: payload.start_time ?? 0,
        end_time: payload.end_time ?? 5,
        stream_url: payload.stream_url || '',
        stream_type: payload.stream_type || 'none',
        thumbnail: payload.thumbnail || '',
        duration: payload.duration ?? 5,
        style: { ...DEFAULT_STYLE, ...(payload.style || {}), title: payload.title || 'Untitled clip' },
        volume: payload.volume ?? 1.0,
        hd_status: payload.hd_status || 'pending'
      }
      CLIPS.push(clip)
      console.log(`[mock] POST /api/clips/ -> created clip ${id} (rank ${clip.rank})`)
      return json(res, 201, clip)
    }

    m = p.match(/^\/api\/clips\/(\d+)\/$/)
    if (m && req.method === 'PATCH') {
      const idx = CLIPS.findIndex((c) => c.id === Number(m[1]))
      if (idx === -1) return json(res, 404, { detail: 'Clip not found.' })
      const patch = await body(req)
      CLIPS[idx] = { ...CLIPS[idx], ...patch }
      if (patch.style) CLIPS[idx].style = { ...CLIPS[idx].style, ...patch.style }
      console.log(`[mock] PATCH /api/clips/${m[1]}/ keys=${Object.keys(patch).join(',')}`)
      return json(res, 200, CLIPS[idx])
    }

    m = p.match(/^\/api\/clips\/(\d+)\/$/)
    if (m && req.method === 'DELETE') {
      const idx = CLIPS.findIndex((c) => c.id === Number(m[1]))
      if (idx === -1) return json(res, 404, { detail: 'Clip not found.' })
      CLIPS.splice(idx, 1)
      // compact ranks 1..N
      const survivors = CLIPS.slice().sort((a, b) => a.rank - b.rank)
      survivors.forEach((c, i) => {
        c.rank = i + 1
      })
      res.writeHead(204, { 'Access-Control-Allow-Origin': '*' })
      return res.end()
    }

    if (p === '/api/clips/reorder/' && req.method === 'POST') {
      const { ids } = await body(req)
      if (Array.isArray(ids)) {
        ids.forEach((id, i) => {
          const clip = CLIPS.find((c) => c.id === id)
          if (clip) clip.rank = i + 1
        })
        console.log(`[mock] POST /api/clips/reorder/ ids=${JSON.stringify(ids)}`)
      }
      return json(res, 200, { clips: [...CLIPS].sort((a, b) => a.rank - b.rank) })
    }

    // ---- v1.2: HD download-section ----
    if (p === '/api/download-section/' && req.method === 'POST') {
      const { clip_id: clipId } = await body(req)
      const clip = CLIPS.find((c) => c.id === Number(clipId))
      if (!clip) return json(res, 404, { detail: 'Clip not found.' })
      clip.hd_status = 'downloading'
      const id = `dl-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      tasks.set(id, {
        kind: 'DOWNLOAD',
        stage: 0,
        clipId: clip.id,
        stages: downloadStages(clip.id)
      })
      console.log(`[mock] POST /api/download-section/ clip=${clipId} -> task ${id}`)
      return json(res, 202, { task_id: id })
    }

    // ---- v1.2: presets CRUD ----
    if (p === '/api/presets/' && req.method === 'GET') {
      return json(res, 200, { presets: PRESETS })
    }

    if (p === '/api/presets/' && req.method === 'POST') {
      const payload = await body(req)
      const preset = {
        id: ++presetSeq,
        name: payload.name || 'Unnamed preset',
        data: payload.data || {}
      }
      PRESETS.push(preset)
      console.log(`[mock] POST /api/presets/ -> preset ${preset.id} "${preset.name}"`)
      return json(res, 201, preset)
    }

    m = p.match(/^\/api\/presets\/(\d+)\/$/)
    if (m && (req.method === 'PUT' || req.method === 'PATCH')) {
      const idx = PRESETS.findIndex((x) => x.id === Number(m[1]))
      if (idx === -1) return json(res, 404, { detail: 'Preset not found.' })
      const payload = await body(req)
      PRESETS[idx] = {
        ...PRESETS[idx],
        ...(payload.name ? { name: payload.name } : {}),
        ...(payload.data ? { data: payload.data } : {})
      }
      return json(res, 200, PRESETS[idx])
    }

    m = p.match(/^\/api\/presets\/(\d+)\/$/)
    if (m && req.method === 'DELETE') {
      const idx = PRESETS.findIndex((x) => x.id === Number(m[1]))
      if (idx === -1) return json(res, 404, { detail: 'Preset not found.' })
      PRESETS.splice(idx, 1)
      res.writeHead(204, { 'Access-Control-Allow-Origin': '*' })
      return res.end()
    }

    // ---- media ----
    if (p.startsWith('/media/')) {
      return serveMedia(req, res, p)
    }

    res.writeHead(404, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ detail: 'Not found.' }))
  } catch (err) {
    console.error('[mock] error:', err)
    json(res, 500, { detail: 'Mock server error.' })
  }
})

server.listen(PORT, () => {
  console.log(`[mock] Viral Ranker Studio mock API listening on http://localhost:${PORT}`)
})
