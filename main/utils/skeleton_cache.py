"""
Range cache for the "skeleton" bytes of remote video files.

Each on-demand HLS segment request runs a fresh ffmpeg subprocess, and each
subprocess re-reads the same handful of small regions from the source:

  * the file header at offset 0 (~tens of KB for MKV)
  * the SeekHead + Cues block at the end of the file (~tens to hundreds of KB)
  * the cluster containing the target timestamp (variable)

The first two are deterministic and small. Caching them in memory turns
N Telegram round-trips per seek into 1 — every segment after the cache is
warm avoids the head/tail fetches entirely.

The cache is keyed by Telegram message_id. Entries hold the first HEAD_SIZE
bytes and the last TAIL_SIZE bytes of the file. A simple TTL evicts stale
entries.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections import OrderedDict
from typing import Dict, Optional

from main.utils import chunk_size as _chunk_size, offset_fix as _offset_fix


HEAD_SIZE = 2 * 1024 * 1024     # 2 MB  — covers MKV header + SeekHead
TAIL_SIZE = 512 * 1024          # 512 KB — covers MP4 MOOV (usually <200 KB);
                                 # smaller = faster cold-cache warmup for
                                 # ffprobe/ffmpeg thumbnail seeks
TTL_SECONDS = 60 * 60         # 1 hour


class _Entry:
    __slots__ = ("file_size", "head", "tail", "added_at", "lock")

    def __init__(self, file_size: int):
        self.file_size = file_size
        self.head: Optional[bytes] = None
        self.tail: Optional[bytes] = None
        self.added_at = time.monotonic()
        self.lock = asyncio.Lock()


# Each entry holds up to HEAD_SIZE + TAIL_SIZE ≈ 2.5 MB. Cap at 30 entries
# (~75 MB) so a bot serving many distinct files doesn't exhaust free-tier RAM.
_MAX_ENTRIES = int(os.environ.get("SKELETON_CACHE_MAX", "30"))
_cache: "OrderedDict[int, _Entry]" = OrderedDict()


class SkeletonFetchError(RuntimeError):
    """Raised when a head/tail fetch comes back short.

    ``ByteStreamer.yield_file`` swallows ``TimeoutError`` mid-stream and just
    stops yielding, so a truncated Telegram fetch returns fewer bytes than the
    chunk-aligned window we asked for. If we cached and served those bytes, the
    response would claim the full requested range in ``Content-Range`` while
    delivering less — ffmpeg's HTTP reader then hits EOF mid-parse, returns
    AVERROR_EOF (exit 187), and the MP4 demuxer aborts with "error reading
    header". Treat short reads as an outright failure so the entry stays
    uncached and the next request gets a fresh attempt.
    """


def _entry(message_id: int, file_size: int) -> _Entry:
    entry = _cache.get(message_id)
    now = time.monotonic()
    if (
        entry is None
        or entry.file_size != file_size
        or (now - entry.added_at) > TTL_SECONDS
    ):
        entry = _Entry(file_size)
        _cache[message_id] = entry
        # LRU eviction: drop oldest entry when over cap.
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)
    else:
        # Bump to most-recently-used end.
        _cache.move_to_end(message_id)
    return entry


def head_limit(file_size: int) -> int:
    """Last byte index (inclusive) covered by the head cache."""
    return min(HEAD_SIZE, file_size) - 1


def tail_floor(file_size: int) -> int:
    """First byte index covered by the tail cache."""
    return max(0, file_size - TAIL_SIZE)


async def _collect_range(
    byte_streamer, file_id, index: int, start: int, end: int
) -> bytes:
    """Drain ByteStreamer.yield_file for a byte range into memory.

    yield_file's part_count formula doesn't account for first_part_cut, so a
    range that starts mid-chunk would end up short by that many bytes. Sidestep
    that by aligning the underlying fetch to chunk boundaries (first_part_cut
    forced to 0, last_part_cut forced to full chunk_sz), fetch enough whole
    chunks to cover the target end, then slice the requested range out of the
    collected bytes.
    """
    length = end - start + 1
    chunk_sz = _chunk_size(length)
    aligned_start = _offset_fix(start, chunk_sz)
    # Smallest chunk-aligned end that covers `end`.
    aligned_end_exclusive = ((end // chunk_sz) + 1) * chunk_sz
    part_count = (aligned_end_exclusive - aligned_start) // chunk_sz

    chunks = []
    async for chunk in byte_streamer.yield_file(
        file_id, index, aligned_start, 0, chunk_sz, part_count, chunk_sz
    ):
        chunks.append(chunk)
    raw = b"".join(chunks)
    rel_start = start - aligned_start
    if len(raw) < rel_start + length:
        raise SkeletonFetchError(
            f"truncated fetch for [{start},{end}]: got {len(raw)} bytes, "
            f"need {rel_start + length}"
        )
    return raw[rel_start : rel_start + length]


async def get_or_fetch_head(
    message_id: int, file_size: int, byte_streamer, file_id, index: int
) -> bytes:
    entry = _entry(message_id, file_size)
    if entry.head is not None:
        return entry.head
    async with entry.lock:
        if entry.head is not None:
            return entry.head
        end = head_limit(file_size)
        logging.debug("Warming head cache for msg %d (%d bytes)", message_id, end + 1)
        entry.head = await _collect_range(byte_streamer, file_id, index, 0, end)
        return entry.head


async def get_or_fetch_tail(
    message_id: int, file_size: int, byte_streamer, file_id, index: int
) -> bytes:
    entry = _entry(message_id, file_size)
    if entry.tail is not None:
        return entry.tail
    async with entry.lock:
        if entry.tail is not None:
            return entry.tail
        start = tail_floor(file_size)
        logging.debug(
            "Warming tail cache for msg %d (%d bytes)", message_id, file_size - start
        )
        entry.tail = await _collect_range(
            byte_streamer, file_id, index, start, file_size - 1
        )
        return entry.tail


def serve_head(message_id: int, from_bytes: int, until_bytes: int) -> Optional[bytes]:
    entry = _cache.get(message_id)
    if entry is None or entry.head is None:
        return None
    if until_bytes >= len(entry.head):
        return None
    return entry.head[from_bytes : until_bytes + 1]


def serve_tail(message_id: int, from_bytes: int, until_bytes: int) -> Optional[bytes]:
    entry = _cache.get(message_id)
    if entry is None or entry.tail is None:
        return None
    t_start = entry.file_size - len(entry.tail)
    if from_bytes < t_start:
        return None
    return entry.tail[from_bytes - t_start : until_bytes - t_start + 1]


async def prefetch_skeleton(
    message_id: int, file_size: int, byte_streamer, file_id, index: int
) -> None:
    """Fire-and-forget warmer. Catches exceptions so a failing prefetch
    never bubbles into the caller's task."""
    try:
        await asyncio.gather(
            get_or_fetch_head(message_id, file_size, byte_streamer, file_id, index),
            get_or_fetch_tail(message_id, file_size, byte_streamer, file_id, index),
        )
    except SkeletonFetchError as exc:
        # Expected under load when yield_file times out mid-stream. Log at
        # warning level (not exception) — next request re-fetches.
        logging.warning("Skeleton prefetch incomplete for msg %d: %s", message_id, exc)
    except Exception:
        logging.exception("Skeleton prefetch failed for msg %d", message_id)
