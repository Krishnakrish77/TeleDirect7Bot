"""Cross-episode intro detection via chromaprint audio fingerprinting.

Port of the approach proven by Jellyfin's Intro Skipper plugin:

  1. Fingerprint the first 25% of every episode in a series (capped at
     10 minutes) with ``fpcalc`` — raw uint32 points, 1 point = 0.128 s
     of audio.
  2. For every episode pair, find fingerprint-point matches (±1 tolerance,
     mirroring Jellyfin's ``invertedIndexShift``) and histogram the implied
     time-shifts. The intro theme repeats exactly across episodes, so the
     dominant shift aligns the two intros.
  3. Walk the aligned pair; the longest contiguous matching range is the
     intro. Snap its end to the first silence ≥ 0.5 s (where the theme
     stops and dialogue begins).
  4. Validate: 15–150 s long, starts within the fingerprinted window.
     Anything else → no intro recorded (honest absence).

Silence is never used to *find* intros (theme music is loud); it only
refines where the theme ends. Manual admin edits win forever via
``intro_source == "manual"``.

Per-episode fingerprints are cached in the catalogue meta store (Mongo or
JSON), so re-sweeps only fingerprint newly indexed episodes.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import struct
import subprocess
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from main.utils import media_index
from main.utils.hls import internal_stream_url

log = logging.getLogger(__name__)

# 1 fingerprint point represents this many seconds of audio (chromaprint spec).
POINT_SECONDS = 0.128
# ±point tolerance when matching — chromaprint points are quantized gradients
# that can differ by ±1 even for identical audio (different encodes).
POINT_TOLERANCE = 1
# Points "match" when ≤18 of 32 bits differ. Calibrated empirically on two
# series (Silo, One Piece): 16 and 18 give stable runs with matching starts;
# 20 over-extends backwards into cold-open music; ≤14 loses the intro.
# Random-pair baseline is 16/32 — the *contiguous run* (not the individual
# point) is what separates intro from noise.
MATCH_HAMMING_BITS = 18
MIN_INTRO_POINTS = int(15 / POINT_SECONDS)   # 15 s
MAX_INTRO_POINTS = int(150 / POINT_SECONDS)  # 150 s
# Fingerprint this fraction of each episode (Jellyfin: first 25% or 10 min).
# INTRO_FP_WINDOW_CAP caps the per-episode audio window — lower it (e.g. 120)
# on memory/CPU-constrained deploys (Koyeb free) where streaming 10 min of
# audio per episode through ffmpeg starves the box and trips the timeout.
_WINDOW_CAP_SECONDS = float(os.environ.get("INTRO_FP_WINDOW_CAP", "600") or 600)
# Hard cap on the ffmpeg|fpcalc pipeline per episode. Must comfortably exceed
# the audio window: streaming 600 s of audio over a slow Telegram DC can take
# longer than the old fixed 180 s on a throttled CPU.
_FP_TIMEOUT_SECONDS = float(os.environ.get("INTRO_FP_TIMEOUT", "600") or 600)
# fpcalc needs a real file; stream via this command into stdout. -ac 2 -ar
# 44100 matches chromaprint's expected input.
_FPCALC = "fpcalc"

intro_state: dict = {
    "running": False,
    "done": 0,
    "total": 0,
    "series_done": 0,
    "intros_found": 0,
    "started_at": 0.0,
    "finished_at": 0.0,
    "error": "",
    # Per-series run (edit-modal "Auto-detect"): which series is being
    # fingerprinted and how many of its episodes are done, so the SPA
    # can show progress via /admin/status while the POST-based flow
    # runs in the background.
    "series_running": False,
    "series_key": "",
    "series_done_count": 0,
    "series_total": 0,
    "series_error": "",
}

# Per-series detect runs one at a time; waiters poll intro_state.
_series_lock = asyncio.Lock()


def state() -> dict:
    return dict(intro_state)


# ---------------------------------------------------------------- fingerprint

def _fingerprint_cache_key(message_id: int) -> str:
    return f"intro_fp:{message_id}"


def _decode_fingerprint(b64: str) -> List[int]:
    """fpcalc emits URL-safe base64 (``-``/``_``) without padding."""
    s = b64.strip().rstrip("=")
    if not s or not re.fullmatch(r"[A-Za-z0-9_-]+", s):
        raise ValueError("malformed fingerprint")
    raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    n = len(raw) // 4
    if n == 0:
        raise ValueError("empty fingerprint")
    return list(struct.unpack(f"<{n}I", raw[: n * 4]))


def _fingerprint_window_seconds(item) -> float:
    """How much of the episode head to fingerprint."""
    duration = int(item.duration or 0)
    if duration <= 0:
        return _WINDOW_CAP_SECONDS
    return min(max(duration * 0.25, 30.0), _WINDOW_CAP_SECONDS)


# ---------------------------------------------------------------- matching

def _hamming(x: int, y: int) -> int:
    """Bit distance between two chromaprint points (32-bit words)."""
    return bin(x ^ y).count("1")


def _longest_contiguous_match(a: List[int], b: List[int]) -> Tuple[int, int, int]:
    """Longest run where Hamming(a[i], b[i+shift]) ≤ threshold, over ALL
    shifts. Returns (run_points, a_start_points, shift).

    Design notes (empirically calibrated on real library episodes — Silo,
    One Piece):
    - Chromaprint points drift a few bits per point across encodes; exact
      or ±1-integer matching finds nothing (0 matches at the true shift).
    - Individual point thresholds can't separate signal from noise
      (matched ≈15/32 vs random ≈16/32). The *contiguous run* is the
      separator: at the true shift, sub-threshold points line up for
      tens of seconds; elsewhere runs are short.
    - Threshold 18 balances run stability vs over-extension (sweep-validated:
      starts agree between 16 and 18; 20 swallows cold-open music).
    - Brute-force scan over all shifts: O(len(a)·len(b)) Hamming ops per
      pair. ~1.4M ops for two 150s windows ≈ sub-second; series sweeps
      use short windows and this is simpler + more robust than Jellyfin's
      histogram optimization (their exact-match anchors don't survive
      cross-encode drift).
    """
    best = (0, 0, 0)
    for shift in range(-len(b) + 1, len(a)):
        run_best = run = best_start = 0
        for i in range(len(a)):
            j = i + shift
            if 0 <= j < len(b) and _hamming(a[i], b[j]) <= MATCH_HAMMING_BITS:
                run += 1
                if run > run_best:
                    run_best, best_start = run, i - run + 1
            else:
                run = 0
        if run_best > best[0]:
            best = (run_best, best_start, shift)
    return best


def _snap_end_to_silence(stream_url: str, approx_end: float, window: float) -> float:
    """Move the intro's end to the first silence ≥ 0.5 s near the boundary.

    The theme outro usually ends in a hard cut to dialogue; the first quiet
    stretch at/after the matched end is the true boundary. Falls back to
    the matched end when nothing is found nearby.
    """
    cmd = (
        f'ffmpeg -hide_banner -nostats -ss {max(0.0, approx_end - 4.0):.2f} '
        f'-t {min(20.0, window - approx_end + 4.0):.2f} -i "{stream_url}" '
        f'-af "silencedetect=noise=-35dB:d=0.5" -f null - 2>&1'
    )
    try:
        out = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception:
        return approx_end
    for line in out.splitlines():
        if "silence_start" in line:
            try:
                candidate = float(line.split("silence_start:")[1].split()[0])
                snapped = approx_end - 4.0 + candidate
                if snapped > approx_end - 2.0:  # don't move the end backwards
                    return snapped
            except (ValueError, IndexError):
                break
    return approx_end


def _fingerprint_sync(item) -> List[int]:
    """Synchronous bridge — detection runs inside a worker thread; ffmpeg
    subprocess work is coordinated via asyncio in the async sweep, but the
    pure matching helpers stay sync for testability."""
    import base64 as _b64
    cache_key = _fingerprint_cache_key(item.message_id)
    cached = None
    try:
        cached = _meta_store_get(cache_key)
    except Exception:
        pass
    if cached:
        try:
            points = _decode_fingerprint(str(cached))
            if points:
                return points
        except Exception:
            pass
    stream_url = internal_stream_url(item.secure_hash, item.message_id)
    window = int(_fingerprint_window_seconds(item))
    cmd = (
        f'ffmpeg -hide_banner -loglevel error -i "{stream_url}" '
        f"-t {window} -ac 2 -ar 44100 -f mp3 - | "
        f"{_FPCALC} -length {window} -"
    )
    proc = subprocess.run(cmd, shell=True, capture_output=True, timeout=_FP_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise RuntimeError(f"fpcalc failed for bin:{item.message_id}")
    fp_line = next(
        (l for l in proc.stdout.decode(errors="replace").splitlines() if l.startswith("FINGERPRINT=")),
        "",
    )
    if not fp_line:
        raise RuntimeError(f"fpcalc produced no fingerprint for bin:{item.message_id}")
    b64 = fp_line.split("=", 1)[1]
    try:
        if media_index._store_active():
            media_index_meta_set(cache_key, b64)
    except Exception:
        pass
    return _decode_fingerprint(b64)


def _meta_store_get(key: str):
    """Sync access to the meta store (used from worker threads).

    Motor clients bind their futures to the loop they were created on
    (the main loop), so a fresh ``asyncio.run`` here would fail with
    "attached to a different loop" — marshal to the captured main loop
    instead."""
    store = media_index._store
    if store is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        asyncio.ensure_future(store.get_meta(key))  # async context: skip caching
        return None
    if _main_loop is None or not _main_loop.is_running():
        # Only reachable when a sync caller bypasses detect_series_intros —
        # without the owning loop the Motor call cannot run anywhere.
        log.warning("intro: no main loop captured; meta read for %s skipped", key)
        return None
    # Worker thread: block until the main loop answers.
    try:
        return asyncio.run_coroutine_threadsafe(
            store.get_meta(key), _main_loop,
        ).result(timeout=60)
    except Exception:
        log.exception("intro: meta read for %s failed (marshal to main loop)", key)
        return None


def media_index_meta_set(key: str, value: str) -> None:
    store = media_index._store
    if store is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        asyncio.ensure_future(store.set_meta(key, value))
        return
    if _main_loop is None or not _main_loop.is_running():
        log.warning("intro: no main loop captured; meta write for %s skipped", key)
        return
    try:
        asyncio.run_coroutine_threadsafe(
            store.set_meta(key, value), _main_loop,
        ).result(timeout=60)
    except Exception:
        log.exception("intro: meta write for %s failed (marshal to main loop)", key)


_main_loop: Optional[asyncio.AbstractEventLoop] = None


async def detect_series_intros(episodes: list) -> Dict[int, Tuple[float, float]]:
    """Async entry — capture the loop that owns the Motor client, then
    run the sync pipeline in a worker thread."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    return await asyncio.to_thread(detect_series_intros_sync, episodes)


async def detect_series_intros_async(series_key: str, episodes: list) -> None:
    """Background per-series detection. Updates ``intro_state["series_*"]``
    so the SPA edit modal can poll /admin/status for live progress and the
    outcome (intros_found > 0, series_error, or neither)."""
    if _series_lock.locked():
        raise RuntimeError("already running")
    async with _series_lock:
        st = intro_state
        st.update(
            series_running=True, series_key=series_key,
            series_done_count=0, series_total=len(episodes), series_error="",
        )
        try:
            results = await detect_series_intros(episodes)
        except Exception as exc:
            st["series_error"] = str(exc) or type(exc).__name__
            log.exception("intro: per-series sweep failed for %s", series_key)
            return
        finally:
            st["series_running"] = False
            st["series_key"] = ""
        applied = 0
        for mid, (start, end) in results.items():
            item = media_index.get_item(mid)
            if item is None:
                continue
            item.intro_start = start
            item.intro_end = end
            item.intro_source = "auto"
            await media_index._store_upsert(item)
            applied += 1
        async with media_index._lock:
            media_index._persist_unlocked()
        st["series_done_count"] = applied


# ---------------------------------------------------------------- sweep

def _series_with_multiple_episodes() -> Dict[str, list]:
    """Visible video episodes grouped by series_key, ≥2 episodes."""
    buckets: Dict[str, list] = defaultdict(list)
    for it in media_index._items.values():
        if it.hidden or (it.media_kind or "") != "video":
            continue
        if getattr(it, "series_key", "") and getattr(it, "intro_source", "") != "manual":
            buckets[it.series_key].append(it)
    return {k: v for k, v in buckets.items() if len(v) >= 2}


async def detect_all_intros() -> dict:
    """Sweep every multi-episode series. Same contract as probe_all_missing."""
    if intro_state["running"]:
        return {"already_running": True}
    series_map = _series_with_multiple_episodes()
    total_eps = sum(len(v) for v in series_map.values())
    intro_state.update(
        running=True, done=0, total=total_eps, series_done=0, intros_found=0,
        started_at=time.time(), finished_at=0.0, error="",
    )
    try:
        for series_key, episodes in series_map.items():
            try:
                results = await detect_series_intros(episodes)
            except Exception:
                log.exception("intro: series sweep failed for %s", series_key)
                results = {}
            for mid, (start, end) in results.items():
                item = media_index.get_item(mid)
                if item is None:
                    continue
                item.intro_start = start
                item.intro_end = end
                item.intro_source = "auto"
                intro_state["intros_found"] += 1
            intro_state["done"] += len(episodes)
            intro_state["series_done"] += 1
            async with media_index._lock:
                media_index._persist_unlocked()
            for mid in results:
                item = media_index.get_item(mid)
                if item is not None:
                    await media_index._store_upsert(item)
    except Exception as exc:
        intro_state["error"] = str(exc)
        log.exception("intro: sweep failed")
    finally:
        intro_state["running"] = False
        intro_state["finished_at"] = time.time()
    return {"done": intro_state["done"], "intros_found": intro_state["intros_found"]}


def detect_series_intros_sync(episodes: list) -> Dict[int, Tuple[float, float]]:
    """Thread entry: runs fingerprint subprocesses + matching synchronously."""
    results: Dict[int, Tuple[float, float]] = {}
    fingerprints: Dict[int, List[int]] = {}
    for ep in episodes:
        try:
            fingerprints[ep.message_id] = _fingerprint_sync(ep)
        except Exception as exc:
            log.info("intro: fingerprint failed for bin:%s — %s", ep.message_id, exc)
    if len(fingerprints) < 2:
        return results
    # Manual episodes (intro_source == "manual") anchor matches for their
    # siblings — without them, a series where an admin pinned one episode
    # by hand would leave the rest undetectable — but they never receive
    # auto results (their admin-set bounds always win).
    manual_ids = {
        ep.message_id for ep in episodes
        if getattr(ep, "intro_source", "") == "manual"
    }
    window = _fingerprint_window_seconds(episodes[0])
    ids = list(fingerprints)
    for pos, mid in enumerate(ids):
        item = next((e for e in episodes if e.message_id == mid), None)
        if item is None or mid in manual_ids:
            continue
        best = (0, 0)
        for other in ids:
            if other == mid:
                continue
            run, a_start, _s = _longest_contiguous_match(fingerprints[mid], fingerprints[other])
            if run > best[0]:
                best = (run, a_start)
        if best[0] < MIN_INTRO_POINTS or best[0] > MAX_INTRO_POINTS:
            continue
        start = best[1] * POINT_SECONDS
        end = (best[1] + best[0]) * POINT_SECONDS
        if start > window * 0.9:
            continue
        if start <= 5:
            start = 0.0
        end = _snap_end_to_silence(internal_stream_url(item.secure_hash, item.message_id), end, window)
        if end - start < 15:
            continue
        results[mid] = (round(start, 2), round(end, 2))
    return results
