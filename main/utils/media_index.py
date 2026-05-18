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
from main.utils.hub_query import ExternalSubtitle, HubItem, MovieGroup, SeriesGroup
from main.utils.index_entry import IndexEntry, parse, title_from_filename
from main.utils import series as series_parse
from main.utils.dedup import movie_key as compute_movie_key
from main.utils.subtitles import stem_for_pairing
from main.utils import tmdb


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
# Highest BIN_CHANNEL message id we've ever observed. Persisted so a
# warm-restart can resume scanning without sending a probe.
_latest_seen_id: int = 0


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
        "movie_key": item.movie_key,
        "tmdb_id": item.tmdb_id,
        "tmdb_kind": item.tmdb_kind,
        "imdb_id": item.imdb_id,
        "poster_path": item.poster_path,
        "backdrop_path": item.backdrop_path,
        "overview": item.overview,
        "tmdb_genres": item.tmdb_genres,
        "enriched_at": item.enriched_at,
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
        movie_key=d.get("movie_key", "") or "",
        tmdb_id=d.get("tmdb_id"),
        tmdb_kind=d.get("tmdb_kind", "") or "",
        imdb_id=d.get("imdb_id", "") or "",
        poster_path=d.get("poster_path", "") or "",
        backdrop_path=d.get("backdrop_path", "") or "",
        overview=d.get("overview", "") or "",
        tmdb_genres=d.get("tmdb_genres", []) or [],
        enriched_at=float(d.get("enriched_at", 0) or 0),
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
    # Movies get a dedup key; series episodes get "" (the series_key path
    # already collapses them by show name).
    mk = "" if sm else compute_movie_key(parsed.title or file_name, parsed.year, file_name)

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
        movie_key=mk,
    )


async def add_from_message(message) -> None:
    global _latest_seen_id
    item = _item_from_message(message)
    if item is None:
        # Even non-video messages (e.g. .srt sidecars, deleted/empty msgs)
        # tell us "the channel has reached at least this id" — record it
        # so the next restart can skip the probe.
        mid = getattr(message, "id", 0) or 0
        if mid > _latest_seen_id:
            async with _lock:
                _latest_seen_id = mid
                _persist_unlocked()
        return
    async with _lock:
        # Preserve any sidecar subtitle pairings across re-indexing.
        existing = _items.get(item.message_id)
        if existing and existing.subtitles:
            item.subtitles = existing.subtitles
        _items[item.message_id] = item
        if item.message_id > _latest_seen_id:
            _latest_seen_id = item.message_id
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
        payload = {
            "latest_seen_id": _latest_seen_id,
            "items": [_to_serializable(it) for it in _items.values()],
        }
        with _INDEX_FILE.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        logging.debug("media_index: persist failed (non-fatal)", exc_info=True)


def _load() -> None:
    global _latest_seen_id
    if not _INDEX_FILE.exists():
        return
    try:
        with _INDEX_FILE.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        # Backwards-compat: older builds wrote a bare list of items.
        if isinstance(raw, list):
            data = raw
            persisted_latest = 0
        else:
            data = raw.get("items") or []
            persisted_latest = int(raw.get("latest_seen_id") or 0)
        for d in data:
            try:
                item = _from_serializable(d)
                _items[item.message_id] = item
            except Exception:
                continue
        # Highest of (persisted, max indexed) so a hand-edited or partial
        # file still gives us a sensible floor.
        local_max = max((it.message_id for it in _items.values()), default=0)
        _latest_seen_id = max(persisted_latest, local_max)
        logging.info(
            "media_index: loaded %d entries from %s (latest_seen_id=%d)",
            len(_items), _INDEX_FILE, _latest_seen_id,
        )
    except Exception:
        logging.warning("media_index: failed to load %s", _INDEX_FILE, exc_info=True)


async def _delete_probe(bot, channel_id: int, probe_id: int) -> None:
    """Delete a freshly-sent probe message with checked retries.

    Pyrogram's Message.delete() returns silently on some failure modes
    (rate limits, permission edge cases). We call delete_messages directly,
    check the integer return value, and retry with exponential backoff.
    Anything left visible afterwards gets logged loudly so an operator can
    clean it up manually.
    """
    delay = 0.3
    for attempt in range(5):
        try:
            n = await bot.delete_messages(channel_id, probe_id)
            if n and int(n) > 0:
                return
            logging.warning(
                "media_index: probe delete returned 0 (attempt %d/%d, bin:%d)",
                attempt + 1, 5, probe_id,
            )
        except Exception as exc:
            logging.warning(
                "media_index: probe delete raised %s (attempt %d/%d, bin:%d)",
                exc.__class__.__name__, attempt + 1, 5, probe_id,
            )
        await asyncio.sleep(delay)
        delay *= 2  # 0.3 → 0.6 → 1.2 → 2.4 → 4.8s
    logging.error(
        "media_index: probe bin:%d still in channel after 5 attempts — "
        "delete it manually or the channel will show a leftover dot",
        probe_id,
    )


async def seed(bot, channel_id: int) -> None:
    """Populate the index from BIN_CHANNEL history.

    Bots can't call ``messages.getHistory``, so on a cold start we discover
    the latest message id by sending a tiny dot to the channel and reading
    its returned id. On warm restarts we already know ``_latest_seen_id``
    from the persisted JSON — the auto-indexer bumps it on every BIN
    message — so we skip the probe entirely. This makes a visible "."
    appear at most ONCE in the channel's lifetime, not on every restart.
    """
    global _seeded, _latest_seen_id
    if _seeded:
        return

    _load()  # Restore whatever was on disk first.

    latest_id = _latest_seen_id
    if latest_id <= 0:
        # No persisted state — first run. Send a probe to learn the id, then
        # erase it with delete_messages + retries.
        try:
            probe = await bot.send_message(channel_id, ".")
        except Exception:
            logging.exception("media_index: probe send failed; seed skipped")
            _seeded = True
            return
        latest_id = probe.id
        await _delete_probe(bot, channel_id, probe.id)
    else:
        logging.info(
            "media_index: skipping probe — resuming from persisted id %d",
            latest_id,
        )

    if latest_id > _latest_seen_id:
        _latest_seen_id = latest_id

    scanned = 0
    floor = max(1, latest_id - _SEED_DEPTH)
    high = latest_id
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


def _build_movie_group(variants: List[HubItem]) -> MovieGroup:
    """Construct a MovieGroup from 2+ same-movie uploads."""
    poster = next(
        (v for v in variants if v.has_thumb),
        max(variants, key=lambda v: v.message_id),
    )
    # Use the longest title as the canonical display name — the longest
    # is usually the most descriptive (channel prefixes and release flags
    # add length but they're equally common across variants).
    canonical = max(variants, key=lambda v: len(v.title or ""))
    return MovieGroup(
        movie_key=variants[0].movie_key,
        title=canonical.title or variants[0].title,
        year=canonical.year or variants[0].year,
        variant_count=len(variants),
        latest_message_id=max(v.message_id for v in variants),
        poster_item=poster,
        has_thumb=any(v.has_thumb for v in variants),
        total_size=sum(v.file_size for v in variants),
    )


def query_grouped(
    *,
    q: str = "",
    year: Optional[int] = None,
    quality: str = "",
    tag: str = "",
    sort: str = "newest",
    offset: int = 0,
    limit: int = 24,
) -> Tuple[List, int]:
    """Filter, sort, collapse, and paginate the catalogue.

    Returned list mixes HubItem (singletons), SeriesGroup (TV episodes
    collapsed by series_key), and MovieGroup (2+ uploads of the same film
    collapsed by movie_key). Returns ``(page, total_count)`` so the caller
    can build a Load More button without an extra query.
    """
    items_all = [
        it for it in _items.values()
        if _matches(it, q, year, quality, tag) or _series_matches_query(it, q)
    ]

    series_groups: dict = {}
    movie_groups: dict = {}
    standalone: List[HubItem] = []
    for it in items_all:
        if it.series_key:
            series_groups.setdefault(it.series_key, []).append(it)
        elif it.movie_key:
            movie_groups.setdefault(it.movie_key, []).append(it)
        else:
            standalone.append(it)

    grouped_series = [_build_series_group(eps) for eps in series_groups.values()]
    # Only collapse when there are 2+ variants — a single upload stays a
    # plain HubItem so the card links straight to /watch instead of
    # forcing the user through a one-row variants page.
    grouped_movies: List = []
    for variants in movie_groups.values():
        if len(variants) >= 2:
            grouped_movies.append(_build_movie_group(variants))
        else:
            standalone.append(variants[0])

    if sort == "newest":
        sort_key = lambda x: -_card_message_id(x)
    elif sort == "oldest":
        sort_key = lambda x: _card_message_id(x)
    elif sort == "title_az":
        sort_key = lambda x: _card_title(x).lower()
    elif sort == "title_za":
        sort_key = lambda x: tuple(-ord(c) for c in _card_title(x).lower())
    elif sort == "largest":
        sort_key = lambda x: -_card_file_size(x)
    else:
        sort_key = lambda x: -_card_message_id(x)

    combined = sorted(grouped_series + grouped_movies + standalone, key=sort_key)
    total = len(combined)
    page = combined[offset : offset + limit]
    return page, total


def _card_message_id(card) -> int:
    if isinstance(card, (SeriesGroup, MovieGroup)):
        return card.latest_message_id
    return card.message_id


def _card_title(card) -> str:
    if isinstance(card, SeriesGroup):
        return card.series_title
    if isinstance(card, MovieGroup):
        return card.title
    return card.title


def _card_file_size(card) -> int:
    if isinstance(card, MovieGroup):
        return card.total_size
    if isinstance(card, SeriesGroup):
        # Series rank by their largest single episode rather than the
        # sum, since otherwise long-running shows always dominate.
        return max((e.file_size for e in episodes_for_series(card.series_key)), default=0)
    return card.file_size


def _series_matches_query(it: HubItem, q: str) -> bool:
    if not q or not it.series_key:
        return False
    return q.lower().lstrip("#") in it.series_title.lower()


def episodes_for_series(series_key: str) -> List[HubItem]:
    """All episodes for a series, sorted by season then episode."""
    eps = [it for it in _items.values() if it.series_key == series_key]
    eps.sort(key=lambda e: (e.season or 0, e.episode or 0, e.message_id))
    return eps


def variants_for_movie(movie_key: str) -> List[HubItem]:
    """All uploads of a given movie, sorted newest first."""
    vs = [it for it in _items.values() if it.movie_key == movie_key]
    vs.sort(key=lambda v: v.message_id, reverse=True)
    return vs


def shelves(per_shelf: int = 12) -> List[dict]:
    """Curated horizontal rows for the hub's no-filter landing view.

    Each shelf returns the top ``per_shelf`` cards in a category, ordered
    newest-first. Shelves are computed off the same ``_items`` snapshot —
    items can legitimately appear on more than one (a recent series shows
    up in both "Recently added" and "Series") — so the landing page reads
    like Netflix's "based on what you have" rather than a strict
    partition.

    Returned shape::

        [
            {"name": "Recently added", "items": [HubItem|SeriesGroup|MovieGroup, ...]},
            ...
        ]
    """
    # --- bucket every item by what it is ---
    series_buckets: dict = {}
    movie_buckets: dict = {}
    singles: List[HubItem] = []
    for it in _items.values():
        if it.series_key:
            series_buckets.setdefault(it.series_key, []).append(it)
        elif it.movie_key:
            movie_buckets.setdefault(it.movie_key, []).append(it)
        else:
            singles.append(it)

    series_groups = [_build_series_group(eps) for eps in series_buckets.values()]
    movie_groups: List = []
    standalone_movies: List[HubItem] = []
    for variants in movie_buckets.values():
        if len(variants) >= 2:
            movie_groups.append(_build_movie_group(variants))
        else:
            standalone_movies.append(variants[0])

    all_movies = movie_groups + standalone_movies + singles

    # --- shape into shelves ---
    def newest(items, key=lambda c: _card_message_id(c)):
        return sorted(items, key=lambda c: -key(c))[:per_shelf]

    out: List[dict] = []

    recent_all = list(_items.values())
    if recent_all:
        out.append({
            "name": "Recently added",
            "items": newest(recent_all),
        })

    if series_groups:
        out.append({
            "name": "Series",
            "items": newest(series_groups, key=lambda s: s.latest_message_id),
        })

    if all_movies:
        out.append({
            "name": "Movies",
            "items": newest(all_movies),
        })

    by_quality_1080 = [
        it for it in _items.values()
        if it.quality in ("1080p", "4K") and not it.series_key
    ]
    if by_quality_1080:
        out.append({
            "name": "1080p &amp; up",
            "items": newest(by_quality_1080),
        })

    return out


def _apply_tmdb_to_item(item: HubItem, hit: "tmdb.TMDBHit") -> None:
    """Merge TMDB data into an existing HubItem. Replaces the title/year
    with TMDB's canonical pair when confidence was high enough for the
    hit to be returned at all, then recomputes movie_key/series_key from
    the new title so deduplication kicks in.

    Genres are merged into the tag set so the existing tag filter and
    tag-cloud surface them, without dropping any user-set tags.
    """
    item.tmdb_id = hit.tmdb_id
    item.tmdb_kind = hit.kind
    item.imdb_id = hit.imdb_id
    item.poster_path = hit.poster_path
    item.backdrop_path = hit.backdrop_path
    item.overview = hit.overview
    item.tmdb_genres = list(hit.genres)
    item.enriched_at = time.time()

    if hit.title:
        item.title = hit.title
    if hit.year:
        item.year = hit.year

    # Recompute grouping keys against the canonical title.
    sm = series_parse.parse(item.file_name) or series_parse.parse(item.title)
    if sm:
        item.series_key = sm.key
        item.series_title = hit.title if hit.kind == "tv" and hit.title else sm.title
        item.season = sm.season
        item.episode = sm.episode
        item.movie_key = ""
    else:
        item.series_key = ""
        item.series_title = ""
        item.season = None
        item.episode = None
        item.movie_key = compute_movie_key(item.title, item.year, item.file_name)

    # Merge TMDB genres into tags without duplicating existing entries.
    existing = set(item.tags)
    for g in hit.genres:
        slug = re.sub(r"[^a-z0-9]+", "-", g.lower()).strip("-")
        if slug and slug not in existing:
            item.tags.append(slug)
            existing.add(slug)


async def enrich_one(message_id: int) -> bool:
    """Look up and apply TMDB enrichment for a single catalogue entry.

    Returns True if the entry got enriched, False otherwise. Idempotent:
    re-running on an already-enriched item refreshes its data.
    """
    if not tmdb.is_configured():
        return False
    item = _items.get(message_id)
    if item is None:
        return False

    # Use the parsed series title for TV episodes so we look up the show,
    # not the episode-specific filename.
    if item.series_key and item.series_title:
        hit = await tmdb.lookup_series(item.series_title, item.year)
    else:
        # Movie path: strip any leading channel tag (the dedup module
        # already does this for keys, but the raw title is what TMDB
        # search sees).
        title = item.title or item.file_name
        hit = await tmdb.lookup_movie(title, item.year)
        if hit is None:
            # Some episodes get parsed without SxxEyy in the title (e.g.
            # standalone uploads of a TV special). Try a TV lookup as a
            # fallback.
            hit = await tmdb.lookup_series(title, item.year)

    if hit is None:
        async with _lock:
            # Record the attempt so enrich_all doesn't keep retrying every
            # unenriched item on every run.
            item.enriched_at = time.time()
            _persist_unlocked()
        return False

    async with _lock:
        _apply_tmdb_to_item(item, hit)
        _persist_unlocked()
    return True


async def enrich_all(force: bool = False) -> dict:
    """Background-enrich every entry that hasn't been enriched yet.

    With ``force=True`` re-enriches everything, including entries that
    already have TMDB data. Episodes of the same series share TMDB calls
    via the tmdb module's in-process cache so a long-running show makes
    only one network round trip per enrichment pass.
    """
    if not tmdb.is_configured():
        return {"total": len(_items), "enriched": 0, "skipped_no_api_key": True}

    ids = list(_items.keys())
    enriched = 0
    failed = 0
    for mid in ids:
        item = _items.get(mid)
        if item is None:
            continue
        if not force and item.tmdb_id:
            continue
        ok = await enrich_one(mid)
        if ok:
            enriched += 1
        else:
            failed += 1
        # Yield to the loop so the web server stays responsive during a
        # bulk enrich.
        await asyncio.sleep(0)
    return {"total": len(_items), "enriched": enriched, "failed": failed}


async def reindex_all() -> dict:
    """Re-derive series_key, season, episode, movie_key, quality on every
    existing HubItem from its current file_name + title.

    Used by /admin/reindex to backfill grouping logic over the catalogue
    after the series/dedup detectors are improved. No Telegram round-trips
    needed — the cached file_name and parsed caption are enough.

    Returns a count summary so the admin UI can show what changed.
    """
    changed_series = 0
    changed_movie = 0
    changed_quality = 0
    async with _lock:
        for it in _items.values():
            sm = series_parse.parse(it.file_name) or series_parse.parse(it.title)
            new_series_key = sm.key if sm else ""
            new_series_title = sm.title if sm else ""
            new_season = sm.season if sm else None
            new_episode = sm.episode if sm else None
            new_movie_key = (
                ""
                if sm
                else compute_movie_key(it.title or it.file_name, it.year, it.file_name)
            )
            new_quality = _extract_quality(it.title, it.file_name, it.description)

            if (it.series_key, it.season, it.episode) != (new_series_key, new_season, new_episode):
                changed_series += 1
            if it.movie_key != new_movie_key:
                changed_movie += 1
            if it.quality != new_quality:
                changed_quality += 1

            it.series_key = new_series_key
            it.series_title = new_series_title
            it.season = new_season
            it.episode = new_episode
            it.movie_key = new_movie_key
            it.quality = new_quality
        _persist_unlocked()
    return {
        "total": len(_items),
        "series_changed": changed_series,
        "movie_changed": changed_movie,
        "quality_changed": changed_quality,
    }


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
