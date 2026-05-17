"""
Query helpers for the media hub.

The hub reads BIN_CHANNEL directly: each video message there has a structured
caption written by the auto-indexer (see main/utils/indexer.py), and the
message id itself is the byte-stream pointer.

A short TTL cache sits in front of get_chat_history/search_messages because
Telegram is rate-limited and the same browse pages get hit by every viewer.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from main import StreamBot
from main.utils.file_properties import get_hash
from main.utils.index_entry import IndexEntry, parse, title_from_filename
from main.vars import Var


PAGE_SIZE = 24
FETCH_FAN_OUT = 60  # over-fetch to account for non-media messages filtered out
CACHE_TTL = 30  # seconds


@dataclass
class HubItem:
    message_id: int
    secure_hash: str  # 6-char hash to build /watch and /hls URLs
    title: str
    year: Optional[int]
    description: str
    tags: List[str]
    duration: int  # seconds, 0 if unknown
    file_size: int  # bytes, 0 if unknown
    has_thumb: bool


def _media_of(message):
    return getattr(message, "video", None) or getattr(message, "document", None) or getattr(message, "animation", None)


def _is_video_message(message) -> bool:
    media = _media_of(message)
    if media is None:
        return False
    mime = (getattr(media, "mime_type", "") or "").lower()
    if message.video is not None or message.animation is not None:
        return True
    return mime.startswith("video/")


def _item_from_message(message) -> Optional[HubItem]:
    if not _is_video_message(message):
        return None
    media = _media_of(message)
    file_name = getattr(media, "file_name", None) or ""
    parsed = parse(message.caption or "")
    if parsed is None:
        # Falls back to a minimal entry derived from filename.
        parsed = IndexEntry(title=title_from_filename(file_name))
    return HubItem(
        message_id=message.id,
        secure_hash=get_hash(message),
        title=parsed.title,
        year=parsed.year,
        description=parsed.description,
        tags=parsed.tags,
        duration=int(getattr(media, "duration", 0) or 0),
        file_size=int(getattr(media, "file_size", 0) or 0),
        has_thumb=bool(getattr(media, "thumbs", None)),
    )


# Minimal TTL caches keyed by (before, page_size) for browse and (query,) for
# search. Entries expire after CACHE_TTL seconds.
_browse_cache: dict = {}
_search_cache: dict = {}
_cache_lock = asyncio.Lock()


def _fresh(key, cache):
    entry = cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if (time.monotonic() - ts) > CACHE_TTL:
        return None
    return value


async def browse(before_id: Optional[int] = None, limit: int = PAGE_SIZE) -> Tuple[List[HubItem], Optional[int]]:
    """Newest-first page of hub items. Returns (items, next_cursor).

    Uses search_messages(query="") instead of get_chat_history — bots can
    hit messages.search but get_history is restricted for them on some
    channels.
    """
    cache_key = (before_id, limit)
    cached = _fresh(cache_key, _browse_cache)
    if cached is not None:
        return cached

    items: List[HubItem] = []
    next_cursor: Optional[int] = None

    try:
        async for message in StreamBot.search_messages(
            chat_id=Var.BIN_CHANNEL,
            query="",
            offset_id=before_id or 0,
            limit=FETCH_FAN_OUT,
        ):
            next_cursor = message.id
            item = _item_from_message(message)
            if item is None:
                continue
            items.append(item)
            if len(items) >= limit:
                break
    except Exception:
        import logging
        logging.exception("Failed to fetch BIN_CHANNEL history; showing empty hub")
        return [], None

    if len(items) < limit:
        next_cursor = None

    result = (items, next_cursor)
    async with _cache_lock:
        _browse_cache[cache_key] = (time.monotonic(), result)
    return result


async def search(query: str, limit: int = PAGE_SIZE) -> List[HubItem]:
    """Search BIN_CHANNEL captions; returns up to `limit` matching items."""
    query = query.strip()
    if not query:
        return []

    cache_key = (query, limit)
    cached = _fresh(cache_key, _search_cache)
    if cached is not None:
        return cached

    items: List[HubItem] = []
    try:
        async for message in StreamBot.search_messages(
            chat_id=Var.BIN_CHANNEL, query=query, limit=FETCH_FAN_OUT
        ):
            item = _item_from_message(message)
            if item is None:
                continue
            items.append(item)
            if len(items) >= limit:
                break
    except Exception:
        import logging
        logging.exception("search_messages failed for query=%r", query)
        return []

    async with _cache_lock:
        _search_cache[cache_key] = (time.monotonic(), items)
    return items


async def by_tag(tag: str, limit: int = PAGE_SIZE) -> List[HubItem]:
    """Items tagged #tag (case-insensitive)."""
    tag = tag.lstrip("#").strip().lower()
    if not tag:
        return []
    # Telegram's search treats "#tag" as a hashtag query.
    return await search(f"#{tag}", limit=limit)
