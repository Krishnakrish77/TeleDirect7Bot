"""
Two-tier thumbnail cache.

Layer 1: in-memory LRU per process. Layer 2: persistent MongoDB
collection (when configured). Survives restart so series-page
thumbnails don't re-run ffmpeg from cold every deploy — which was
costing ~5-10 s per item × 50 items per series page.

Every browser hitting / triggers ~24 separate /thumb/* requests. Each
call hierarchy:

   ┌── L1 (in-memory) hit ── return
   │
   ├── L2 (Mongo) hit ────── hydrate L1, return
   │
   └── fetcher() (Telegram download_media or ffmpeg frame grab)
        └── on success → write through to L1 + L2
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple


MAX_ENTRIES = int(__import__("os").environ.get("THUMB_CACHE_MAX", "256"))
TTL_SECONDS = 6 * 60 * 60      # 6h success TTL — thumbs don't change
FAIL_TTL_SECONDS = 60 * 60     # 1h failure TTL — don't retry broken files every page load


_cache: "OrderedDict[int, Tuple[float, bytes]]" = OrderedDict()
_locks: Dict[int, asyncio.Lock] = {}
_failures: Dict[int, float] = {}  # message_id → timestamp of last fetch failure
_global_lock = asyncio.Lock()


def _store():
    """Lazy accessor for the MongoDB-backed durable store. Returns None
    when no Mongo is configured — callers fall back to L1-only behaviour.
    Import lazily to avoid a startup-time circular import via media_index."""
    try:
        from main.utils import media_index
        return media_index._store
    except Exception:
        return None


def get(message_id: int) -> Optional[bytes]:
    entry = _cache.get(message_id)
    if entry is None:
        return None
    ts, data = entry
    if (time.monotonic() - ts) > TTL_SECONDS:
        # Don't bother evicting here; the next set() trims.
        return None
    _cache.move_to_end(message_id)  # LRU bump
    return data


def set_(message_id: int, data: bytes) -> None:
    _cache[message_id] = (time.monotonic(), data)
    _cache.move_to_end(message_id)
    while len(_cache) > MAX_ENTRIES:
        evicted_key, _ = _cache.popitem(last=False)
        _locks.pop(evicted_key, None)


def lock_for(message_id: int) -> asyncio.Lock:
    """Return a per-message lock so multiple concurrent /thumb requests for
    the same file collapse into a single Telegram download."""
    return _locks.setdefault(message_id, asyncio.Lock())


async def cached_or_fetch(message_id: int, fetcher) -> Optional[bytes]:
    """Returns thumb bytes from cache if present, otherwise calls fetcher().
    Concurrent requests for the same message_id share one fetch.
    Failed fetches are remembered for FAIL_TTL_SECONDS so broken files
    (corrupt MP4, revoked file_id) don't spawn a new ffmpeg process on
    every page load."""
    # L1 — in-memory LRU
    data = get(message_id)
    if data is not None:
        return data
    # Failure short-circuit (don't keep retrying broken files this hour)
    fail_ts = _failures.get(message_id, 0.0)
    if fail_ts and (time.monotonic() - fail_ts) < FAIL_TTL_SECONDS:
        return None
    async with lock_for(message_id):
        # Re-check inside the lock — another coroutine may have populated.
        data = get(message_id)
        if data is not None:
            return data
        fail_ts = _failures.get(message_id, 0.0)
        if fail_ts and (time.monotonic() - fail_ts) < FAIL_TTL_SECONDS:
            return None
        # L2 — durable store (MongoDB). Hydrate L1 on hit so subsequent
        # requests in the same process are L1-fast.
        store = _store()
        if store is not None:
            try:
                persisted = await store.get_thumb(message_id)
            except Exception:
                logging.exception("thumb_cache: L2 get failed for msg %d", message_id)
                persisted = None
            if persisted:
                set_(message_id, persisted)
                return persisted
        # Miss everywhere — fetch fresh.
        try:
            data = await fetcher()
        except Exception:
            logging.exception("Thumb fetch failed for msg %d", message_id)
            _failures[message_id] = time.monotonic()
            return None
        if data is not None:
            set_(message_id, data)
            _failures.pop(message_id, None)
        else:
            _failures[message_id] = time.monotonic()

    # ── outside lock_for(message_id) ──
    # Fire-and-forget L2 write so a slow Mongo round trip doesn't block
    # other concurrent /thumb requests for the same message. The bytes
    # are already in L1 so subsequent in-process hits are still fast;
    # the durable mirror is best-effort.
    if data is not None and store is not None:
        async def _persist():
            try:
                await store.set_thumb(message_id, data)
            except Exception:
                logging.exception(
                    "thumb_cache: L2 set failed for msg %d", message_id,
                )
        try:
            asyncio.create_task(_persist())
        except RuntimeError:
            pass  # no running loop; skip
    return data
