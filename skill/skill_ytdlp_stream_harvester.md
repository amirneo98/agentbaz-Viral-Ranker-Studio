---
name: ytdlp-stream-harvester
description: High-speed zero-download stream URL inspection, metadata extraction, and precise timestamp partial video clipping.
---

# Media Ingestion & yt-dlp Standards

1. **Zero-Wait Stream Extraction (For Preview):**
   - Extract raw direct playback stream and metadata instantly without downloading bytes to disk:
     `yt-dlp -j --no-playlist --skip-download "<URL>"`
   - Fetch direct HLS/MP4 streams for HTML5 `<video>` preview:
     `yt-dlp -g -f "best[ext=mp4][height<=720]/best[height<=720]/best" "<URL>"`

2. **Precision Partial Clipping (Download Sections):**
   - When user locks in-point (`start_time`) and out-point (`end_time`), download ONLY the requested slice:
     `yt-dlp --download-sections "*HH:MM:SS-HH:MM:SS" --force-keyframes-at-cuts -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" "<URL>" -o "<output_path>"`

3. **Bot Evasion & Header Rotation:**
   - Include mobile/desktop user agents and socket timeouts:
     `--user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"`
     `--socket-timeout 15 --retries 3`