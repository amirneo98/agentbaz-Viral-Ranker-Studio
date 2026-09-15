---
name: react-canvas-studio
description: Building high-performance 2-column video studio dashboards with synchronized live canvas overlays, drag-and-drop timelines, and style presets.
---

# React & UI Architecture Rules

1. **Two-Column Responsive Layout:**
   - Left Column (Width: ~55%): URL fetcher, Drag-and-drop ranking clip stacks (`@hello-pangea/dnd`), trimming sliders, typography toolbars.
   - Right Column (Width: ~45% sticky): Live WYSIWYG Canvas preview container with strict aspect-ratio containers (`aspect-[9/16]` or `aspect-[16/9]`).

2. **WYSIWYG Overlay Sync:**
   - Render live overlays directly over the HTML5 video element using absolute SVG/CSS layers or Canvas.
   - Sync real-time text styles (font family, stroke width, text shadow, badge fill color) to the active clip state instantly without re-renders of the video player.

3. **Preset Management:**
   - Store and load layout configurations as clean JSON schemas containing:
     `{ "font": str, "fontSize": int, "strokeColor": str, "strokeWidth": int, "badgeBg": str, "videoScale": int, "blurBg": bool, "bgmVolume": float }`.

4. **Async Task Polling:**
   - Use exponential backoff or 1.5s polling intervals with `axios` on `GET /api/tasks/<task_id>/` to display modal progress bars without freezing the UI.