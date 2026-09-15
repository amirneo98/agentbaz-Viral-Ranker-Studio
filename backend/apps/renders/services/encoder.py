"""Encoder selection: NVENC first, libx264 fallback.

The final assembly command is built once with codec-agnostic arguments and
run with h264_nvenc.  If the GPU encoder is unavailable (driver mismatch,
container without /dev/nvidia*, older build) the identical command is
re-run with the video codec args swapped for libx264 so the render still
completes on any host.
"""
import logging
import re
import subprocess
import threading

logger = logging.getLogger(__name__)

# Codec argument blocks — swapped as a unit on fallback.
NVENC_ARGS = [
    "-c:v", "h264_nvenc",
    "-preset", "p4",
    "-cq", "19",
    "-b:v", "0",
    "-maxrate", "12M",
    "-bufsize", "24M",
]
X264_ARGS = [
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "18",
]

# stderr signatures of every NVENC failure mode we have seen in the wild.
NVENC_FAILURE_RE = re.compile(
    r"Cannot load|nvenc|No NVIDIA|OpenEncodeSessionEx|not supported",
    re.IGNORECASE,
)

STDERR_TAIL = 2000
ENCODE_TIMEOUT = 3600  # seconds per attempt


class EncoderError(Exception):
    """Both the NVENC attempt and the libx264 fallback failed."""


def swap_to_x264(cmd):
    """Return a copy of *cmd* with the NVENC block replaced by libx264."""
    start = _codec_start(cmd)
    end = start + len(NVENC_ARGS)
    return list(cmd[:start]) + list(X264_ARGS) + list(cmd[end:])


def _codec_start(cmd):
    """Index of the first video-codec argument block inside *cmd*."""
    for i, arg in enumerate(cmd):
        if arg in ("-c:v", "-codec:v"):
            return i
    raise EncoderError("final command has no -c:v block to swap")


def _first_input_index(cmd):
    for i, arg in enumerate(cmd):
        if arg == "-i":
            return i
    return len(cmd)


def _run_ffmpeg(cmd, on_progress=None, total_duration=None):
    """Run one ffmpeg attempt.

    When *on_progress* is given, '-progress pipe:1 -nostats' is injected and
    parsed out_time_ms lines are forwarded as on_progress(seconds_done).
    stderr is drained on a thread so a chatty ffmpeg can never fill the OS
    pipe buffer and deadlock the run.

    Returns (returncode, stderr_tail).
    """
    argv = ["ffmpeg"] + list(cmd)
    if on_progress is not None:
        insert_at = _first_input_index(argv)
        argv[insert_at:insert_at] = ["-progress", "pipe:1", "-nostats"]

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stderr_chunks = []
    stderr_tail = [""]

    def drain_stderr():
        try:
            for chunk in iter(proc.stderr.readline, ""):
                stderr_chunks.append(chunk)
        except (ValueError, OSError):
            pass

    drain_thread = threading.Thread(target=drain_stderr, daemon=True)
    drain_thread.start()

    try:
        if on_progress is not None:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms="):
                    raw = line.split("=", 1)[1].strip()
                    try:
                        seconds_done = int(raw) / 1_000_000.0
                    except ValueError:
                        continue
                    if total_duration and seconds_done > 0:
                        on_progress(min(seconds_done, float(total_duration)))
        else:
            # Nothing to parse; consume to EOF so the child never blocks.
            for _ in iter(proc.stdout.readline, ""):
                pass
    except (ValueError, OSError):
        pass

    try:
        proc.wait(timeout=ENCODE_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        stderr_tail[0] = f"ffmpeg timed out after {ENCODE_TIMEOUT}s"
        return 124, stderr_tail[0]

    drain_thread.join(timeout=5)
    tail = "".join(stderr_chunks)[-STDERR_TAIL:]
    return proc.returncode, tail


def attempt_video_encode(cmd, on_progress=None, total_duration=None):
    """Run *cmd* (argv WITHOUT the ffmpeg binary) trying NVENC first.

    *cmd* must already contain the NVENC codec block; the fallback swaps it
    for libx264.  Returns ``encoder_used`` ("h264_nvenc" | "libx264") and
    raises :class:`EncoderError` when both attempts fail (the message then
    carries the last stderr tail for the task error field).
    """
    attempts = [
        ("h264_nvenc", list(cmd)),
        ("libx264", swap_to_x264(cmd)),
    ]
    last_tail = ""
    for encoder, argv in attempts:
        logger.info("Encoding final assembly with %s", encoder)
        try:
            rc, tail = _run_ffmpeg(
                argv, on_progress=on_progress, total_duration=total_duration
            )
        except OSError as exc:
            last_tail = f"failed to execute ffmpeg: {exc}"
            logger.error("Could not execute ffmpeg (%s): %s", encoder, exc)
            continue

        if rc == 0:
            logger.info("Encode succeeded with %s", encoder)
            return encoder

        last_tail = tail
        if encoder == "h264_nvenc":
            logger.warning(
                "NVENC encode failed (exit %s, nvenc-related=%s) — falling "
                "back to libx264. stderr tail: %s",
                rc,
                bool(NVENC_FAILURE_RE.search(tail or "")),
                (tail or "")[-600:],
            )
        else:
            logger.error("libx264 encode failed (exit %s)", rc)

    raise EncoderError(
        f"All encoders failed. Last ffmpeg stderr tail:\n{last_tail}"
    )
