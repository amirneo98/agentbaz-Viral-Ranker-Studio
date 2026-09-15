"""ffprobe wrapper shared by the ingestion and rendering pipelines."""
import json
import subprocess


class ProbeError(Exception):
    """Raised when ffprobe fails or returns unusable output."""


def probe_media(path):
    """Return media facts for *path* using ffprobe.

    Returns a dict with keys: duration (float seconds), width, height,
    has_audio (bool), video_codec, audio_codec.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out while probing {path}") from exc
    except OSError as exc:
        raise ProbeError(f"failed to execute ffprobe: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[:500]
        raise ProbeError(f"ffprobe failed for {path}: {detail}")

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned invalid JSON for {path}") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ProbeError(f"no video stream found in {path}")

    def _to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    duration = _to_float(data.get("format", {}).get("duration"))
    if duration is None:
        duration = _to_float(video.get("duration"))
    if duration is None:
        raise ProbeError(f"could not determine duration of {path}")

    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": audio is not None,
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", "") if audio else "",
    }
