"""Audio filtergraph builders for the final assembly step.

The BGM chain implements sidechain ducking: the dialogue track compresses
the music so speech stays intelligible, then both are mixed and limited.
"""
AUDIO_RATE = 48000


def build_bgm_chain(total_duration, bgm_volume):
    """Return the filter_complex fragment producing the mixed [aout].

    Inputs (referenced by label):
      [0:a] — concatenated dialogue audio from the normalized clips
      [1:a] — looped BGM (input 1 of the final command)

    * bgm is trimmed to the total render duration (it is looped with
      -stream_loop -1 so a short track repeats rather than truncating),
    * volume-scaled to the requested level,
    * sidechain-compressed by the dialogue (~35% duck),
    * mixed with the dialogue and hard-limited to -0.44 dBFS.
    """
    return (
        f"[1:a]atrim=duration={float(total_duration):.6f},"
        "asetpts=PTS-STARTPTS,"
        f"volume={float(bgm_volume):.4f},"
        f"aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo[bgm];"
        "[bgm][0:a]sidechaincompress=threshold=0.03:ratio=4:attack=25"
        ":release=300[ducked];"
        "[0:a][ducked]amix=inputs=2:duration=first:normalize=0,"
        "alimiter=limit=0.95[aout]"
    )


def build_plain_audio_chain():
    """Audio chain when no BGM is selected: pass the dialogue through."""
    return "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[aout]"
