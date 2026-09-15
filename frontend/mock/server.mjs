// Dev-only mock backend for Viral Ranker Studio.
// Zero dependencies: node server.mjs  (port 8000)
// Serves /api/* fixtures plus tiny media files, with staged task progress.
import http from 'node:http'
import { readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PORT = 8000
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

// -------------------------------------------------- staged task simulation
// Each task id maps to a script: an array of (poll count -> response) stages.
const tasks = new Map()
let fetchSeq = 0
let renderSeq = 0
let videoSeq = 100

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
      'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS',
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
