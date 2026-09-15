"""Audio filtergraph builders for the final assembly step.

The BGM chain implements sidechain ducking: the dialogue track compresses
the music so speech stays intelligible, then both are mixed and limited.

v1.2: the sidechaincompress threshold/ratio are configurable
(``ducking_threshold`` / ``ducking_ratio`` render settings; v1.1 defaults
0.03 / 4 kept — the render spec's 8 is accepted but 4 remains the default
for behavioural parity with shipped v1.1 renders).
"""
AUDIO_RATE = 48000

DEFAULT_DUCKING_THRESHOLD = 0.03
DEFAULT_DUCKING_RATIO = 4


def _clamp_float(value, lo, hi, fallback):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return fallback


def build_bgm_chain(total_duration, bgm_volume,
                    ducking_threshold=DEFAULT_DUCKING_THRESHOLD,
                    ducking_ratio=DEFAULT_DUCKING_RATIO):
    """Return the filter_complex fragment producing the mixed [aout].

    Inputs (referenced by label):
      [0:a] — concatenated dialogue audio from the normalized clips
      [1:a] — looped BGM (input 1 of the final command)

    * bgm is trimmed to the total render duration (it is looped with
      -stream_loop -1 so a short track repeats rather than truncating),
    * volume-scaled to the requested level,
    * sidechain-compressed by the dialogue,
    * mixed with the dialogue and hard-limited to -0.44 dBFS.
    """
    threshold = _clamp_float(
        ducking_threshold, 0.0001, 1.0, DEFAULT_DUCKING_THRESHOLD
    )
    ratio = _clamp_float(
        ducking_ratio, 1.0, 20.0, DEFAULT_DUCKING_RATIO
    )
    return (
        f"[1:a]atrim=duration={float(total_duration):.6f},"
        "asetpts=PTS-STARTPTS,"
        f"volume={float(bgm_volume):.4f},"
        f"aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo[bgm];"
        f"[bgm][0:a]sidechaincompress=threshold={threshold:.4f}"
        f":ratio={ratio:.2f}:attack=25"
        ":release=300[ducked];"
        "[0:a][ducked]amix=inputs=2:duration=first:normalize=0,"
        "alimiter=limit=0.95[aout]"
    )


def build_plain_audio_chain():
    """Audio chain when no BGM is selected: pass the dialogue through."""
    return "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[aout]"
