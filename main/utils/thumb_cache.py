"""
In-memory LRU cache for thumbnail JPEGs.

Every browser hitting / triggers ~24 separate /thumb/* requests. Without
this cache, each one calls Telegram's download_media for the JPEG bytes.
Thumbnails are tiny (≤30 KB JPEG) and immutable per message — perfect for
a small in-process cache.
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
    data = get(message_id)
    if data is not None:
        return data
    # Short-circuit if we already know this thumb can't be generated.
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
        return data
