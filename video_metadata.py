"""Probe merged video metadata and prepare Telegram-compatible thumbnail."""
import asyncio
import json
import logging
import math
from pathlib import Path

LOG = logging.getLogger("lecturebot.media")


async def _run(*args, timeout=90):
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("Media probe timed out")
    if proc.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-500:])
    return stdout


async def video_info(path, part_dir=None, part_count=0):
    path = Path(path)
    raw = await _run(
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,width,height,duration",
        "-of", "json", str(path),
    )
    info = json.loads(raw)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("Merged output has no video stream")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width < 1 or height < 1:
        raise RuntimeError("Merged output has invalid frame dimensions")

    def number(value):
        try:
            v = float(value)
            return v if math.isfinite(v) and v > 0 else 0.0
        except (TypeError, ValueError):
            return 0.0

    duration = number(info.get("format", {}).get("duration"))
    duration = max(duration, number(video.get("duration")))

    if duration <= 0 and part_dir and part_count:
        LOG.warning("Merged file has no duration; summing input part durations")
        total = 0.0
        for i in range(part_count):
            part = Path(part_dir) / f"part_{i:03}.mp4"
            raw = await _run(
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of", "default=nw=1:nk=1", str(part),
            )
            total += number(raw.decode().strip())
        duration = total

    if duration <= 0:
        raise RuntimeError(
            "Cannot determine video duration; verify FFmpeg concat and source timestamps"
        )

    return {
        "duration": max(1, int(math.ceil(duration))),
        "width": width,
        "height": height,
    }


async def create_thumbnail(video, destination, duration):
    video = Path(video)
    destination = Path(destination)
    # Avoid blindly picking the first frame, often a splash or black frame.
    seek = min(max(float(duration) * 0.12, 1.0), 90.0)
    for position in (seek, 1.0, 0.0):
        destination.unlink(missing_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-y", "-ss", str(position), "-i", str(video),
            "-frames:v", "1", "-vf", "scale=320:-2",
            "-q:v", "5", str(destination),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode == 0 and destination.is_file():
            if 0 < destination.stat().st_size <= 200 * 1024:
                return str(destination)
        LOG.warning("Thumbnail attempt at %ss did not produce a valid JPEG: %s",
                    position, err.decode(errors="replace")[-120:])
    return None
