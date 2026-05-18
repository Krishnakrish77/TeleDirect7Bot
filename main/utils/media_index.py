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
from main.utils.index_entry import (
    IndexEntry,
    parse,
    title_from_filename,
    year_from_filename,
)
from main.utils import series as series_parse
from main.utils.dedup import movie_key as compute_movie_key
from main.utils.subtitles import stem_for_pairing
from main.utils import tmdb
from main.utils import state_doc


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
# Message id of the most recent snapshot doc uploaded to BIN_CHANNEL.
# Persisted so subsequent saves can delete the prior snapshot even when
# pinning silently failed (bot may lack pin permission).
_snapshot_msg_id: int = 0

# In-memory progress state for the two long-running pipelines (seed and
# enrich). The admin UI polls /admin/status which serialises both.
_seed_state: dict = {"running": False, "scanned": 0, "total": 0,
                     "indexed": 0, "started_at": 0.0, "finished_at": 0.0}
_enrich_state: dict = {"running": False, "done": 0, "total": 0,
                       "enriched": 0, "failed": 0,
                       "started_at": 0.0, "finished_at": 0.0,
                       "last_title": ""}
_reindex_state: dict = {"running": False, "done": 0, "total": 0,
                        "series_changed": 0, "movie_changed": 0,
                        "quality_changed": 0,
                        "started_at": 0.0, "finished_at": 0.0}


def seed_state() -> dict:
    return dict(_seed_state)


def enrichment_state() -> dict:
    return dict(_enrich_state)


def reindex_state() -> dict:
    return dict(_reindex_state)


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
        parsed = IndexEntry(
            title=title_from_filename(file_name),
            year=year_from_filename(file_name),
        )
    elif parsed.year is None:
        # Caption parser found a title but no year; backfill from the
        # filename so the year filter and movie_key both work.
        parsed.year = year_from_filename(file_name)
    # Try filename first (release names follow conventions), fall back to the
    # parsed caption title for hand-written entries.
    sm = series_parse.parse(file_name) or series_parse.parse(parsed.title)
    # If the caption preserved a TMDB tv id but the filename has no
    # SxxEyy pattern, still treat the upload as a series so a re-seed
    # doesn't fragment grouped titles back into the movie-variant pool.
    is_tv_by_id = parsed.tmdb_kind == "tv" and parsed.tmdb_id

    if sm:
        # series.parse keeps any leading channel-prefix the filename had
        # (``rodeo When Life Gives You Tangerines``). Feed the un-humanised
        # raw_title — clean_for_search detects channel handles by leading
        # lowercase, which the humanise step would have erased.
        from main.utils.dedup import clean_for_search
        cleaned_series_title = clean_for_search(sm.raw_title or sm.title) or sm.title
        series_key = series_parse.slugify(cleaned_series_title) or sm.key
        series_title = cleaned_series_title
        season = sm.season
        episode = sm.episode
        movie_key = ""
    elif is_tv_by_id and parsed.title:
        # Caption-derived title can carry a channel prefix the original
        # write-back captured (``rodeo When Life Gives You Tangerines``).
        # Clean it so the series_title we restore is presentable and
        # collapses with the cleaned reindex output rather than living
        # as a polluted duplicate.
        from main.utils.dedup import clean_for_search
        cleaned_tv_title = clean_for_search(parsed.title) or parsed.title
        series_key = series_parse.slugify(cleaned_tv_title)
        series_title = cleaned_tv_title
        season = None
        episode = None
        movie_key = ""
    else:
        series_key = ""
        series_title = ""
        season = None
        episode = None
        movie_key = compute_movie_key(parsed.title or file_name, parsed.year, file_name)

    # When the caption carries a TMDB id, the description we just parsed
    # is the TMDB overview written back by an earlier enrichment. Promote
    # it to the overview field so a re-seed-from-BIN keeps the watch
    # page's overview block populated without needing to re-hit TMDB.
    overview = parsed.description if parsed.tmdb_id else ""

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
        series_key=series_key,
        series_title=series_title,
        season=season,
        episode=episode,
        movie_key=movie_key,
        # Provider IDs + artwork paths round-trip through the caption
        # so a re-seed recovers them without needing to hit TMDB again.
        tmdb_id=parsed.tmdb_id,
        tmdb_kind=parsed.tmdb_kind,
        imdb_id=parsed.imdb_id,
        poster_path=parsed.poster_path,
        backdrop_path=parsed.backdrop_path,
        overview=overview,
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
            "snapshot_msg_id": _snapshot_msg_id,
            "items": [_to_serializable(it) for it in _items.values()],
        }
        with _INDEX_FILE.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        logging.debug("media_index: persist failed (non-fatal)", exc_info=True)


def _load() -> None:
    global _latest_seen_id, _snapshot_msg_id
    if not _INDEX_FILE.exists():
        return
    try:
        with _INDEX_FILE.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        # Backwards-compat: older builds wrote a bare list of items.
        if isinstance(raw, list):
            data = raw
            persisted_latest = 0
            persisted_snapshot = 0
        else:
            data = raw.get("items") or []
            persisted_latest = int(raw.get("latest_seen_id") or 0)
            persisted_snapshot = int(raw.get("snapshot_msg_id") or 0)
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
        _snapshot_msg_id = persisted_snapshot
        logging.info(
            "media_index: loaded %d entries from %s (latest_seen_id=%d, snapshot=%d)",
            len(_items), _INDEX_FILE, _latest_seen_id, _snapshot_msg_id,
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

    # If /tmp was empty (cold container restart on Koyeb) try the
    # Telegram-pinned snapshot before walking BIN. The snapshot carries
    # full TMDB enrichment state — poster paths, tmdb_ids, the lot — so
    # a redeploy doesn't lose hard-won enrichment data.
    if not _items:
        try:
            await restore_from_telegram(bot)
        except Exception:
            logging.exception(
                "media_index: telegram-snapshot restore failed (non-fatal)"
            )

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

    floor = max(1, latest_id - _SEED_DEPTH)
    high = latest_id
    total_to_scan = high - floor + 1
    _seed_state.update(
        running=True,
        scanned=0,
        total=total_to_scan,
        indexed=len(_items),
        started_at=time.time(),
        finished_at=0.0,
    )
    logging.info(
        "media_index: seeding %d…%d (depth=%d)", high, floor, _SEED_DEPTH
    )
    try:
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
                    new_item = _item_from_message(m)
                    if new_item is None:
                        continue
                    existing = _items.get(new_item.message_id)
                    if existing is not None:
                        # Merge with prior snapshot/in-memory state.
                        #
                        # Snapshot always wins for fields that aren't
                        # round-tripped through the caption: TMDB
                        # genres, attached subtitle sidecars, the
                        # enriched_at timestamp.
                        new_item.tmdb_genres = (
                            existing.tmdb_genres or new_item.tmdb_genres
                        )
                        new_item.subtitles = (
                            existing.subtitles or new_item.subtitles
                        )
                        new_item.enriched_at = (
                            existing.enriched_at or new_item.enriched_at
                        )
                        # When the caption-write succeeded the caption
                        # carries the TMDB id, title, year, poster, etc.
                        # — let new_item (the parse result) win in that
                        # case so manual admin edits land too.
                        #
                        # When the snapshot says the row was enriched
                        # but the parsed caption did NOT recover a
                        # tmdb_id, the caption write-back must have
                        # been rejected (uneditable bin). The caption
                        # is then the raw upload text and new_item's
                        # title/year/etc. are filename-derived and
                        # worse than what the snapshot already has.
                        # Preserve every enrichment-derived field.
                        caption_lost_enrichment = (
                            existing.tmdb_id and not new_item.tmdb_id
                        )
                        if caption_lost_enrichment:
                            new_item.title = existing.title or new_item.title
                            new_item.year = existing.year or new_item.year
                            new_item.tmdb_id = existing.tmdb_id
                            new_item.tmdb_kind = existing.tmdb_kind
                            new_item.imdb_id = existing.imdb_id or new_item.imdb_id
                            new_item.poster_path = (
                                existing.poster_path or new_item.poster_path
                            )
                            new_item.backdrop_path = (
                                existing.backdrop_path or new_item.backdrop_path
                            )
                            # series_title / series_key are derived from
                            # the (now-restored) title for TV rows.
                            if existing.tmdb_kind == "tv":
                                new_item.series_title = (
                                    existing.series_title or new_item.series_title
                                )
                                new_item.series_key = (
                                    existing.series_key or new_item.series_key
                                )
                            if existing.movie_key and not new_item.movie_key:
                                new_item.movie_key = existing.movie_key
                        if existing.tags and not new_item.tags:
                            new_item.tags = existing.tags
                        if existing.description and not new_item.description:
                            new_item.description = existing.description
                        if existing.overview and not new_item.overview:
                            new_item.overview = existing.overview
                    _items[new_item.message_id] = new_item
                _persist_unlocked()
            _seed_state["scanned"] += len(ids)
            _seed_state["indexed"] = len(_items)
            high -= _FETCH_BATCH
            # Yield to the loop so a long seed doesn't starve other work.
            await asyncio.sleep(0)
    finally:
        _seeded = True
        _seed_state["running"] = False
        _seed_state["finished_at"] = time.time()
    logging.info(
        "media_index: seed done — scanned %d ids, %d entries indexed",
        _seed_state["scanned"], len(_items),
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


def _haystack(item: HubItem) -> str:
    """Searchable text pulled from every field a user might query against.

    Lowercased once at search time — cheap on a single-thousand-item
    catalogue and avoids paying for it per substring comparison.
    """
    return " ".join([
        item.title.lower(),
        item.series_title.lower(),
        item.description.lower(),
        item.overview.lower(),
        item.file_name.lower(),
        item.imdb_id.lower(),
        " ".join(item.tags),
        " ".join(g.lower() for g in item.tmdb_genres),
    ])


def _fuzzy_score(q: str, item: HubItem) -> float:
    """Word-level fuzzy ratio against the item's most-display-relevant
    title fields. Returns 0 if no token clears the 0.75 threshold so
    misspellings stay forgiving but unrelated titles don't leak in.
    """
    import difflib
    candidates = [item.title.lower(), item.series_title.lower()]
    best = 0.0
    for cand in candidates:
        if not cand:
            continue
        # Whole-string ratio first — catches "samsram" vs "samsaram".
        score = difflib.SequenceMatcher(None, q, cand).ratio()
        if score > best:
            best = score
        # Then token-level — catches "minsaram" in "samsaram adhu minsaram".
        for word in cand.split():
            if len(word) < 3:
                continue
            score = difflib.SequenceMatcher(None, q, word).ratio()
            if score > best:
                best = score
    return best if best >= 0.75 else 0.0


def _matches(item: HubItem, q: str, year: Optional[int], quality: str, tag: str) -> bool:
    if year is not None and item.year != year:
        return False
    if quality and item.quality.lower() != quality.lower():
        return False
    if tag and tag.lstrip("#").lower() not in item.tags:
        return False
    if q:
        ql = q.lower().lstrip("#")
        if ql in _haystack(item):
            return True
        # Fuzzy fallback — only for queries 3+ chars so a stray keystroke
        # doesn't sweep in the whole catalogue.
        if len(ql) >= 3 and _fuzzy_score(ql, item) > 0:
            return True
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
    view: str = "",
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

    if view == "series":
        # Only show series cards in this view.
        grouped_movies = []
        standalone = []
    elif view == "movies":
        # Only movies and standalone non-series items.
        grouped_series = []

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


def suggest(q: str, limit: int = 8) -> List[dict]:
    """Lightweight search for the nav dropdown.

    Walks the catalogue once, scoring each item; returns the top N as
    plain dicts suitable for JSON response (small enough to keep the
    HTTP round-trip snappy). Collapses series episodes into one row per
    series_key, and movie variants into one row per movie_key, so a
    show with 10 episodes doesn't drown out unrelated matches.
    """
    if not q or len(q.strip()) < 1:
        return []
    ql = q.strip().lower().lstrip("#")

    scored: List = []
    for it in _items.values():
        hay = _haystack(it)
        score = 0.0
        if ql in it.title.lower() or ql in it.series_title.lower():
            score = 1.0  # title-substring is the strongest signal
        elif ql in hay:
            score = 0.6  # substring elsewhere (description, genres, file)
        elif len(ql) >= 3:
            fuzzy = _fuzzy_score(ql, it)
            if fuzzy > 0:
                score = fuzzy * 0.8  # fuzzy under exact substring
        if score > 0:
            scored.append((score, it))

    if not scored:
        return []

    # Highest score first, ties broken by recency.
    scored.sort(key=lambda x: (-x[0], -x[1].message_id))

    # Collapse by group so a multi-episode series shows once.
    seen_series: set = set()
    seen_movie: set = set()
    suggestions: List[dict] = []
    for score, it in scored:
        if it.series_key:
            if it.series_key in seen_series:
                continue
            seen_series.add(it.series_key)
            url = f"/series/{it.series_key}"
            title = it.series_title or it.title
            kind = "series"
        elif it.movie_key:
            if it.movie_key in seen_movie:
                continue
            seen_movie.add(it.movie_key)
            # MovieGroup page only exists when there are 2+ variants; we
            # can't tell from a single item here without scanning, so
            # always link to /watch and let users click into variants
            # from there if needed.
            url = f"/watch/{it.secure_hash}{it.message_id}"
            title = it.title
            kind = "movie"
        else:
            url = f"/watch/{it.secure_hash}{it.message_id}"
            title = it.title
            kind = "movie"
        suggestions.append({
            "title": title,
            "year": it.year,
            "kind": kind,
            "url": url,
            "poster_path": it.poster_path,
            "secure_hash": it.secure_hash,
            "message_id": it.message_id,
        })
        if len(suggestions) >= limit:
            break

    return suggestions


def variants_for_movie(movie_key: str) -> List[HubItem]:
    """All uploads of a given movie, sorted newest first."""
    vs = [it for it in _items.values() if it.movie_key == movie_key]
    vs.sort(key=lambda v: v.message_id, reverse=True)
    return vs


def _dedup_by_group(items: List[HubItem]) -> List[HubItem]:
    """Keep at most one item per tmdb_id / series_key / movie_key,
    preserving input order (which callers expect to be newest-first).

    tmdb_id is checked first because it's the strongest signal — two
    uploads enriched from the same TMDB record are definitely the same
    title, even when the source filenames are too different for the
    local heuristics (clean_for_search, series.parse) to collapse them
    to a common key.
    """
    seen_tmdb: set = set()
    seen_series: set = set()
    seen_movie: set = set()
    out: List[HubItem] = []
    for it in items:
        if it.tmdb_id:
            if it.tmdb_id in seen_tmdb:
                continue
            seen_tmdb.add(it.tmdb_id)
        elif it.series_key:
            if it.series_key in seen_series:
                continue
            seen_series.add(it.series_key)
        elif it.movie_key:
            if it.movie_key in seen_movie:
                continue
            seen_movie.add(it.movie_key)
        out.append(it)
    return out


def pick_heroes(limit: int = 6) -> List[HubItem]:
    """Choose featured items for the landing-page hero carousel.

    Tiered fallback so the hero never goes dark:
    1. Most recent ``limit`` items with backdrop + overview (best look).
    2. Items with overview only (poster as bg; happens when TMDB lacks
       a backdrop, common for older / regional titles).
    3. Any enriched item (poster + canonical title, no copy).
    4. Most recent items in the catalogue, period — at least the hero
       has *something* on a cold start before enrichment lands.

    Each tier is deduplicated by series_key / movie_key so a show with
    several recent episodes shows up once, not three times.
    """
    by_recent = sorted(_items.values(), key=lambda it: -it.message_id)

    tier1 = _dedup_by_group([it for it in by_recent
                             if it.backdrop_path and it.overview])
    if len(tier1) >= 3:
        return tier1[:limit]

    pool_ids = {id(it) for it in tier1}
    tier2 = _dedup_by_group([
        it for it in by_recent
        if it.overview and id(it) not in pool_ids
    ])
    pool = _dedup_by_group(tier1 + tier2)
    if len(pool) >= 3:
        return pool[:limit]

    pool_ids = {id(it) for it in pool}
    tier3 = _dedup_by_group([
        it for it in by_recent
        if it.tmdb_id and id(it) not in pool_ids
    ])
    pool = _dedup_by_group(pool + tier3)
    if len(pool) >= 3:
        return pool[:limit]

    pool_ids = {id(it) for it in pool}
    tier4 = _dedup_by_group([it for it in by_recent if id(it) not in pool_ids])
    return _dedup_by_group(pool + tier4)[:limit]


def pick_hero() -> Optional[HubItem]:
    """Backwards-compat single-pick for older callers."""
    heroes = pick_heroes(limit=1)
    return heroes[0] if heroes else None


def shelves(per_shelf: int = 25) -> List[dict]:
    """Curated horizontal rows for the hub's no-filter landing view.

    Recent first, then series, then movies, then up to three genre shelves
    derived from TMDB enrichment. Items can appear on multiple rows
    (a recent series shows in both "Recently added" and "Series") so the
    landing page reads like Netflix's "based on what you have" rather
    than a strict partition.

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
    all_cards: List = series_groups + movie_groups + standalone_movies + singles

    def newest(items, key=lambda c: _card_message_id(c)):
        return sorted(items, key=lambda c: -key(c))[:per_shelf]

    out: List[dict] = []

    if all_cards:
        # Recently added isn't filterable to a smaller superset; "see all"
        # for it is just the flat newest-first grid (?sort=newest).
        out.append({
            "name": "Recently added",
            "items": newest(all_cards),
            "link": None,
            "total": len(all_cards),
        })
    if series_groups:
        out.append({
            "name": "Series",
            "items": newest(series_groups, key=lambda s: s.latest_message_id),
            "link": "/?view=series",
            "total": len(series_groups),
        })
    if all_movies:
        out.append({
            "name": "Movies",
            "items": newest(all_movies),
            "link": "/?view=movies",
            "total": len(all_movies),
        })

    # --- genre shelves from TMDB enrichment ---
    # Map each genre slug → list of items that carry it. Pick the top 3
    # most-populated genres for shelf rows so the page doesn't sprawl.
    by_genre: dict = {}
    for it in _items.values():
        if not it.tmdb_genres:
            continue
        # We promote SeriesGroup/MovieGroup cards into genre rows too, so
        # the per-item iteration needs to deduplicate by group.
        for g in it.tmdb_genres:
            by_genre.setdefault(g, []).append(it)

    if by_genre:
        # For each genre, walk the items and emit the right card (group
        # for series/movie variants, plain HubItem otherwise) — same
        # promotion logic as the all_cards bucketing above.
        genre_rows = sorted(by_genre.items(), key=lambda kv: -len(kv[1]))[:3]
        for genre, members in genre_rows:
            seen_series: set = set()
            seen_movie: set = set()
            row_cards: List = []
            for it in sorted(members, key=lambda x: -x.message_id):
                if it.series_key:
                    if it.series_key in seen_series:
                        continue
                    eps = series_buckets.get(it.series_key, [it])
                    row_cards.append(_build_series_group(eps))
                    seen_series.add(it.series_key)
                elif it.movie_key:
                    if it.movie_key in seen_movie:
                        continue
                    variants = movie_buckets.get(it.movie_key, [it])
                    if len(variants) >= 2:
                        row_cards.append(_build_movie_group(variants))
                    else:
                        row_cards.append(variants[0])
                    seen_movie.add(it.movie_key)
                else:
                    row_cards.append(it)
                if len(row_cards) >= per_shelf:
                    break
            if row_cards:
                # Genre shelves link into the tag-filter view so "see all
                # Drama" lands on the existing /?tag=drama page.
                slug = re.sub(r"[^a-z0-9]+", "-", genre.lower()).strip("-")
                out.append({
                    "name": genre,
                    "items": row_cards,
                    "link": f"/?tag={slug}" if slug else None,
                    "total": len(members),
                })

    return out


async def snapshot_to_telegram(bot) -> Optional[int]:
    """Upload a JSON snapshot of the catalogue to BIN_CHANNEL.

    Called at the end of admin actions that meaningfully change state
    (enrich, reindex). The pinned snapshot is what cold-start restarts
    read from — /tmp/media_index.json is just a hot cache on top.
    Failures don't block the caller; the catalogue is still in memory
    and the BIN captions remain a fallback recovery path.

    Remembers the message id of the new snapshot so the next save can
    delete the previous copy even when pinning is unavailable.
    """
    global _snapshot_msg_id
    try:
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "latest_seen_id": _latest_seen_id,
            "items": [_to_serializable(it) for it in _items.values()],
        }
        new_id = await state_doc.save(bot, payload, prev_id_hint=_snapshot_msg_id)
        if new_id:
            async with _lock:
                _snapshot_msg_id = new_id
                _persist_unlocked()
        return new_id
    except Exception:
        logging.exception("media_index: snapshot_to_telegram failed (non-fatal)")
        return None


async def restore_from_telegram(bot) -> bool:
    """Try to repopulate ``_items`` from the pinned Telegram snapshot.

    Returns True if the snapshot existed and was loaded. Called early in
    ``seed()`` when ``/tmp`` is empty — recovers full enrichment state
    (poster paths, tmdb_ids, etc.) without re-hitting TMDB.
    """
    global _latest_seen_id
    payload = await state_doc.load(bot)
    if payload is None:
        return False
    items_data = payload.get("items") or []
    persisted_latest = int(payload.get("latest_seen_id") or 0)
    loaded = 0
    async with _lock:
        for d in items_data:
            try:
                item = _from_serializable(d)
                _items[item.message_id] = item
                loaded += 1
            except Exception:
                continue
        local_max = max((it.message_id for it in _items.values()), default=0)
        _latest_seen_id = max(_latest_seen_id, persisted_latest, local_max)
        _persist_unlocked()
    logging.info(
        "media_index: restored %d entries from Telegram snapshot (latest=%d)",
        loaded, _latest_seen_id,
    )
    return loaded > 0


async def persist_canonical_to_bin(bot, message_id: int) -> bool:
    """Edit a BIN_CHANNEL message's caption to reflect the HubItem's
    current canonical state.

    This is what makes the catalogue scalable across container restarts:
    /tmp/media_index.json is ephemeral on Koyeb, but the Telegram channel
    isn't. A cold restart re-seeds from BIN, parses cleaned captions for
    titles and years, then re-enriches via TMDB to recover poster URLs
    etc. The hub never falls back to "[MS] F1 The Movie 2025 720p HDRip"
    state.

    Handles FloodWait by sleeping and retrying; MessageNotModified counts
    as success (the caption is already what we want). When Telegram
    returns MessageIdInvalid the in-memory entry is stale (the source
    message was deleted on the channel) — we drop it from the catalogue
    so subsequent seeds don't repeat the work. Returns True on
    successful edit (or no-op), False on hard failure.
    """
    from pyrogram.errors import FloodWait, MessageNotModified
    from pyrogram.errors.exceptions.bad_request_400 import (
        MessageIdInvalid, ChannelInvalid,
    )
    # 403 cases where the bot is allowed to read the message but not edit
    # its caption. Common when the BIN entry was forwarded from an
    # inline-bot source (quote bots, wallpaper bots) or originally sent
    # by a different bot. The entry is still streamable — we just skip
    # the write-back rather than tearing it down.
    _UNEDITABLE_EXC: tuple = ()
    try:
        from pyrogram.errors.exceptions.forbidden_403 import (
            InlineBotRequired,
        )
        _UNEDITABLE_EXC = _UNEDITABLE_EXC + (InlineBotRequired,)
    except ImportError:
        pass
    try:
        from pyrogram.errors.exceptions.forbidden_403 import (
            MessageAuthorRequired,
        )
        _UNEDITABLE_EXC = _UNEDITABLE_EXC + (MessageAuthorRequired,)
    except ImportError:
        pass
    from main.utils.index_entry import render
    from main.vars import Var

    item = _items.get(message_id)
    if item is None:
        return False

    entry = IndexEntry(
        title=item.title or "(untitled)",
        year=item.year,
        description=item.overview or item.description or "",
        tags=list(item.tags),
        tmdb_id=item.tmdb_id,
        tmdb_kind=item.tmdb_kind,
        imdb_id=item.imdb_id,
        poster_path=item.poster_path,
        backdrop_path=item.backdrop_path,
    )
    caption = render(entry)
    try:
        await bot.edit_message_caption(
            chat_id=Var.BIN_CHANNEL,
            message_id=message_id,
            caption=caption,
        )
        return True
    except MessageNotModified:
        return True
    except MessageIdInvalid:
        # MessageIdInvalid is overloaded: the message is either truly
        # gone OR the bot doesn't own it (admin posted directly to the
        # channel rather than forwarded through the bot). Probe first
        # so we don't shred the catalogue every time enrichment writes
        # back to a non-bot-owned video.
        try:
            probe = await bot.get_messages(Var.BIN_CHANNEL, message_id)
            still_exists = probe is not None and not getattr(probe, "empty", False)
        except Exception:
            still_exists = False
        if still_exists:
            logging.info(
                "media_index: bin:%d not bot-owned; in-memory enrichment kept",
                message_id,
            )
            return True
        logging.info(
            "media_index: dropping stale entry bin:%d (genuinely missing)",
            message_id,
        )
        await remove(message_id)
        return False
    except _UNEDITABLE_EXC as exc:
        # We can't edit this particular caption but the underlying media
        # still streams fine — keep the entry, skip the write-back.
        logging.info(
            "media_index: bin:%d caption is read-only (%s); keeping entry",
            message_id, exc.__class__.__name__,
        )
        return True
    except FloodWait as e:
        wait = getattr(e, "value", None) or getattr(e, "x", 0) or 1
        logging.warning("FloodWait writing caption bin:%d — sleeping %ss",
                        message_id, wait)
        await asyncio.sleep(wait)
        try:
            await bot.edit_message_caption(
                chat_id=Var.BIN_CHANNEL,
                message_id=message_id,
                caption=caption,
            )
            return True
        except MessageIdInvalid:
            # Same probe-before-drop logic on the retry path.
            try:
                probe = await bot.get_messages(Var.BIN_CHANNEL, message_id)
                still_exists = probe is not None and not getattr(probe, "empty", False)
            except Exception:
                still_exists = False
            if still_exists:
                return True
            await remove(message_id)
            return False
        except Exception:
            logging.exception("Caption write failed after FloodWait for bin:%d",
                              message_id)
            return False
    except ChannelInvalid:
        # BIN_CHANNEL itself unreachable — abort, don't drop the entry.
        logging.exception("Caption write failed: BIN_CHANNEL invalid")
        return False
    except Exception:
        logging.exception("Caption write failed for bin:%d", message_id)
        return False


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

    # For TV episodes we deliberately preserve the per-episode title that
    # was parsed from the filename (e.g. "Tangerines.S01E03.WEBRip") —
    # otherwise every episode card ends up showing the show name and
    # they're indistinguishable. Movies and standalone uploads do
    # take TMDB's canonical title.
    if hit.kind != "tv" and hit.title:
        item.title = hit.title
    if hit.year and hit.kind != "tv":
        item.year = hit.year

    # Recompute grouping keys against the canonical title.
    sm = series_parse.parse(item.file_name) or series_parse.parse(item.title)
    if sm:
        # Filename or title carries an SxxEyy / 1x03 / Season N pattern.
        item.series_key = sm.key
        item.series_title = hit.title if hit.kind == "tv" and hit.title else sm.title
        item.season = sm.season
        item.episode = sm.episode
        item.movie_key = ""
    elif hit.kind == "tv" and hit.title:
        # TMDB says the title is a TV show, but the source filename lacks
        # any SxxEyy pattern. Still a series — group by TMDB title so
        # multiple uploads collapse into one card. Run the loose episode
        # extractor on the filename + title to recover an episode number
        # when explicit "Ep13" / "E13" / trailing-integer hints exist;
        # default to S1 when one or more siblings are SxxEyy-labelled.
        item.series_key = series_parse.slugify(hit.title)
        item.series_title = hit.title
        inferred_ep = (
            series_parse.infer_episode_loose(item.file_name)
            or series_parse.infer_episode_loose(item.title)
        )
        if inferred_ep is not None:
            item.season = 1  # default; a smarter probe could read TMDB
            item.episode = inferred_ep
        else:
            item.season = None
            item.episode = None
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


async def enrich_one(message_id: int, bot=None) -> bool:
    """Look up and apply TMDB enrichment for a single catalogue entry.

    Returns True if the entry got enriched, False otherwise. Idempotent:
    re-running on an already-enriched item refreshes its data.

    The lookup tries multiple cleaned title forms before giving up:
    1. ``clean_for_search`` on the catalogue title (strips channel tags,
       release noise, year suffix)
    2. ``clean_for_search`` on the file_name (fallback when the title got
       degraded by an earlier indexer)
    3. The raw title as TMDB sees it

    When ``bot`` is provided, the enriched canonical metadata is also
    written back to the BIN_CHANNEL message caption — making the Telegram
    channel itself the durable source of truth, so a /tmp wipe doesn't
    lose hard-won enrichment data.
    """
    from main.utils.dedup import clean_for_search

    if not tmdb.is_configured():
        return False
    item = _items.get(message_id)
    if item is None:
        return False

    if item.series_key and item.series_title:
        # TV: lookup the show. series_title is already clean per series.parse.
        hit = await tmdb.lookup_series(item.series_title, item.year)
    else:
        # Movie: try the cleaned title forms. The earlier 'drop leading
        # words' fallback would generate generic variants like "The
        # Movie" / "Movie" which TMDB happily matched against random
        # films (Plankton: The Movie 2025 collided with F1 The Movie
        # 2025). Now that clean_for_search handles the channel-prefix
        # cases on its own, only the full-base queries are tried; if
        # they all miss, admin edit is the recovery path.
        candidates: list = []
        seen: set = set()
        for raw in (item.title, item.file_name):
            cleaned = clean_for_search(raw, item.file_name)
            if cleaned and cleaned not in seen and len(cleaned) >= 2:
                candidates.append(cleaned)
                seen.add(cleaned)
        if item.title and item.title not in seen and len(item.title) >= 2:
            candidates.append(item.title)

        hit = None
        for q in candidates:
            hit = await tmdb.lookup_movie(q, item.year)
            if hit is not None:
                break
        if hit is None:
            for q in candidates:
                hit = await tmdb.lookup_series(q, item.year)
                if hit is not None:
                    break

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

    if bot is not None:
        # Best-effort caption write-back. A failure here doesn't undo the
        # in-memory enrichment; the next enrich pass will retry.
        await persist_canonical_to_bin(bot, message_id)
        # Refresh the pinned snapshot so this one-off enrichment survives
        # a /tmp wipe even when the caption write-back failed.
        try:
            await snapshot_to_telegram(bot)
        except Exception:
            logging.exception(
                "media_index: post-enrich-one snapshot failed (non-fatal)"
            )
    return True


async def enrich_with_tmdb_id(message_id: int, tmdb_id: int, kind: str,
                              bot=None) -> bool:
    """Apply a specific TMDB record to one HubItem by its TMDB id.

    Used by the admin Edit modal when the operator wants to override
    the title-search result with an exact pick (the search either
    matched the wrong record or didn't match at all). Returns True on
    success, False if TMDB couldn't resolve the id.
    """
    if not tmdb.is_configured():
        return False
    item = _items.get(message_id)
    if item is None:
        return False
    hit = await tmdb.fetch_by_id(tmdb_id, kind)
    if hit is None:
        return False
    async with _lock:
        _apply_tmdb_to_item(item, hit)
        _persist_unlocked()
    if bot is not None:
        await persist_canonical_to_bin(bot, message_id)
        # Refresh the pinned snapshot too. /tmp is ephemeral on Koyeb,
        # and persist_canonical_to_bin is best-effort (the caption
        # write fails for messages the bot doesn't own). The pinned
        # snapshot is the only persistence layer that survives both
        # — without this refresh, restarting the bot loses the manual
        # override the operator just applied.
        try:
            await snapshot_to_telegram(bot)
        except Exception:
            logging.exception(
                "media_index: post-manual-enrich snapshot failed (non-fatal)"
            )
    return True


async def enrich_all(bot=None, force: bool = False) -> dict:
    """Background-enrich every entry that hasn't been enriched yet.

    Populates ``_enrich_state`` as it goes so the admin UI can poll for
    live progress. With ``force=True`` re-enriches everything, including
    entries that already have TMDB data. Episodes of the same series
    share TMDB calls via the tmdb module's in-process cache so a
    long-running show makes only one network round trip per pass.
    """
    if _enrich_state["running"]:
        return {"total": len(_items), "enriched": 0, "already_running": True}

    if not tmdb.is_configured():
        return {"total": len(_items), "enriched": 0, "skipped_no_api_key": True}

    targets = [
        mid for mid, it in _items.items()
        if force or not it.tmdb_id
    ]

    _enrich_state.update(
        running=True,
        done=0,
        total=len(targets),
        enriched=0,
        failed=0,
        started_at=time.time(),
        finished_at=0.0,
        last_title="",
    )

    try:
        for mid in targets:
            item = _items.get(mid)
            if item is None:
                _enrich_state["done"] += 1
                continue
            _enrich_state["last_title"] = item.title or item.file_name or f"bin:{mid}"
            ok = await enrich_one(mid, bot=bot)
            _enrich_state["done"] += 1
            if ok:
                _enrich_state["enriched"] += 1
            else:
                _enrich_state["failed"] += 1
            # Throttle to stay under Telegram's per-channel editMessage
            # rate limit (~30/minute for bots) when we're writing back.
            await asyncio.sleep(2.0 if bot is not None else 0)
    finally:
        _enrich_state["running"] = False
        _enrich_state["finished_at"] = time.time()

    # Snapshot the freshly-enriched state to Telegram so a future cold
    # start picks up the work without re-running TMDB.
    if bot is not None and (_enrich_state["enriched"] or force):
        try:
            await snapshot_to_telegram(bot)
        except Exception:
            logging.exception(
                "media_index: post-enrich snapshot failed (non-fatal)"
            )

    return {
        "total": len(targets),
        "enriched": _enrich_state["enriched"],
        "failed": _enrich_state["failed"],
    }


async def reindex_all(bot=None) -> dict:
    """Re-derive series_key, season, episode, movie_key, quality on every
    existing HubItem from its current file_name + title.

    Used by /admin/reindex to backfill grouping logic over the catalogue
    after the series/dedup detectors are improved. No Telegram round-trips
    needed — the cached file_name and parsed caption are enough.

    Populates ``_reindex_state`` so the admin UI can poll progress; for
    100-ish entries this is sub-second but the state keeps the surface
    consistent with seed and enrich.
    """
    if _reindex_state["running"]:
        return {"already_running": True, "total": len(_items)}

    _reindex_state.update(
        running=True,
        done=0,
        total=len(_items),
        series_changed=0,
        movie_changed=0,
        quality_changed=0,
        started_at=time.time(),
        finished_at=0.0,
    )
    try:
        async with _lock:
            for it in _items.values():
                # Refresh title + year from the filename so improvements
                # to clean_for_search land on existing entries.
                #
                # Refresh in two cases:
                # • Not enriched yet (no tmdb_id) — filename is the only
                #   source of truth for the title.
                # • Enriched as TV — the per-episode title field is
                #   filename-derived (the TMDB show title lives in
                #   series_title separately), so refreshing applies
                #   cleaner improvements without losing canonical data.
                # Movies stay untouched so TMDB's canonical title and
                # manual admin renames aren't clobbered.
                if not it.tmdb_id or it.tmdb_kind == "tv":
                    fresh_title = title_from_filename(it.file_name)
                    if fresh_title and fresh_title != "(untitled)":
                        it.title = fresh_title
                    if it.year is None:
                        it.year = year_from_filename(it.file_name)

                sm = series_parse.parse(it.file_name) or series_parse.parse(it.title)
                if sm:
                    # See _item_from_message — feed un-humanised raw_title
                    # so the lowercase-leading-word heuristic still fires
                    # against channel prefixes like ``rodeo``.
                    from main.utils.dedup import clean_for_search
                    cleaned_series_title = (
                        clean_for_search(sm.raw_title or sm.title) or sm.title
                    )
                    new_series_key = series_parse.slugify(cleaned_series_title) or sm.key
                    new_series_title = cleaned_series_title
                    new_season = sm.season
                    new_episode = sm.episode
                    new_movie_key = ""
                elif it.tmdb_kind == "tv" and it.title:
                    # Same logic as the enrich path: TMDB says TV, no
                    # filename SxxEyy → still a series, collapse by
                    # canonical title. Try the loose-episode extractor
                    # to recover an episode number from the filename.
                    new_series_key = series_parse.slugify(it.title)
                    new_series_title = it.title
                    inferred_ep = (
                        series_parse.infer_episode_loose(it.file_name)
                        or series_parse.infer_episode_loose(it.title)
                    )
                    if inferred_ep is not None:
                        new_season = 1
                        new_episode = inferred_ep
                    else:
                        new_season = None
                        new_episode = None
                    new_movie_key = ""
                else:
                    new_series_key = ""
                    new_series_title = ""
                    new_season = None
                    new_episode = None
                    new_movie_key = compute_movie_key(
                        it.title or it.file_name, it.year, it.file_name,
                    )
                new_quality = _extract_quality(it.title, it.file_name, it.description)

                if (it.series_key, it.season, it.episode) != (new_series_key, new_season, new_episode):
                    _reindex_state["series_changed"] += 1
                if it.movie_key != new_movie_key:
                    _reindex_state["movie_changed"] += 1
                if it.quality != new_quality:
                    _reindex_state["quality_changed"] += 1

                it.series_key = new_series_key
                it.series_title = new_series_title
                it.season = new_season
                it.episode = new_episode
                it.movie_key = new_movie_key
                it.quality = new_quality
                _reindex_state["done"] += 1
            _persist_unlocked()
    finally:
        _reindex_state["running"] = False
        _reindex_state["finished_at"] = time.time()

    if bot is not None:
        try:
            await snapshot_to_telegram(bot)
        except Exception:
            logging.exception(
                "media_index: post-reindex snapshot failed (non-fatal)"
            )
    return {
        "total": _reindex_state["total"],
        "series_changed": _reindex_state["series_changed"],
        "movie_changed": _reindex_state["movie_changed"],
        "quality_changed": _reindex_state["quality_changed"],
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
