"""
Bot-owned media index for the hub.

Telegram doesn't let bots call messages.getHistory or messages.search. So
we maintain our own in-memory catalogue keyed by BIN_CHANNEL message_id,
populated by two sources:

  1. Live updates: every time a file is forwarded into BIN_CHANNEL via the
     existing stream handlers, the auto-indexer adds it here.
  2. Startup seed: a one-shot scan iterates message_ids backward from the
     current latest (discovered by sending a probe message), batch-fetching
     metadata via the bot-allowed get_messages([ids]) call. Probe is
     deleted afterwards so it doesn't pollute the channel.

Persisted to /tmp/media_index.json best-effort so a restart doesn't blank
the catalogue. /tmp is ephemeral on Koyeb, but it survives short reboots
within a container's lifetime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from main.utils.file_properties import get_hash
from main.utils.hub_query import HubItem
from main.utils.index_entry import IndexEntry, parse, title_from_filename


_INDEX_FILE = Path(os.environ.get("MEDIA_INDEX_PATH", "/tmp/media_index.json"))
_SEED_DEPTH = int(os.environ.get("MEDIA_INDEX_SEED_DEPTH", "800"))
_FETCH_BATCH = 100  # get_messages caps around 200; 100 is well within limits.

_items: Dict[int, HubItem] = {}
_lock = asyncio.Lock()
_seeded = False


def _to_serializable(item: HubItem) -> dict:
    return {
        "message_id": item.message_id,
        "secure_hash": item.secure_hash,
        "title": item.title,
        "year": item.year,
        "description": item.description,
        "tags": item.tags,
        "duration": item.duration,
        "file_size": item.file_size,
        "has_thumb": item.has_thumb,
    }


def _from_serializable(d: dict) -> HubItem:
    return HubItem(
        message_id=d["message_id"],
        secure_hash=d["secure_hash"],
        title=d["title"],
        year=d.get("year"),
        description=d.get("description", ""),
        tags=d.get("tags", []) or [],
        duration=d.get("duration", 0) or 0,
        file_size=d.get("file_size", 0) or 0,
        has_thumb=d.get("has_thumb", False),
    )


def _media_of(message):
    return (
        getattr(message, "video", None)
        or getattr(message, "document", None)
        or getattr(message, "animation", None)
    )


def _is_video_message(message) -> bool:
    if getattr(message, "empty", False):
        return False
    media = _media_of(message)
    if media is None:
        return False
    if message.video is not None or message.animation is not None:
        return True
    mime = (getattr(media, "mime_type", "") or "").lower()
    return mime.startswith("video/")


def _item_from_message(message) -> Optional[HubItem]:
    if not _is_video_message(message):
        return None
    media = _media_of(message)
    file_name = getattr(media, "file_name", None) or ""
    parsed = parse(message.caption or "")
    if parsed is None:
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


async def add_from_message(message) -> None:
    item = _item_from_message(message)
    if item is None:
        return
    async with _lock:
        _items[item.message_id] = item
        _persist_unlocked()


async def remove(message_id: int) -> None:
    async with _lock:
        _items.pop(message_id, None)
        _persist_unlocked()


def _persist_unlocked() -> None:
    try:
        _INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _INDEX_FILE.open("w", encoding="utf-8") as f:
            json.dump([_to_serializable(it) for it in _items.values()], f)
    except Exception:
        logging.debug("media_index: persist failed (non-fatal)", exc_info=True)


def _load() -> None:
    if not _INDEX_FILE.exists():
        return
    try:
        with _INDEX_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for d in data:
            try:
                item = _from_serializable(d)
                _items[item.message_id] = item
            except Exception:
                continue
        logging.info("media_index: loaded %d entries from %s", len(_items), _INDEX_FILE)
    except Exception:
        logging.warning("media_index: failed to load %s", _INDEX_FILE, exc_info=True)


async def seed(bot, channel_id: int) -> None:
    """Populate the index from BIN_CHANNEL history. Sends a probe to learn
    the latest message id, then iterates backward fetching by id batches.
    Safe to call multiple times — already-seen entries get refreshed."""
    global _seeded
    if _seeded:
        return

    _load()  # Restore whatever was on disk first.

    try:
        probe = await bot.send_message(channel_id, "🔍 seeding catalogue…")
    except Exception:
        logging.exception("media_index: probe send failed; seed skipped")
        _seeded = True
        return

    latest_id = probe.id
    try:
        await probe.delete()
    except Exception:
        logging.debug("media_index: probe delete failed (non-fatal)")

    scanned = 0
    floor = max(1, latest_id - _SEED_DEPTH)
    high = latest_id - 1
    logging.info(
        "media_index: seeding %d…%d (depth=%d)", high, floor, _SEED_DEPTH
    )
    while high >= floor:
        ids = list(range(high, max(floor - 1, high - _FETCH_BATCH), -1))
        try:
            batch = await bot.get_messages(channel_id, ids)
        except Exception:
            logging.exception("media_index: get_messages failed for %d..%d", ids[-1], ids[0])
            break
        if not isinstance(batch, list):
            batch = [batch]
        async with _lock:
            for m in batch:
                item = _item_from_message(m)
                if item is not None:
                    _items[item.message_id] = item
            _persist_unlocked()
        scanned += len(ids)
        high -= _FETCH_BATCH
        # Yield to the loop so a long seed doesn't starve other work.
        await asyncio.sleep(0)

    _seeded = True
    logging.info(
        "media_index: seed done — scanned %d ids, %d entries indexed",
        scanned, len(_items),
    )


# --- Read-side: browse / search / tag --------------------------------

def _all_sorted_desc() -> List[HubItem]:
    return sorted(_items.values(), key=lambda it: it.message_id, reverse=True)


def browse_page(before_id: Optional[int], limit: int):
    """(items, next_cursor) — newest-first, page through by message_id."""
    items_all = _all_sorted_desc()
    if before_id:
        items_all = [it for it in items_all if it.message_id < before_id]
    page = items_all[:limit]
    next_cursor = page[-1].message_id if len(page) == limit else None
    return page, next_cursor


def search(query: str, limit: int) -> List[HubItem]:
    if not query:
        return []
    q = query.lower().lstrip("#")
    items_all = _all_sorted_desc()
    out: List[HubItem] = []
    for it in items_all:
        haystack = " ".join([
            it.title.lower(),
            it.description.lower(),
            " ".join(it.tags),
        ])
        if q in haystack:
            out.append(it)
            if len(out) >= limit:
                break
    return out


def by_tag(tag: str, limit: int) -> List[HubItem]:
    tag = tag.lstrip("#").lower().strip()
    if not tag:
        return []
    items_all = _all_sorted_desc()
    out = [it for it in items_all if tag in it.tags]
    return out[:limit]


def size() -> int:
    return len(_items)
