"""ffprobe a single BIN entry to learn its video codec + pixel format.

The catalogue can answer "will this play in a browser?" only when we
know what's inside the container. ffprobe reading the file's first
few MB gives us ``codec_name`` (h264/hevc/av1/...) and ``pix_fmt``
(yuv420p / yuv420p10le / ...). With those two fields stored on the
HubItem, the watch page can render the VLC-fallback overlay
immediately for HEVC / 10-bit / etc., instead of letting the
browser hit a chunk-demuxer error mid-playback.

Probing is done by pointing ffprobe at our OWN byte-range stream
URL — same endpoint the watch page would have hit. ffprobe pulls
only the first ~5MB via Range requests, more than enough to read
container headers. No new client wiring, no Telegram code path
specific to probing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional


# Strip common distribution-site watermarks from ID3 title/artist tags.
# Pattern: trailing " - SiteName" or " - SiteName.tld" or " - @Handle".
_MUSIC_WATERMARK_RE = re.compile(
    r'\s*[-–]\s*(?:'
    r'@\w+'                              # @Handle
    r'|www\.\S+'                         # www.site.com
    r'|\w+\.(?:dev|com|net|org|io|in)'  # site.dev / site.com
    r'|Mass(?:Tamilan)?'                 # MassTamilan (common Tamil music site)
    r')\s*$',
    re.IGNORECASE,
)


def _clean_music_tag(text: str) -> str:
    """Strip distribution-site watermarks from an ID3 title or artist field."""
    if not text:
        return text
    cleaned = _MUSIC_WATERMARK_RE.sub("", text).strip()
    return cleaned or text  # never return empty if original was non-empty

from main.utils import media_index
from main.vars import Var


# Codecs that essentially every desktop browser decodes natively.
# ``vp9`` is also widely supported, but only inside webm — when it
# shows up inside MKV, the HLS-transmux remux to MP4 will fail
# because MP4 doesn't take VP9.
_BROWSER_FRIENDLY_VIDEO_CODECS = {"h264", "avc1"}

# Pixel formats that are 10-bit or higher. Browsers can decode 8-bit
# H.264 and 8-bit HEVC but not 10-bit anything in MSE.
_HIGH_BIT_DEPTH_HINTS = ("10le", "10be", "p010", "p012", "12le")


def is_browser_playable(video_codec: str, pix_fmt: str) -> bool:
    """Return True only when we're CONFIDENT the browser can decode.

    A blank ``video_codec`` (probe never ran, or failed) returns
    True — we don't know enough to gate the player. The caller
    should treat True as "let the browser try" and reserve the
    early-overlay path for known-bad combos.

    8-bit HEVC is playable on Safari (natively) and Chrome/Edge on
    most modern hardware via HLS+MSE. We allow it through here and
    let the JS watchForUndecodableCodec() show the overlay after
    4 s if frames genuinely never decode — better than blocking
    immediately and showing a false "can't play" message.
    """
    if not video_codec:
        return True
    vc = video_codec.lower()
    pf = (pix_fmt or "").lower()
    # 10-bit always blocks — no browser MSE path handles it.
    for hint in _HIGH_BIT_DEPTH_HINTS:
        if hint in pf:
            return False
    # h264 and 8-bit hevc are playable.
    if vc in _BROWSER_FRIENDLY_VIDEO_CODECS:
        return True
    if vc == "hevc":
        return True
    return False


def needs_probe(item) -> bool:
    """True if the HubItem needs a codec probe.

    Probes items that:
    - have never been tried (probed_at==0)
    - were probed but have no duration (document uploads)
    - are audio but missing artist/album (probed before music tag extraction
      was added to the show_entries command)
    """
    if not getattr(item, "secure_hash", "") or not getattr(item, "message_id", 0):
        return False
    never_probed = float(getattr(item, "probed_at", 0) or 0) <= 0
    missing_duration = int(getattr(item, "duration", 0) or 0) <= 0
    # Audio items probed before music tags were added need a re-probe to fill
    # in artist/album/track. Trigger when artist is missing — once artist is
    # set from the probe, this condition won't fire again (avoiding loops).
    # We don't also require album_title to be missing: an item can have
    # artist (from Telegram's Audio.performer) but still need probe for album.
    is_audio = getattr(item, "media_kind", "") == "audio"
    missing_artist = is_audio and not getattr(item, "artist", "")
    return never_probed or missing_duration or missing_artist


async def probe_item(item, *, timeout: float = 30.0) -> bool:
    """Run ffprobe against the BIN entry's stream URL. Returns True
    if a video stream was identified; False on timeout / failure.

    Always sets ``probed_at`` so a transient failure doesn't cause
    the next sweep to keep retrying.
    """
    from main.utils.hls import internal_stream_url
    import aiohttp as _aiohttp
    stream_url = internal_stream_url(item.secure_hash, item.message_id)

    # Warm the skeleton cache tail before ffprobe. A full-file GET bypasses
    # the skeleton cache and streams from Telegram — the connection drops
    # before ffprobe can find the MOOV atom. A tail Range request fetches
    # 512 KB into cache so ffprobe's subsequent MOOV seek is served locally.
    if item.file_size and item.file_size > 1024:
        try:
            tail_start = max(0, item.file_size - 1024)
            async with _aiohttp.ClientSession() as _s:
                async with _s.get(
                    stream_url,
                    headers={"Range": f"bytes={tail_start}-"},
                    timeout=_aiohttp.ClientTimeout(total=45),
                ) as _r:
                    await _r.read()
        except Exception:
            pass  # best-effort; ffprobe will still try

    cmd = [
        "ffprobe",
        "-v", "error",
        "-hide_banner",
        # Allow 45s per HTTP request — skeleton cache warmup can take ~5s.
        "-timeout", "45000000",
        # Our stream route serves only the 2 MB skeleton head for the initial
        # no-Range GET. Without -reconnect_at_eof, ffprobe would treat the
        # end of that response as terminal EOF and abort with AVERROR_EOF
        # on any file whose moov sits past 2 MB. These flags let it reopen
        # the connection with a Range request and continue parsing.
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        # Tolerate broken MP4 index atoms.
        "-fflags", "+ignidx+igndts",
        "-err_detect", "ignore_err",
        "-probesize", "5M",
        "-analyzeduration", "5000000",
        "-select_streams", "v:0",
        # format_tags includes all available music metadata for audio files.
        # title/date/genre also used for video (embedded title fallback, year, tags).
        "-show_entries", "stream=codec_name,pix_fmt,profile,width,height:format=duration:format_tags=title,artist,album_artist,album,track,date,year,genre,composer",
        "-of", "json",
        stream_url,
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        logging.warning("codec_probe: ffprobe timed out for bin:%d", item.message_id)
        item.probed_at = time.time()
        await media_index.persist_now()
        await media_index._store_upsert(item)
        return False
    except FileNotFoundError:
        logging.error("codec_probe: ffprobe binary not found on PATH")
        return False
    except Exception:
        logging.exception("codec_probe: ffprobe failed for bin:%d", item.message_id)
        item.probed_at = time.time()
        await media_index.persist_now()
        await media_index._store_upsert(item)
        return False

    if proc.returncode != 0:
        logging.info(
            "codec_probe: ffprobe rc=%d for bin:%d (stderr: %s)",
            proc.returncode, item.message_id,
            (stderr or b"").decode(errors="replace")[:200],
        )
        item.probed_at = time.time()
        await media_index.persist_now()
        await media_index._store_upsert(item)
        return False

    try:
        payload = json.loads(stdout.decode("utf-8") or "{}")
    except Exception:
        logging.warning("codec_probe: JSON parse failed for bin:%d", item.message_id)
        item.probed_at = time.time()
        await media_index.persist_now()
        await media_index._store_upsert(item)
        return False

    # Extract music tags from format section BEFORE the streams check.
    # Audio-only files have no video stream so streams would be empty, but
    # format.tags (artist/album/track/title) are always present regardless.
    fmt_tags_all = (payload.get("format") or {}).get("tags") or {}
    def _ftag(key):
        return (fmt_tags_all.get(key) or fmt_tags_all.get(key.upper()) or "").strip()
    probe_artist = _ftag("artist") or _ftag("album_artist")
    probe_album = _ftag("album")
    probe_title_tag = _ftag("title")
    probe_track_raw = _ftag("track")
    # Year: try "date" first (e.g. "2023" or "2023-05-12"), then "year"
    probe_date = _ftag("date") or _ftag("year")
    probe_year: Optional[int] = None
    if probe_date:
        try:
            y = int(probe_date[:4])
            # Reject placeholder/garbage years (0000, 1900, etc.)
            if 1920 <= y <= 2100:
                probe_year = y
        except (ValueError, IndexError):
            pass
    # Genre: comma/slash-separated in ID3, take the first one.
    # Reject ID3v1 numeric codes (0-191) — ffprobe sometimes returns "17"
    # instead of the human-readable name (e.g. "Rock").
    probe_genre_raw = _ftag("genre")
    if probe_genre_raw:
        raw = probe_genre_raw.split("/")[0].split(",")[0].strip()
        probe_genre = "" if raw.isdigit() else raw
    else:
        probe_genre = ""
    probe_composer = _ftag("composer")
    probe_track: Optional[int] = None
    if probe_track_raw:
        try:
            probe_track = int(probe_track_raw.split("/")[0])
        except ValueError:
            pass

    is_audio = getattr(item, "media_kind", "") == "audio"

    if probe_artist:
        clean_artist = _clean_music_tag(probe_artist)
        if not item.artist:
            item.artist = clean_artist
        elif item.artist != clean_artist and _MUSIC_WATERMARK_RE.search(item.artist):
            # Fix already-stored watermarked artist on re-probe
            item.artist = clean_artist
    from main.utils.series import slugify as _slugify
    if probe_album and not item.album_title:
        item.album_title = probe_album
    # Always recompute album_key from album_title.
    if item.album_title:
        correct_key = _slugify(item.album_title)
        if item.album_key != correct_key:
            item.album_key = correct_key
    if probe_track is not None and item.track_number is None:
        item.track_number = probe_track
    # For audio: use embedded title as track title (cleaned of watermarks).
    if probe_title_tag and is_audio:
        clean_title = _clean_music_tag(probe_title_tag)
        if not item.title:
            item.title = clean_title
        elif item.title != clean_title and _MUSIC_WATERMARK_RE.search(item.title):
            # Fix already-stored watermarked title on re-probe
            item.title = clean_title
    # Year from tags: fills in audio year (Telegram doesn't extract it).
    if probe_year and not item.year:
        item.year = probe_year
    # Genre from tags: add to item tags if not already there.
    if probe_genre and is_audio:
        existing_tags = list(item.tags or [])
        genre_lower = probe_genre.lower()
        if genre_lower not in [t.lower() for t in existing_tags]:
            item.tags = existing_tags + [probe_genre]
    # Composer: store in description for now if audio and description empty.
    if probe_composer and is_audio and not item.description:
        item.description = f"Composer: {probe_composer}"

    streams = payload.get("streams") or []
    if not streams:
        # No video stream — audio-only file. Music tags already saved above.
        item.probed_at = time.time()
        await media_index.persist_now()
        await media_index._store_upsert(item)
        return False
    s = streams[0]
    item.video_codec = (s.get("codec_name") or "").lower()
    item.pix_fmt = (s.get("pix_fmt") or "").lower()
    # For audio files, a video stream means an APIC/cover-art attachment.
    # Mark has_thumb so the album page picks this track for the cover art.
    if getattr(item, "media_kind", "") == "audio" and item.video_codec:
        item.has_thumb = True
    # Fill duration from ffprobe if Telegram didn't extract it (e.g. document uploads)
    if not item.duration:
        try:
            probed_duration = float((payload.get("format") or {}).get("duration") or 0)
            if probed_duration > 0:
                item.duration = int(probed_duration)
        except (TypeError, ValueError):
            pass
    # Use the embedded title tag as a filename fallback for video-type
    # uploads that Telegram strips the original name from.
    if not item.file_name:
        fmt_tags = (payload.get("format") or {}).get("tags") or {}
        embedded_title = (fmt_tags.get("title") or fmt_tags.get("Title") or "").strip()
        if embedded_title:
            item.file_name = embedded_title
    # Derive quality bucket from the actual encoded height when the
    # filename didn't reveal one. ffprobe's truth wins over the
    # filename-parsed bucket — release groups frequently mislabel
    # ("X.S01E01.1080p.mkv" that's actually 720p at runtime).
    try:
        height = int(s.get("height") or 0)
    except (TypeError, ValueError):
        height = 0
    if height > 0:
        probed_quality = _quality_from_height(height)
        if probed_quality:
            item.quality = probed_quality
    item.probed_at = time.time()
    await media_index.persist_now()
    await media_index._store_upsert(item)
    logging.info(
        "codec_probe: bin:%d → codec=%s pix_fmt=%s height=%s quality=%s",
        item.message_id, item.video_codec, item.pix_fmt,
        height or "?", item.quality or "?",
    )
    return True


def _quality_from_height(height: int) -> str:
    """Map encoded-video height to the same buckets the rest of the
    catalogue uses. Thresholds are deliberately generous to absorb
    near-canonical resolutions (e.g. 1088 for HEVC alignment, 1078
    for slightly-cropped 1080p sources)."""
    if height >= 1800:
        return "4K"
    if height >= 950:
        return "1080p"
    if height >= 650:
        return "720p"
    if height >= 350:
        return "480p"
    return ""


# In-memory throttle so neither the bulk admin pass NOR per-upload
# auto-probes ever run more than N ffprobes in parallel — each probe
# holds an aiohttp range stream open and a subprocess, both finite.
# Env-configurable: ``CODEC_PROBE_CONCURRENCY`` (default 3).
def _probe_conc_from_env() -> int:
    import os as _os
    raw = (_os.environ.get("CODEC_PROBE_CONCURRENCY", "") or "").strip()
    if not raw:
        return 3
    try:
        n = int(raw)
    except ValueError:
        return 3
    return max(1, min(n, 16))


_BULK_CONCURRENCY = _probe_conc_from_env()
# Shared semaphore across bulk + per-upload paths so the cap applies
# whichever channel kicks off a probe. Lazy-init for the same
# event-loop reason as in indexer.py.
_probe_sem: Optional[asyncio.Semaphore] = None


def _semaphore() -> asyncio.Semaphore:
    global _probe_sem
    if _probe_sem is None:
        _probe_sem = asyncio.Semaphore(_BULK_CONCURRENCY)
    return _probe_sem


# Module-level state for an admin-triggered batch probe.
probe_state: dict = {
    "running": False,
    "done": 0,
    "total": 0,
    "found_incompatible": 0,
    "started_at": 0.0,
    "finished_at": 0.0,
}


def state() -> dict:
    return dict(probe_state)


async def probe_all_missing() -> dict:
    """Probe every catalogue entry that hasn't been probed yet.

    Bounded concurrency keeps server CPU and bandwidth predictable.
    Idempotent: a second call after completion is a no-op because
    ``probed_at`` is set on every attempt (success or failure).
    """
    if probe_state["running"]:
        return {"already_running": True}

    targets = [
        it for it in media_index._items.values()
        if needs_probe(it)
    ]

    probe_state.update(
        running=True,
        done=0,
        total=len(targets),
        found_incompatible=0,
        started_at=time.time(),
        finished_at=0.0,
    )

    sem = _semaphore()

    async def _one(it):
        async with sem:
            ok = await probe_item(it)
            probe_state["done"] += 1
            if ok and not is_browser_playable(it.video_codec, it.pix_fmt):
                probe_state["found_incompatible"] += 1

    try:
        await asyncio.gather(*[_one(it) for it in targets])
    finally:
        probe_state["running"] = False
        probe_state["finished_at"] = time.time()

    return {
        "total": probe_state["total"],
        "done": probe_state["done"],
        "found_incompatible": probe_state["found_incompatible"],
    }


def schedule_probe(message_id: int) -> None:
    """Fire-and-forget per-message probe. Called from the indexer
    after a new BIN message is added to the catalogue.
    """
    item = media_index.get_item(message_id)
    if item is None or not needs_probe(item):
        return
    try:
        asyncio.create_task(_probe_quietly(item))
    except RuntimeError:
        # No running loop (called from sync context outside the
        # web/bot loop); skip.
        pass


async def _probe_quietly(item) -> None:
    """Per-upload background probe. Shares the semaphore with the bulk
    pass so simultaneous schedules never run more than
    ``_BULK_CONCURRENCY`` ffprobes at once.

    Brand-new uploads frequently fail the first attempt because the
    streaming server's skeleton cache for that file is stone-cold and
    Telegram's metadata propagation can lag a few seconds. Retry once
    after a short delay if the first attempt didn't produce a duration.
    """
    try:
        async with _semaphore():
            await probe_item(item)
        if (not getattr(item, "duration", 0)
                and not getattr(item, "video_codec", None)):
            # First attempt produced nothing usable. Wait for the file
            # to settle, then retry once. This catches cold-cache races
            # without making the bulk Probe sweep mandatory.
            item.probed_at = 0.0  # let probe_item run again
            await asyncio.sleep(15)
            async with _semaphore():
                await probe_item(item)
    except Exception:
        logging.exception("codec_probe: background probe failed for bin:%d",
                          item.message_id)

    # For audio files, also pre-warm the thumbnail cache after the probe
    # so the first album/music page load shows art immediately without
    # waiting for the /thumb/ route to run ffmpeg cold.
    if getattr(item, "media_kind", "") == "audio":
        try:
            from main.utils import hls, thumb_cache
            source_url = hls.internal_stream_url(item.secure_hash, item.message_id)
            async def _fetch_thumb():
                # seek=0.0: APIC lives in ID3 header at byte 0; seeking past
                # it via a Range request causes ffmpeg to miss it entirely.
                return await hls.grab_thumbnail(source_url, duration=0, seek=0.0)
            await thumb_cache.cached_or_fetch(item.message_id, _fetch_thumb)
        except Exception:
            logging.debug("codec_probe: thumb pre-warm failed for bin:%d",
                          item.message_id, exc_info=True)


_BLOCKING_VIDEO_CODECS: Optional[set] = None


def known_unplayable(item) -> bool:
    """Convenience wrapper used by the watch page: True iff the probe
    has run AND found a codec the browser can't decode. False when
    the probe hasn't run (don't gate the player yet)."""
    if not item or not getattr(item, "probed_at", 0):
        return False
    return not is_browser_playable(
        getattr(item, "video_codec", "") or "",
        getattr(item, "pix_fmt", "") or "",
    )
