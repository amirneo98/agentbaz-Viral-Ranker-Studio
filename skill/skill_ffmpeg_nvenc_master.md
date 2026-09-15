---
name: ffmpeg-nvenc-master
description: Deep expertise in hardware-accelerated video rendering, complex FFmpeg filtergraphs, dynamic background blurs, typography badges, and audio ducking.
---

# FFmpeg & GPU Acceleration Rules

1. **Hardware Acceleration Protocols:**
   - Always prioritize NVIDIA NVENC encoder: `-c:v h264_nvenc -preset p4 -cq 23 -pix_fmt yuv420p`.
   - Implement automatic fallback to `-c:v libx264 -preset veryfast -crf 22` wrapped in Python try/except or process checks.
   - For audio: `-c:a aac -b:a 192k -ar 48000 -ac 2`.

2. **Aspect Ratio Normalization (9:16 & 16:9):**
   - For 9:16 vertical outputs with padded blur:
     `[0:v]split=2[fg][bg_in];`
     `[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=25[bg];`
     `[fg]scale=1080:-2,setsar=1[fg_scaled];`
     `[bg][fg_scaled]overlay=(W-w)/2:(H-h)/2[base_comp];`
   - For 16:9 standard: Scale and pad to 1920x1080 at 30 fps.

3. **Typography & Badge Overlays:**
   - Always reference explicit TTF font paths (e.g., `/app/assets/fonts/ArchivoBlack.ttf`).
   - Draw background text badges using `box=1:boxcolor=black@0.75:boxborderw=12`.
   - Properly escape dynamic text strings (escape `:`, `\`, `'`, and `%`).

4. **Dynamic Sidechain Audio Ducking:**
   - Merge concatenated clip audio with BGM:
     `[main_audio]asplit=2[sc_in][main_out];`
     `[bgm_audio][sc_in]sidechaincompress=threshold=0.1:ratio=4:attack=50:release=300[ducked_bgm];`
     `[main_out][ducked_bgm]amix=inputs=2:duration=first:dropout_transition=2[final_a]`