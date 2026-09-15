import { useEffect, useMemo, useRef, useState } from 'react'
import Hls from 'hls.js'
import { useStudioV2Store } from '../store/useStudioV2Store'

const FONT_STACKS = {
  'Archivo Black': "'Archivo Black', 'Arial Black', sans-serif",
  'Bebas Neue': "'Bebas Neue', 'Impact', sans-serif",
  Rubik: 'Rubik, sans-serif',
  Montserrat: 'Montserrat, sans-serif',
  Vazirmatn: 'Vazirmatn, Tahoma, sans-serif'
}

const FONT_LINKS = [
  'https://fonts.googleapis.com/css2?family=Archivo+Black&family=Bebas+Neue&family=Rubik:wght@700;900&family=Montserrat:wght@700;900&family=Vazirmatn:wght@700;900&display=swap'
]

let fontsLoaded = false
function ensureFonts() {
  if (fontsLoaded || typeof document === 'undefined') return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = FONT_LINKS[0]
  document.head.appendChild(link)
  fontsLoaded = true
}

/**
 * Renders a styled overlay text block (title + optional subtitle) exactly
 * matching the clip.style fields: font, size, fill, stroke, shadow and
 * position. Mirrors the ffmpeg drawtext look via -webkit-text-stroke.
 */
export function StyleOverlayText({ style, scale = 1 }) {
  if (!style) return null
  const pos = style.textPosition || 'top'
  const stack = FONT_STACKS[style.font] || FONT_STACKS.Rubik
  const textStyle = {
    fontFamily: stack,
    fontSize: `${(style.fontSize || 56) * scale}px`,
    fontWeight: 900,
    lineHeight: 1.1,
    color: style.textColor || '#FFFFFF',
    WebkitTextStroke: `${(style.strokeWidth || 0) * scale}px ${style.strokeColor || '#000000'}`,
    paintOrder: 'stroke fill',
    textShadow: style.shadow ? `0 ${4 * scale}px ${12 * scale}px rgba(0,0,0,0.75)` : 'none',
    whiteSpace: 'pre-wrap'
  }
  return (
    <div
      className="pointer-events-none absolute inset-x-0 flex flex-col items-center px-[6%] text-center"
      style={{
        [pos === 'top' ? 'top' : pos === 'bottom' ? 'bottom' : 'top']: pos === 'center' ? '50%' : '8%',
        transform: pos === 'center' ? 'translateY(-50%)' : undefined,
        gap: `${6 * scale}px`
      }}
    >
      {style.title ? (
        <span style={textStyle}>{style.title}</span>
      ) : null}
      {style.subtitle ? (
        <span style={{ ...textStyle, fontSize: `${(style.fontSize || 56) * 0.55 * scale}px` }}>
          {style.subtitle}
        </span>
      ) : null}
    </div>
  )
}

/**
 * LEFT-margin vertical rank rail: numbers 1..N with the active rank
 * highlighted. Positioned like the rendered compilation's rank strip.
 */
function RankRail({ clips, activeRank }) {
  return (
    <div className="pointer-events-none absolute left-[3%] top-1/2 z-20 flex -translate-y-1/2 flex-col gap-1.5">
      {clips.map((c) => {
        const isActive = c.rank === activeRank
        return (
          <div
            key={c.id}
            className={`flex h-6 w-6 items-center justify-center rounded font-black transition-all ${
              isActive
                ? 'scale-110 bg-accent-start text-white shadow-lg ring-2 ring-white/70'
                : 'bg-black/60 text-white/60'
            }`}
            style={{ fontSize: 11 }}
            aria-hidden="true"
          >
            {c.rank}
          </div>
        )
      })}
    </div>
  )
}

function hexToRgba(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim())
  if (!m) return `rgba(0,0,0,${alpha})`
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`
}

/**
 * Live WYSIWYG preview (RIGHT column, sticky):
 * - aspect container (9:16 / 16:9 from the top bar toggle)
 * - active clip's stream playing muted/looping within its trim range
 * - DOM overlays synced to clip.style (title/subtitle/stroke/shadow/badge)
 * - videoScalePct + blur background behave like the render pipeline
 */
export default function LiveCanvasPreview() {
  const clips = useStudioV2Store((s) => s.clips)
  const activeClipId = useStudioV2Store((s) => s.activeClipId)
  const aspect = useStudioV2Store((s) => s.aspect)
  const settings = useStudioV2Store((s) => s.settings)

  const videoRef = useRef(null)
  const hlsRef = useRef(null)
  const [videoError, setVideoError] = useState(false)
  const [containerW, setContainerW] = useState(0)
  const containerRef = useRef(null)

  const active = useMemo(
    () => clips.find((c) => c.id === activeClipId) || null,
    [clips, activeClipId]
  )

  useEffect(ensureFonts, [])

  // measure container for overlay scaling (overlays designed at 720px width)
  useEffect(() => {
    const el = containerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setContainerW(e.contentRect.width)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const scale = containerW > 0 ? containerW / 720 : 0.6
  const start = active ? active.start_time : 0
  const end = active ? active.end_time : 0

  // (re)attach the media source when the active clip's stream changes
  useEffect(() => {
    const video = videoRef.current
    setVideoError(false)
    if (!video || !active || !active.stream_url || active.stream_type === 'none') return undefined

    if (hlsRef.current) {
      hlsRef.current.destroy()
      hlsRef.current = null
    }

    if (active.stream_type === 'hls' && Hls.isSupported()) {
      const hls = new Hls({ enableWorker: false })
      hlsRef.current = hls
      hls.loadSource(active.stream_url)
      hls.attachMedia(video)
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (data && data.fatal) setVideoError(true)
      })
      return () => {
        hls.destroy()
        if (hlsRef.current === hls) hlsRef.current = null
      }
    }

    video.src = active.stream_url
    video.load()
    video.play().catch(() => {
      /* autoplay muted should succeed; ignore rejection */
    })
    return undefined
  }, [active && active.id, active && active.stream_url, active && active.stream_type])

  // trim-range loop: seek back to start when out of [start, end]
  useEffect(() => {
    const video = videoRef.current
    if (!video || !active || active.stream_type === 'none') return undefined
    const onTime = () => {
      if (video.currentTime < start - 0.25 || video.currentTime > end + 0.25) {
        video.currentTime = Math.max(0, start)
        video.play().catch(() => {})
      }
    }
    const onErr = () => setVideoError(true)
    video.addEventListener('timeupdate', onTime)
    video.addEventListener('error', onErr)
    return () => {
      video.removeEventListener('timeupdate', onTime)
      video.removeEventListener('error', onErr)
    }
  }, [active && active.id, start, end, active && active.stream_type])

  const scalePct = (settings.videoScalePct ?? 80) / 100
  const useBlur = !!settings.backgroundBlur

  return (
    <section
      className="panel sticky top-[76px] flex flex-col items-center gap-3 p-4"
      aria-label="Live preview"
      data-testid="live-preview"
    >
      <div className="flex w-full items-center justify-between">
        <h2 className="text-sm font-bold uppercase tracking-wider text-ink">Live Preview</h2>
        {active ? (
          <span className="rounded bg-edge px-2 py-0.5 font-mono text-[11px] text-muted">
            #{active.rank} · {active.stream_type || 'none'}
          </span>
        ) : (
          <span className="text-[11px] text-muted">no clip selected</span>
        )}
      </div>

      <div
        ref={containerRef}
        className={`relative w-full overflow-hidden rounded-xl bg-black ring-1 ring-edge ${
          aspect === '9:16' ? 'aspect-[9/16] max-h-[70vh] w-auto mx-auto' : 'aspect-video'
        }`}
        data-testid="preview-frame"
      >
        {/* blurred fill background */}
        {useBlur && active && active.stream_type !== 'none' && !videoError && (
          <video
            src={active.stream_type === 'hls' ? undefined : active.stream_url}
            muted
            playsInline
            aria-hidden="true"
            className="absolute inset-0 h-full w-full object-cover opacity-70"
            style={{ filter: `blur(${settings.blurSigma || 12}px)` }}
            ref={(el) => {
              if (el && active.stream_type !== 'hls') {
                el.src = active.stream_url
              }
            }}
          />
        )}

        {/* main scaled video */}
        {active && active.stream_type !== 'none' && !videoError && (
          <video
            ref={videoRef}
            muted
            autoPlay
            loop
            playsInline
            className="absolute inset-0 m-auto"
            style={{
              width: `${scalePct * 100}%`,
              height: `${scalePct * 100}%`,
              objectFit: 'contain',
              zIndex: 10
            }}
          />
        )}

        {/* fallbacks */}
        {(!active || active.stream_type === 'none' || videoError) && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-base p-6 text-center">
            {active && active.thumbnail ? (
              <img
                src={active.thumbnail}
                alt=""
                className="max-h-[60%] w-auto rounded-lg opacity-60"
              />
            ) : (
              <svg viewBox="0 0 24 24" className="h-12 w-12 text-white/20" fill="currentColor">
                <path d="M8 5.14v14l11-7-11-7z" />
              </svg>
            )}
            <p className="max-w-[80%] text-xs leading-relaxed text-muted">
              {!active
                ? 'Add a clip via the URL bar to see the live preview.'
                : active.stream_type === 'none'
                  ? 'No direct stream available for this source. Preview shows the thumbnail only — rendering still works via server-side download.'
                  : 'Stream failed to load. The render pipeline may still handle this source.'}
            </p>
          </div>
        )}

        {/* master title (top), like the render title card */}
        {settings.masterTitle && settings.masterTitleStyle && (
          <div className="pointer-events-none absolute inset-x-0 top-[2%] z-20 text-center">
            <span
              style={{
                fontFamily:
                  FONT_STACKS[settings.masterTitleStyle.font] || FONT_STACKS.Rubik,
                fontSize: `${(settings.masterTitleStyle.fontSize || 72) * 0.45 * scale}px`,
                fontWeight: 900,
                color: settings.masterTitleStyle.textColor || '#FFFFFF',
                WebkitTextStroke: `${(settings.masterTitleStyle.strokeWidth || 0) * 0.45 * scale}px ${
                  settings.masterTitleStyle.strokeColor || '#000000'
                }`,
                paintOrder: 'stroke fill',
                textShadow: settings.masterTitleStyle.shadow
                  ? `0 ${3 * scale}px ${10 * scale}px rgba(0,0,0,0.75)`
                  : 'none'
              }}
            >
              {settings.masterTitle}
            </span>
          </div>
        )}

        {/* rank rail on the left margin */}
        {clips.length > 0 && active && <RankRail clips={clips} activeRank={active.rank} />}

        {/* rank badge + styled overlay text */}
        {active && (
          <>
            <div
              className="pointer-events-none absolute left-[10%] top-[6%] z-20 flex h-10 w-10 items-center justify-center rounded-lg font-black text-white"
              style={{
                background: hexToRgba(active.style?.badgeBg, active.style?.badgeOpacity ?? 0.9),
                fontSize: 18 * Math.max(scale, 0.6)
              }}
              aria-hidden="true"
            >
              {active.rank}
            </div>
            <StyleOverlayText style={active.style} scale={scale} />
          </>
        )}
      </div>

      <p className="text-center text-[11px] text-muted">
        Preview is approximate — final render is produced server-side with ffmpeg.
      </p>
    </section>
  )
}
