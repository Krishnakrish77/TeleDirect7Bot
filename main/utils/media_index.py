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
import re
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from main.utils.file_properties import get_hash
from main.utils.hub_query import ExternalSubtitle, HubItem, SeriesGroup
from main.utils.index_entry import IndexEntry, parse, title_from_filename
from main.utils import series as series_parse
from main.utils.subtitles import stem_for_pairing


# Buckets we recognise in filenames/captions. Order matters: longest match
# first so "2160p" wins over "p", "4K" wins over "K" alone.
_QUALITY_PATTERNS: List[Tuple[str, "re.Pattern"]] = [
    ("4K",    re.compile(r"\b(2160p|uhd|4k)\b", re.IGNORECASE)),
    ("1080p", re.compile(r"\b1080p?\b|\bfhd\b", re.IGNORECASE)),
    ("720p",  re.compile(r"\b720p?\b|\bhd\b", re.IGNORECASE)),
    ("480p",  re.compile(r"\b480p?\b|\bsd\b", re.IGNORECASE)),
]


def _extract_quality(*texts: str) -> str:
    """Pick the highest-resolution match across the given haystacks."""
    haystack = " ".join(t for t in texts if t)
    for label, pat in _QUALITY_PATTERNS:
        if pat.search(haystack):
            return label
    return ""


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
        "quality": item.quality,
        "file_name": item.file_name,
        "series_key": item.series_key,
        "series_title": item.series_title,
        "season": item.season,
        "episode": item.episode,
        "subtitles": [
            {
                "bin_message_id": s.bin_message_id,
                "secure_hash": s.secure_hash,
                "language": s.language,
                "label": s.label,
            }
            for s in item.subtitles
        ],
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
        quality=d.get("quality", "") or "",
        file_name=d.get("file_name", "") or "",
        series_key=d.get("series_key", "") or "",
        series_title=d.get("series_title", "") or "",
        season=d.get("season"),
        episode=d.get("episode"),
        subtitles=[
            ExternalSubtitle(
                bin_message_id=s["bin_message_id"],
                secure_hash=s.get("secure_hash", ""),
                language=s.get("language", ""),
                label=s.get("label", ""),
            )
            for s in (d.get("subtitles") or [])
        ],
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
    # Try filename first (release names follow conventions), fall back to the
    # parsed caption title for hand-written entries.
    sm = series_parse.parse(file_name) or series_parse.parse(parsed.title)

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
        quality=_extract_quality(parsed.title, file_name, parsed.description),
        file_name=file_name,
        series_key=sm.key if sm else "",
        series_title=sm.title if sm else "",
        season=sm.season if sm else None,
        episode=sm.episode if sm else None,
    )


async def add_from_message(message) -> None:
    item = _item_from_message(message)
    if item is None:
        return
    async with _lock:
        # Preserve any sidecar subtitle pairings across re-indexing.
        existing = _items.get(item.message_id)
        if existing and existing.subtitles:
            item.subtitles = existing.subtitles
        _items[item.message_id] = item
        _persist_unlocked()


def get_item(message_id: int) -> Optional[HubItem]:
    return _items.get(message_id)


def find_by_hash(secure_hash: str) -> Optional[HubItem]:
    """Linear lookup by secure_hash (first 6 chars of file_unique_id)."""
    if not secure_hash:
        return None
    for it in _items.values():
        if it.secure_hash == secure_hash:
            return it
    return None


def find_by_filename_stem(stem: str) -> Optional[HubItem]:
    """Most-recent indexed video whose filename stem matches.

    Used to pair an external .srt with a video when the upload wasn't sent
    as a reply. Latest-first so a re-uploaded version wins over older copies.
    """
    if not stem:
        return None
    matches = [
        it for it in _items.values()
        if it.file_name and stem_for_pairing(it.file_name) == stem
    ]
    if not matches:
        return None
    return max(matches, key=lambda it: it.message_id)


async def attach_subtitle(video_message_id: int, sub: ExternalSubtitle) -> bool:
    """Link a sidecar to a video. Returns True if the video was found."""
    async with _lock:
        item = _items.get(video_message_id)
        if item is None:
            return False
        # Replace any existing entry for the same bin id (re-upload case).
        item.subtitles = [
            s for s in item.subtitles if s.bin_message_id != sub.bin_message_id
        ]
        item.subtitles.append(sub)
        _persist_unlocked()
        return True


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
    """Populate the index from BIN_CHANNEL history.

    Bots can't call ``messages.getHistory`` (Pyrogram's ``get_chat_history``
    raises an RPCError for bot accounts), so we discover the latest message
    id by sending a tiny probe to the channel and reading its returned id,
    then deleting the probe. The probe ``​`` is a zero-width space —
    if a client renders it before delete lands, the message is at worst a
    single blank cell rather than human-readable text.
    """
    global _seeded
    if _seeded:
        return

    _load()  # Restore whatever was on disk first.

    try:
        probe = await bot.send_message(channel_id, "​")
    except Exception:
        logging.exception("media_index: probe send failed; seed skipped")
        _seeded = True
        return

    latest_id = probe.id
    # Best-effort delete with one retry — the visible-probe complaint is
    # rare but worth defending against. We do this BEFORE the long history
    # scan so the channel cleans up fast.
    for _ in range(2):
        try:
            await probe.delete()
            break
        except Exception:
            await asyncio.sleep(0.5)
    else:
        logging.warning(
            "media_index: probe delete failed after retries (bin:%d)", latest_id
        )

    scanned = 0
    floor = max(1, latest_id - _SEED_DEPTH)
    high = latest_id - 1  # the probe itself is gone; start one below it
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


# --- Read-side: unified query -----------------------------------------

_SORT_KEYS = {
    "newest":   (lambda it: it.message_id, True),
    "oldest":   (lambda it: it.message_id, False),
    "title_az": (lambda it: it.title.lower(), False),
    "title_za": (lambda it: it.title.lower(), True),
    "largest":  (lambda it: it.file_size, True),
    "smallest": (lambda it: it.file_size, False),
}


def _matches(item: HubItem, q: str, year: Optional[int], quality: str, tag: str) -> bool:
    if year is not None and item.year != year:
        return False
    if quality and item.quality.lower() != quality.lower():
        return False
    if tag and tag.lstrip("#").lower() not in item.tags:
        return False
    if q:
        ql = q.lower().lstrip("#")
        haystack = " ".join([
            item.title.lower(),
            item.description.lower(),
            " ".join(item.tags),
        ])
        if ql not in haystack:
            return False
    return True


def query(
    *,
    q: str = "",
    year: Optional[int] = None,
    quality: str = "",
    tag: str = "",
    sort: str = "newest",
    before_id: Optional[int] = None,
    limit: int = 24,
) -> Tuple[List[HubItem], Optional[int]]:
    """Unified filter + sort + paginate over the in-process catalogue."""
    key_fn, reverse = _SORT_KEYS.get(sort, _SORT_KEYS["newest"])
    items_all = sorted(_items.values(), key=key_fn, reverse=reverse)

    items_all = [it for it in items_all if _matches(it, q, year, quality, tag)]

    # Pagination cursor is only meaningful for message_id-ordered sorts.
    if before_id and sort in ("newest",):
        items_all = [it for it in items_all if it.message_id < before_id]
    elif before_id and sort == "oldest":
        items_all = [it for it in items_all if it.message_id > before_id]

    page = items_all[:limit]
    next_cursor = None
    if len(page) == limit and sort in ("newest", "oldest"):
        next_cursor = page[-1].message_id
    return page, next_cursor


def _build_series_group(episodes: List[HubItem]) -> SeriesGroup:
    """Construct a SeriesGroup from at least one episode HubItem."""
    poster = next(
        (e for e in episodes if e.has_thumb),
        max(episodes, key=lambda e: e.message_id),
    )
    seasons = {e.season for e in episodes if e.season is not None}
    return SeriesGroup(
        series_key=episodes[0].series_key,
        series_title=episodes[0].series_title or episodes[0].title,
        episode_count=len(episodes),
        season_count=len(seasons) or 1,
        latest_message_id=max(e.message_id for e in episodes),
        poster_item=poster,
        has_thumb=any(e.has_thumb for e in episodes),
    )


def query_grouped(
    *,
    q: str = "",
    year: Optional[int] = None,
    quality: str = "",
    tag: str = "",
    sort: str = "newest",
    limit: int = 24,
) -> List:
    """Like query() but collapses items sharing a series_key into one card.

    Returned list mixes HubItem (standalone) and SeriesGroup (series).
    Series search match-on-title: a query for ``office`` matches the series
    name itself in addition to per-episode titles, so groups don't get
    filtered away when the user types the show name.
    """
    items_all = [
        it for it in _items.values()
        if _matches(it, q, year, quality, tag) or _series_matches_query(it, q)
    ]

    groups: dict = {}
    standalone: List[HubItem] = []
    for it in items_all:
        if it.series_key:
            groups.setdefault(it.series_key, []).append(it)
        else:
            standalone.append(it)

    grouped = [_build_series_group(eps) for eps in groups.values()]

    if sort == "newest":
        sort_key = lambda x: -(x.latest_message_id if isinstance(x, SeriesGroup) else x.message_id)
    elif sort == "oldest":
        sort_key = lambda x: (x.latest_message_id if isinstance(x, SeriesGroup) else x.message_id)
    elif sort == "title_az":
        sort_key = lambda x: (x.series_title if isinstance(x, SeriesGroup) else x.title).lower()
    elif sort == "title_za":
        sort_key = lambda x: tuple(-ord(c) for c in (x.series_title if isinstance(x, SeriesGroup) else x.title).lower())
    elif sort == "largest":
        sort_key = lambda x: -(sum(e.file_size for e in groups.get(x.series_key, [])) if isinstance(x, SeriesGroup) else x.file_size)
    else:
        sort_key = lambda x: -(x.latest_message_id if isinstance(x, SeriesGroup) else x.message_id)

    combined = sorted(grouped + standalone, key=sort_key)
    return combined[:limit]


def _series_matches_query(it: HubItem, q: str) -> bool:
    if not q or not it.series_key:
        return False
    return q.lower().lstrip("#") in it.series_title.lower()


def episodes_for_series(series_key: str) -> List[HubItem]:
    """All episodes for a series, sorted by season then episode."""
    eps = [it for it in _items.values() if it.series_key == series_key]
    eps.sort(key=lambda e: (e.season or 0, e.episode or 0, e.message_id))
    return eps


def distinct_years() -> List[int]:
    """Years present in the catalogue, newest first."""
    return sorted({it.year for it in _items.values() if it.year}, reverse=True)


def distinct_qualities() -> List[str]:
    """Qualities present, ordered by resolution from 4K to 480p."""
    present = {it.quality for it in _items.values() if it.quality}
    order = ["4K", "1080p", "720p", "480p"]
    return [q for q in order if q in present]


def tag_cloud(limit: int = 30) -> List[Tuple[str, int]]:
    """Most-used tags with usage counts."""
    counter: Counter = Counter()
    for it in _items.values():
        counter.update(it.tags)
    return counter.most_common(limit)


def size() -> int:
    return len(_items)
