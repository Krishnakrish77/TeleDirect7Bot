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
import time
from typing import Optional

from main.utils import media_index
from main.vars import Var


# Codecs that essentially every desktop browser decodes natively.
# ``vp9`` is also widely supported, but only inside webm — when it
# shows up inside MKV, the HLS-transmux remux to MP4 will fail
# because MP4 doesn't take VP9.
_BROWSER_FRIENDLY_VIDEO_CODECS = {"h264", "avc1"}

# Pixel formats that are 10-bit or higher. Browsers can decode 8-bit
# H.264 and 8-bit HEVC (on Safari) but not 10-bit anything in MSE.
_HIGH_BIT_DEPTH_HINTS = ("10le", "10be", "p010", "p012", "12le")


def is_browser_playable(video_codec: str, pix_fmt: str) -> bool:
    """Return True only when we're CONFIDENT the browser can decode.

    A blank ``video_codec`` (probe never ran, or failed) returns
    True — we don't know enough to gate the player. The caller
    should treat True as "let the browser try" and reserve the
    early-overlay path for known-bad combos.
    """
    if not video_codec:
        return True
    vc = video_codec.lower()
    pf = (pix_fmt or "").lower()
    if vc not in _BROWSER_FRIENDLY_VIDEO_CODECS:
        return False
    for hint in _HIGH_BIT_DEPTH_HINTS:
        if hint in pf:
            return False
    return True


def needs_probe(item) -> bool:
    """True if the HubItem hasn't been probed yet.

    ``probed_at == 0`` means we've never tried. We don't probe
    items with no streaming hash (defensive — shouldn't happen).
    """
    if not getattr(item, "secure_hash", "") or not getattr(item, "message_id", 0):
        return False
    return float(getattr(item, "probed_at", 0) or 0) <= 0


async def probe_item(item, *, timeout: float = 30.0) -> bool:
    """Run ffprobe against the BIN entry's stream URL. Returns True
    if a video stream was identified; False on timeout / failure.

    Always sets ``probed_at`` so a transient failure doesn't cause
    the next sweep to keep retrying.
    """
    stream_url = f"{Var.URL}{item.secure_hash}{item.message_id}"
    cmd = [
        "ffprobe",
        "-v", "error",
        "-hide_banner",
        # Limit how much ffprobe pulls. ~5MB is more than enough to
        # read MOOV / EBML / MPEG-PS headers; keeps probe latency
        # bounded for huge files.
        "-probesize", "5M",
        "-analyzeduration", "5000000",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,pix_fmt,profile",
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
        return False
    except FileNotFoundError:
        logging.error("codec_probe: ffprobe binary not found on PATH")
        return False
    except Exception:
        logging.exception("codec_probe: ffprobe failed for bin:%d", item.message_id)
        item.probed_at = time.time()
        await media_index.persist_now()
        return False

    if proc.returncode != 0:
        logging.info(
            "codec_probe: ffprobe rc=%d for bin:%d (stderr: %s)",
            proc.returncode, item.message_id,
            (stderr or b"").decode(errors="replace")[:200],
        )
        item.probed_at = time.time()
        await media_index.persist_now()
        return False

    try:
        payload = json.loads(stdout.decode("utf-8") or "{}")
    except Exception:
        logging.warning("codec_probe: JSON parse failed for bin:%d", item.message_id)
        item.probed_at = time.time()
        await media_index.persist_now()
        return False

    streams = payload.get("streams") or []
    if not streams:
        item.probed_at = time.time()
        await media_index.persist_now()
        return False
    s = streams[0]
    item.video_codec = (s.get("codec_name") or "").lower()
    item.pix_fmt = (s.get("pix_fmt") or "").lower()
    item.probed_at = time.time()
    media_index.persist_now()
    logging.info(
        "codec_probe: bin:%d → codec=%s pix_fmt=%s",
        item.message_id, item.video_codec, item.pix_fmt,
    )
    return True


# In-memory throttle so a bulk pass never runs more than N ffprobes
# in parallel — each probe holds an aiohttp range stream open and a
# subprocess, both finite.
_BULK_CONCURRENCY = 3


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

    sem = asyncio.Semaphore(_BULK_CONCURRENCY)

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
    try:
        await probe_item(item)
    except Exception:
        logging.exception("codec_probe: background probe failed for bin:%d",
                          item.message_id)


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
