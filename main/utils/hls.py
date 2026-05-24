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
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional, Tuple

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

# Text-based subtitle codecs we can transcode to WebVTT. Image-based ones
# (hdmv_pgs_subtitle, dvd_subtitle, dvb_subtitle) would require OCR — skip.
SUB_TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}

# Map ISO 639-2 codes (what ffprobe gives) to ISO 639-1 (what HTML expects).
_LANG_3_TO_2 = {
    "eng": "en", "tam": "ta", "hin": "hi", "tel": "te", "mal": "ml",
    "kan": "kn", "ben": "bn", "mar": "mr", "guj": "gu", "pan": "pa",
    "urd": "ur", "spa": "es", "fre": "fr", "fra": "fr", "ger": "de",
    "deu": "de", "ita": "it", "jpn": "ja", "kor": "ko", "chi": "zh",
    "zho": "zh", "rus": "ru", "ara": "ar", "por": "pt", "nld": "nl",
    "dut": "nl", "pol": "pl", "tur": "tr", "swe": "sv", "nor": "no",
    "dan": "da", "fin": "fi",
}

_LANG_LABEL = {
    "en": "English", "ta": "Tamil", "hi": "Hindi", "te": "Telugu",
    "ml": "Malayalam", "kn": "Kannada", "bn": "Bengali", "mr": "Marathi",
    "gu": "Gujarati", "pa": "Punjabi", "ur": "Urdu", "es": "Spanish",
    "fr": "French", "de": "German", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "ru": "Russian", "ar": "Arabic",
    "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "tr": "Turkish",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
}


@dataclass(frozen=True)
class AudioTrack:
    """One audio stream available for selection."""
    index: int        # ffmpeg 0:a:index
    codec: str
    language: str     # ISO 639-1, may be ""
    label: str        # human-readable e.g. "Tamil", "English"


@dataclass(frozen=True)
class SubtitleTrack:
    """One text-based subtitle stream available for WebVTT extraction."""
    index: int          # ffmpeg -map 0:s:index
    codec: str
    language: str       # ISO 639-1, may be ""
    label: str          # human-readable, e.g. "English"


@dataclass(frozen=True)
class ProbeResult:
    duration: float
    video_codec: Optional[str]
    audio_codec: Optional[str]
    subtitles: Tuple[SubtitleTrack, ...] = ()
    audio_tracks: Tuple[AudioTrack, ...] = ()
    # Music metadata from format tags (populated for audio files)
    music_title: str = ""
    music_artist: str = ""
    music_album: str = ""
    music_track: Optional[int] = None

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
        # Only cache successful probes. A failed probe (duration=0, no codec)
        # is usually transient (server starting up, loopback contention) —
        # caching it for an hour would lock the item out of codec info.
        if result.duration > 0 or result.video_codec:
            now = time.monotonic()
            _probe_cache[message_id] = (now, result)
            # Purge expired cache entries and any orphaned locks (locks whose
            # cache entry never succeeded and therefore was never inserted).
            expired = [mid for mid, (ts, _) in _probe_cache.items()
                       if now - ts > PROBE_TTL]
            for mid in expired:
                _probe_cache.pop(mid, None)
            # Remove locks for IDs absent from the cache (failed probes that
            # never wrote a cache entry accumulate locks indefinitely otherwise).
            stale_locks = [mid for mid in _probe_locks if mid not in _probe_cache]
            for mid in stale_locks:
                _probe_locks.pop(mid, None)
        return result


def _normalise_lang(raw: Optional[str]) -> str:
    if not raw:
        return ""
    raw = raw.lower().strip()
    if len(raw) == 2:
        return raw
    return _LANG_3_TO_2.get(raw, "")


# Patterns that indicate an audio stream title is a release-group watermark
# or codec metadata rather than a human-readable language name. When matched,
# the title is discarded and we fall through to language code → Track N.
_JUNK_TITLE_RE = re.compile(
    r"www\."            # URL fragment
    r"|\.\w{2,6}\s*[-–]"  # domain extension followed by separator (e.g. .nexus -)
    r"|\[\s*\w"         # bracket-wrapped codec/bitrate info, e.g. [AAC 2.0 - 64Kbps]
    r"|\d+\s*[Kk]bps"  # bare bitrate string
    r"|^\s*und\s*$"     # "und" = undefined language code
    r"|@\w+"            # Telegram/social handle promo (e.g. "Join -> @Filmbox_Studios")
    r"|->\s*@",         # arrow-then-handle variant
    re.IGNORECASE,
)


def _label_for(lang: str, title: Optional[str], index: int) -> str:
    # Reject titles that look like release watermarks or codec metadata.
    if title and not _JUNK_TITLE_RE.search(title):
        return title.strip()
    if lang and lang in _LANG_LABEL:
        return _LANG_LABEL[lang]
    if lang and lang not in ("und", "---"):
        return lang.upper()
    return f"Track {index + 1}"


async def _run_ffprobe(source_url: str) -> ProbeResult:
    global _ffmpeg_available
    args = [
        "ffprobe",
        "-v", "error",
        # Match the reconnect handling used by grab_thumbnail — without these
        # flags, our 206-with-only-the-skeleton-head response would cause
        # ffprobe to hit EOF mid-parse on files that need more than 2 MB.
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        # Also pull tags so we know the subtitle's language + title,
        # and format_tags for music metadata (artist, album, track).
        "-show_entries",
        "format=duration:format_tags=title,artist,album_artist,album,track:stream=index,codec_type,codec_name:stream_tags=language,title",
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

    fmt = data.get("format", {}) or {}
    duration = float(fmt.get("duration", 0) or 0)
    fmt_tags = fmt.get("tags", {}) or {}

    def _tag(key):
        # Tags may be uppercase or lowercase depending on the container
        return (fmt_tags.get(key) or fmt_tags.get(key.upper()) or "").strip()

    music_artist = _tag("artist") or _tag("album_artist")
    music_album = _tag("album")
    music_title = _tag("title")
    track_raw = _tag("track")  # may be "3" or "3/12"
    music_track = None
    if track_raw:
        try:
            music_track = int(track_raw.split("/")[0])
        except ValueError:
            pass

    video_codec = None
    audio_codec = None
    subtitle_tracks: List[SubtitleTrack] = []
    audio_tracks_list: List[AudioTrack] = []
    sub_index = 0   # the Nth subtitle stream (for ffmpeg's -map 0:s:N)
    audio_index = 0  # the Nth audio stream (for ffmpeg's -map 0:a:N)

    for stream in data.get("streams", []):
        ctype = stream.get("codec_type")
        cname = stream.get("codec_name")
        if ctype == "video" and video_codec is None:
            video_codec = cname
        elif ctype == "audio":
            tags = stream.get("tags", {}) or {}
            lang = _normalise_lang(tags.get("language"))
            title = (tags.get("title") or "").strip() or None
            if audio_codec is None:
                audio_codec = cname
            audio_tracks_list.append(AudioTrack(
                index=audio_index,
                codec=cname or "",
                language=lang,
                label=_label_for(lang, title, audio_index),
            ))
            audio_index += 1
        elif ctype == "subtitle":
            if cname in SUB_TEXT_CODECS:
                tags = stream.get("tags", {}) or {}
                lang = _normalise_lang(tags.get("language"))
                title = (tags.get("title") or "").strip() or None
                subtitle_tracks.append(SubtitleTrack(
                    index=sub_index,
                    codec=cname or "",
                    language=lang,
                    label=_label_for(lang, title, sub_index),
                ))
            sub_index += 1

    return ProbeResult(
        duration=duration,
        video_codec=video_codec,
        audio_codec=audio_codec,
        subtitles=tuple(subtitle_tracks),
        audio_tracks=tuple(audio_tracks_list),
        music_title=music_title,
        music_artist=music_artist,
        music_album=music_album,
        music_track=music_track,
    )


# Cap parallel ffmpeg thumbnail grabs separately from segment generation —
# a freshly-loaded hub can fire 24 thumb requests at once, and each grab
# launches its own ffmpeg.
MAX_CONCURRENT_THUMBS = int(os.environ.get("THUMB_MAX_CONCURRENT", "3"))
_thumb_semaphore: Optional[asyncio.Semaphore] = None


def _thumb_sem() -> asyncio.Semaphore:
    global _thumb_semaphore
    if _thumb_semaphore is None:
        _thumb_semaphore = asyncio.Semaphore(MAX_CONCURRENT_THUMBS)
    return _thumb_semaphore


async def grab_thumbnail(source_url: str, duration: float = 0.0, seek: float = 1.0) -> Optional[bytes]:
    """Grab a single JPEG frame from a video / APIC art from audio.

    Used by /thumb/* when Telegram's own thumbnail is missing. We input-seek
    (``-ss`` before ``-i``) for video so ffmpeg only reads the part it needs.
    For audio APIC extraction pass seek=0.0 — the APIC lives in the ID3 header
    at byte 0; an input-seek sends a Range request that skips it entirely.
    """
    # Default: seek to 1 second for video (avoids black first frame, reads
    # minimal data via MOOV index). Callers pass seek=0.0 for audio APIC.

    args = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        # HTTP-level read timeout (microseconds). Cold-cache tail warmup
        # fetches 512 KB from Telegram which takes ~3-10s; give enough room.
        "-timeout", "45000000",         # 45s per HTTP request
        # Our stream route returns a 206 with only the 2 MB skeleton head in
        # response to ffmpeg's initial no-Range GET (see stream_routes.py).
        # If the MP4 demuxer reads sequentially past that 2 MB — large moov
        # at start, or many small boxes before mdat — libavformat would
        # otherwise treat the end of the response body as terminal EOF and
        # abort with AVERROR_EOF / "error reading header" (exit 187). These
        # flags make ffmpeg reissue a Range request from the current offset
        # instead, which our route serves from cache (head/tail) or from
        # Telegram. reconnect_at_eof is the load-bearing one.
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        # Tolerate broken MP4 indices (STCO/STSC mismatches).
        "-fflags", "+ignidx+igndts+discardcorrupt",
        "-err_detect", "ignore_err",
        # Input-side seek (-ss before -i): ffmpeg uses the MOOV index to jump
        # directly to the seek position via a Range request — reads ~100 KB
        # total. Output-side seek reads all data from 0 to seek point which
        # can be many MB for long videos, causing loopback timeouts.
        "-ss", f"{seek:.2f}",
        "-i", source_url,
        "-frames:v", "1",
        "-q:v", "5",
        "-vf", "scale=320:-2",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-",
    ]
    async with _thumb_sem():
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            logging.warning("thumbnail grab timed out for %s", source_url)
            return None
        except Exception:
            logging.exception("thumbnail grab failed for %s", source_url)
            return None
    if proc.returncode != 0 or not stdout:
        logging.warning(
            "ffmpeg thumb grab failed exit=%s url=%s stderr=%s",
            proc.returncode, source_url,
            (stderr or b"")[:300].decode("utf-8", "replace"),
        )
        return None
    return stdout


async def extract_subtitle_vtt(source_url: str, track_index: int) -> Optional[bytes]:
    """Run ffmpeg to extract the Nth subtitle stream and convert it to WebVTT
    bytes. Returns None on failure."""
    global _ffmpeg_available
    args = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-i", source_url,
        "-map", f"0:s:{track_index}",
        "-c:s", "webvtt",
        "-f", "webvtt",
        "pipe:1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        _ffmpeg_available = False
        return None
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logging.warning(
            "ffmpeg subtitle extract failed (track=%d, code=%s): %s",
            track_index, proc.returncode, stderr.decode(errors="replace")[:400],
        )
        return None
    return stdout


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
        # Regenerate PTS starting at 0 for each subprocess so -output_ts_offset
        # below gives the logical position rather than (keyframe_pts + offset).
        "-fflags", "+genpts",
        "-ss", f"{start_sec:.3f}",  # fast seek to nearest keyframe ≤ start
        "-i", source_url,
        "-t", f"{duration_sec:.3f}",
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-c:v", "copy",
        *audio_args,
        # Each segment's output PTS starts at its logical position
        # (start_sec). Combined with +genpts above, this is the closest
        # we can get to continuous timestamps across segments without
        # re-encoding. The -copyts variant caused playback to stop ~11s
        # in because adjacent segments had divergent PTS that MSE refused
        # past the keyframe boundary of segment 1.
        "-output_ts_offset", f"{start_sec:.3f}",
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
