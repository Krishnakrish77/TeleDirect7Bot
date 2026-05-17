"""
On-demand HLS pipeline.

Each `<video>` request hits a manifest endpoint that returns a static M3U8
listing N segments. The browser then fetches each segment from a segment
endpoint, which spawns ffmpeg on demand, seeks into the source file (served
by our own /{hash}{id} route via HTTP), and streams an MPEG-TS chunk back.

Inputs come from Telegram, transmuxed (`-c copy`) — no re-encoding. So this
only works when the source has codecs MPEG-TS can carry: H.264/HEVC video,
AAC/AC3/EAC3/MP3 audio. ffprobe runs first to confirm; incompatible files
get the existing VLC fallback in the watch page.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Optional, Tuple

from main.vars import Var


# Target segment length. 6s is HLS-standard and works well with most encoders.
SEGMENT_SECONDS = 6

# Cache codec/duration probes for an hour — the source file never changes.
PROBE_TTL = 60 * 60

# Cap concurrent ffmpeg subprocesses so a free-tier instance can't be DOSed
# into oblivion by a handful of viewers all hitting "play" at once.
MAX_CONCURRENT_SEGMENTS = int(os.environ.get("HLS_MAX_CONCURRENT", "2"))

# Codecs MPEG-TS can carry without re-encoding.
HLS_COMPAT_VIDEO = {"h264", "hevc"}
HLS_COMPAT_AUDIO = {"aac", "ac3", "eac3", "mp3", "mp2"}

# Audio codecs browsers can play natively in HLS without re-encoding.
# AAC and MP3 are universal in MSE; AC3/EAC3 don't play in Chrome/Firefox so
# we transcode those to AAC when serving HLS.
BROWSER_AUDIO_OK = {"aac", "mp3", "mp2"}


@dataclass(frozen=True)
class ProbeResult:
    duration: float
    video_codec: Optional[str]
    audio_codec: Optional[str]

    @property
    def hls_compatible(self) -> bool:
        if self.duration <= 0:
            return False
        if self.video_codec not in HLS_COMPAT_VIDEO:
            return False
        # Audio is optional — a silent video is still streamable.
        if self.audio_codec is not None and self.audio_codec not in HLS_COMPAT_AUDIO:
            return False
        return True

    @property
    def segment_count(self) -> int:
        return max(1, math.ceil(self.duration / SEGMENT_SECONDS))


_probe_cache: Dict[int, Tuple[float, ProbeResult]] = {}
_probe_locks: Dict[int, asyncio.Lock] = {}
_segment_semaphore: Optional[asyncio.Semaphore] = None

# Toggled to False the first time ffprobe/ffmpeg fails to launch with
# FileNotFoundError — typically because the deploy is on a buildpack that
# didn't install ffmpeg. After that the HLS routes 415 immediately instead
# of error-spamming logs on every viewer request.
_ffmpeg_available: bool = True


def ffmpeg_available() -> bool:
    return _ffmpeg_available


def _semaphore() -> asyncio.Semaphore:
    # Lazy-init so we always bind to the running loop, not the import-time one.
    global _segment_semaphore
    if _segment_semaphore is None:
        _segment_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEGMENTS)
    return _segment_semaphore


def internal_stream_url(secure_hash: str, message_id: int) -> str:
    """The loopback URL our own byte-stream route serves. ffmpeg fetches this
    instead of going through the public load balancer."""
    return f"http://127.0.0.1:{Var.PORT}/{secure_hash}{message_id}"


async def probe(message_id: int, source_url: str) -> ProbeResult:
    """Return cached (or freshly probed) duration + codec metadata for the
    message. Concurrent callers for the same message share one ffprobe."""
    now = time.monotonic()
    cached = _probe_cache.get(message_id)
    if cached and (now - cached[0]) < PROBE_TTL:
        return cached[1]

    lock = _probe_locks.setdefault(message_id, asyncio.Lock())
    async with lock:
        # Re-check after acquiring the lock — another coroutine may have
        # already filled the cache while we were waiting.
        cached = _probe_cache.get(message_id)
        if cached and (time.monotonic() - cached[0]) < PROBE_TTL:
            return cached[1]

        result = await _run_ffprobe(source_url)
        _probe_cache[message_id] = (time.monotonic(), result)
        return result


async def _run_ffprobe(source_url: str) -> ProbeResult:
    global _ffmpeg_available
    args = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name",
        "-of", "json",
        "-timeout", "15000000",  # 15s connect/read timeout in microseconds
        source_url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        _ffmpeg_available = False
        logging.warning(
            "ffprobe not installed on the deploy image; HLS disabled. "
            "Install ffmpeg (Dockerfile already does this) and redeploy."
        )
        return ProbeResult(duration=0.0, video_codec=None, audio_codec=None)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logging.warning("ffprobe failed (%s): %s", proc.returncode, stderr.decode()[:300])
        return ProbeResult(duration=0.0, video_codec=None, audio_codec=None)

    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError:
        logging.warning("ffprobe returned non-JSON: %s", stdout[:200])
        return ProbeResult(duration=0.0, video_codec=None, audio_codec=None)

    duration = float(data.get("format", {}).get("duration", 0) or 0)
    video_codec = None
    audio_codec = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and video_codec is None:
            video_codec = stream.get("codec_name")
        elif stream.get("codec_type") == "audio" and audio_codec is None:
            audio_codec = stream.get("codec_name")

    return ProbeResult(duration=duration, video_codec=video_codec, audio_codec=audio_codec)


def build_playlist(probe_result: ProbeResult, segment_url_template: str) -> str:
    """Build a VOD HLS manifest. segment_url_template must contain '{n}'."""
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{SEGMENT_SECONDS}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    total = probe_result.duration
    for n in range(probe_result.segment_count):
        remaining = total - n * SEGMENT_SECONDS
        seg_dur = min(SEGMENT_SECONDS, remaining) if remaining > 0 else SEGMENT_SECONDS
        lines.append(f"#EXTINF:{seg_dur:.3f},")
        lines.append(segment_url_template.format(n=n))
    lines.append("#EXT-X-ENDLIST")
    lines.append("")
    return "\n".join(lines)


async def stream_segment(
    source_url: str,
    start_sec: float,
    duration_sec: float,
    audio_codec: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """Spawn ffmpeg to transmux a segment of the source into MPEG-TS, yielding
    bytes as they're produced. Caller is responsible for response framing.

    Audio is copied when the source codec is already browser-friendly (AAC/MP3)
    and transcoded to AAC otherwise. The codec hint should come from the
    cached probe so we don't re-probe per segment.
    """
    if audio_codec and audio_codec in BROWSER_AUDIO_OK:
        audio_args = ["-c:a", "copy"]
    else:
        # AC3/EAC3/DTS sources: encode to AAC for browser MSE compatibility.
        audio_args = ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]

    args = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "warning",
        "-ss", f"{start_sec:.3f}",  # fast seek to nearest keyframe ≤ start
        "-i", source_url,
        "-t", f"{duration_sec:.3f}",
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-c:v", "copy",
        *audio_args,
        # Preserve source timestamps. With -c copy ffmpeg can't drop pre-roll
        # frames after the keyframe seek; trying to renumber output PTS via
        # -output_ts_offset created gaps/overlaps between segments that hls.js
        # surfaced as replayed frames around boundaries. Keeping source PTS
        # means adjacent segments share a small keyframe-aligned overlap which
        # MSE deduplicates cleanly during stitching.
        "-copyts",
        "-muxdelay", "0",
        "-muxpreload", "0",
        "-mpegts_copyts", "1",
        "-avoid_negative_ts", "disabled",
        "-f", "mpegts",
        "pipe:1",
    ]

    global _ffmpeg_available
    sem = _semaphore()
    await sem.acquire()
    proc = None
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            _ffmpeg_available = False
            logging.warning("ffmpeg not installed on the deploy image; HLS disabled.")
            return
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
        await proc.wait()
        if proc.returncode != 0:
            err = (await proc.stderr.read()).decode(errors="replace")[:500] if proc.stderr else ""
            logging.warning("ffmpeg segment exit=%s: %s", proc.returncode, err)
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        sem.release()
