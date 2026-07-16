"""
Bot-owned media index for the hub.

Telegram doesn't let bots call messages.getHistory or messages.search. So
we maintain our own in-memory catalogue keyed by BIN_CHANNEL message_id,
populated by two sources:

  1. Live updates: every time an admin-added file lands in BIN_CHANNEL via
     the stream/grab handlers, the auto-indexer adds it here.
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
from dataclasses import replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

from main.utils.file_properties import get_hash
from main.utils.hub_query import AlbumGroup, ExternalSubtitle, HubItem, MovieGroup, SeriesGroup
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
from main.utils import store as _store_module


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
_hash_map: Dict[str, int] = {}  # secure_hash → message_id for O(1) find_by_hash
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
_credits_state: dict = {"running": False, "done": 0, "total": 0,
                        "updated": 0, "failed": 0,
                        "started_at": 0.0, "finished_at": 0.0,
                        "last_title": ""}
_reindex_state: dict = {"running": False, "done": 0, "total": 0,
                        "series_changed": 0, "movie_changed": 0,
                        "quality_changed": 0,
                        "started_at": 0.0, "finished_at": 0.0}
_group_enrich_locks: dict[tuple[str, str], asyncio.Lock] = {}
_group_art_tasks: dict[tuple[str, str], asyncio.Task] = {}
_art_recovery_negative_until: dict[tuple[str, str], float] = {}
_ART_RECOVERY_NEGATIVE_TTL = 60 * 60 * 6

# --- Durable store (Mongo when configured) ---------------------------------
# When ``STORE_BACKEND=mongo``, every mutation also writes-through to
# MongoDB Atlas. The in-memory ``_items`` dict stays authoritative
# during runtime — Mongo is the durable mirror that survives a /tmp
# wipe without needing the pinned-snapshot kludge.
_store: Optional["object"] = None  # store.Store at runtime


def _store_active() -> bool:
    return _store is not None


async def _store_upsert(item: HubItem) -> None:
    if _store is None:
        return
    try:
        await _store.upsert(_to_serializable(item))
    except Exception:
        logging.exception("media_index: store.upsert failed for bin:%d",
                          item.message_id)
    finally:
        _invalidate_hub_caches()


async def _store_remove(message_id: int) -> None:
    if _store is None:
        return
    try:
        await _store.remove(message_id)
    except Exception:
        logging.exception("media_index: store.remove failed for bin:%d",
                          message_id)


async def init_store() -> None:
    """Initialise the configured durable store (if any). Call once at
    bot startup, before seed()."""
    global _store
    candidate = _store_module.from_env()
    if candidate is None:
        logging.info("media_index: durable store DISABLED (JSON fallback)")
        return
    try:
        await candidate.init()
        _store = candidate
    except Exception:
        logging.exception("media_index: store.init() failed, sticking with JSON")
        _store = None


# --- Snapshot debouncer ----------------------------------------------------
# Pinned-snapshot writes are expensive (upload+pin+delete-prior) and noisy
# (each shows up in BIN_CHANNEL until the delete-prior succeeds). Calls
# made within ``_SNAPSHOT_DEBOUNCE`` of each other collapse into one
# actual save once the window settles. Bulk-uploading a TV-show season
# (70+ episodes triggering enrich_one each) used to fan-out into 70+
# snapshot saves — now it's one.
_SNAPSHOT_DEBOUNCE: float = 30.0
_pending_snapshot_task = None


def schedule_snapshot(bot) -> None:
    """Queue a coalesced ``snapshot_to_telegram`` after a quiet window.

    Each call cancels the previous pending task and starts a new one,
    so a rapid burst of mutations produces exactly one snapshot save
    after the burst finishes. Pass ``bot=None`` to disable (we can't
    save without a client).

    When the durable Mongo store is active the pinned-snapshot
    mechanism is redundant — every mutation has already been written
    through to Mongo, so we skip the upload entirely.
    """
    global _pending_snapshot_task
    if _store_active():
        return
    if bot is None:
        return
    try:
        if _pending_snapshot_task is not None and not _pending_snapshot_task.done():
            _pending_snapshot_task.cancel()
    except Exception:
        pass
    try:
        _pending_snapshot_task = asyncio.create_task(_deferred_snapshot(bot))
    except RuntimeError:
        # No running loop (e.g. called from a sync context outside the
        # web/bot event loop). Skip silently — the next event-loop call
        # will reschedule.
        pass


async def _deferred_snapshot(bot) -> None:
    try:
        await asyncio.sleep(_SNAPSHOT_DEBOUNCE)
    except asyncio.CancelledError:
        return
    try:
        await snapshot_to_telegram(bot)
    except Exception:
        logging.exception(
            "media_index: deferred snapshot_to_telegram failed (non-fatal)"
        )


def seed_state() -> dict:
    return dict(_seed_state)


def enrichment_state() -> dict:
    return dict(_enrich_state)


def credits_backfill_state() -> dict:
    return dict(_credits_state)


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
        "episode_end": item.episode_end,
        "intro_start": item.intro_start,
        "intro_end": item.intro_end,
        "recap_start": item.recap_start,
        "recap_end": item.recap_end,
        "chapters": list(item.chapters or []),
        "movie_key": item.movie_key,
        "tmdb_id": item.tmdb_id,
        "tmdb_kind": item.tmdb_kind,
        "imdb_id": item.imdb_id,
        "tmdb_vote_average": item.tmdb_vote_average,
        "tmdb_vote_count": item.tmdb_vote_count,
        "tmdb_vote_checked_at": item.tmdb_vote_checked_at,
        "poster_path": item.poster_path,
        "backdrop_path": item.backdrop_path,
        "overview": item.overview,
        "tmdb_genres": item.tmdb_genres,
        "cast": item.cast,
        "director": item.director,
        "enriched_at": item.enriched_at,
        "video_codec": item.video_codec,
        "pix_fmt": item.pix_fmt,
        "probed_at": item.probed_at,
        "episode_title": item.episode_title,
        "episode_overview": item.episode_overview,
        "episode_still_path": item.episode_still_path,
        "episode_air_date": item.episode_air_date,
        "episode_tmdb_vote_average": item.episode_tmdb_vote_average,
        "episode_tmdb_vote_count": item.episode_tmdb_vote_count,
        "episode_tmdb_vote_checked_at": item.episode_tmdb_vote_checked_at,
        "trailer_key": item.trailer_key,
        "media_kind": item.media_kind,
        "artist": item.artist,
        "album_title": item.album_title,
        "album_key": item.album_key,
        "track_number": item.track_number,
        "audio_codec": item.audio_codec,
        "audio_sample_rate": item.audio_sample_rate,
        "audio_bit_depth": item.audio_bit_depth,
        "admin_locked": list(item.admin_locked or []),
        "hidden": item.hidden,
        "subtitles": [
            {
                "bin_message_id": s.bin_message_id,
                "secure_hash": s.secure_hash,
                "language": s.language,
                "label": s.label,
            }
            for s in item.subtitles
        ],
        "embedded_subtitle_count": item.embedded_subtitle_count,
        "subtitles_probed_at": item.subtitles_probed_at,
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
        file_name=_clean_file_name(d.get("file_name", "") or ""),
        series_key=d.get("series_key", "") or "",
        series_title=d.get("series_title", "") or "",
        season=d.get("season"),
        episode=d.get("episode"),
        episode_end=d.get("episode_end"),
        intro_start=d.get("intro_start"),
        intro_end=d.get("intro_end"),
        recap_start=d.get("recap_start"),
        recap_end=d.get("recap_end"),
        chapters=list(d.get("chapters") or []),
        movie_key=d.get("movie_key", "") or "",
        tmdb_id=d.get("tmdb_id"),
        tmdb_kind=d.get("tmdb_kind", "") or "",
        imdb_id=d.get("imdb_id", "") or "",
        tmdb_vote_average=float(d.get("tmdb_vote_average") or 0),
        tmdb_vote_count=int(d.get("tmdb_vote_count") or 0),
        tmdb_vote_checked_at=float(d.get("tmdb_vote_checked_at") or 0),
        poster_path=d.get("poster_path", "") or "",
        backdrop_path=d.get("backdrop_path", "") or "",
        overview=d.get("overview", "") or "",
        tmdb_genres=d.get("tmdb_genres", []) or [],
        cast=d.get("cast", []) or [],
        director=d.get("director", "") or "",
        enriched_at=float(d.get("enriched_at", 0) or 0),
        video_codec=d.get("video_codec", "") or "",
        pix_fmt=d.get("pix_fmt", "") or "",
        probed_at=float(d.get("probed_at", 0) or 0),
        episode_title=d.get("episode_title", "") or "",
        episode_overview=d.get("episode_overview", "") or "",
        episode_still_path=d.get("episode_still_path", "") or "",
        episode_air_date=d.get("episode_air_date", "") or "",
        episode_tmdb_vote_average=float(d.get("episode_tmdb_vote_average") or 0),
        episode_tmdb_vote_count=int(d.get("episode_tmdb_vote_count") or 0),
        episode_tmdb_vote_checked_at=float(d.get("episode_tmdb_vote_checked_at") or 0),
        trailer_key=d.get("trailer_key", "") or "",
        media_kind=d.get("media_kind", "") or "",
        artist=d.get("artist", "") or "",
        album_title=d.get("album_title", "") or "",
        album_key=d.get("album_key", "") or "",
        track_number=d.get("track_number"),
        audio_codec=d.get("audio_codec", "") or "",
        audio_sample_rate=int(d.get("audio_sample_rate") or 0),
        audio_bit_depth=int(d.get("audio_bit_depth") or 0),
        admin_locked=list(d.get("admin_locked") or []),
        hidden=bool(d.get("hidden", False)),
        subtitles=[
            ExternalSubtitle(
                bin_message_id=s["bin_message_id"],
                secure_hash=s.get("secure_hash", ""),
                language=s.get("language", ""),
                label=s.get("label", ""),
            )
            for s in (d.get("subtitles") or [])
        ],
        embedded_subtitle_count=int(d.get("embedded_subtitle_count") or 0),
        subtitles_probed_at=float(d.get("subtitles_probed_at") or 0),
    )


def _media_of(message):
    return (
        getattr(message, "video", None)
        or getattr(message, "audio", None)
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


def _is_audio_message(message) -> bool:
    if getattr(message, "empty", False):
        return False
    if getattr(message, "audio", None) is not None:
        return True
    media = _media_of(message)
    if media is None:
        return False
    mime = (getattr(media, "mime_type", "") or "").lower()
    return mime.startswith("audio/")


_KURIGRAM_TS_RE = re.compile(r"^video_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.mp4$")
# Other device-generated or meaningless filenames that should not be used
# as TMDB search titles.
_DEVICE_NAME_RE = re.compile(
    r"""
    ^(
        \(?untitled\)?                    # "Untitled.mp4" or "(untitled).mp4"
      | video                             # bare "video.mp4"
      | movie                             # bare "movie.mp4"
      | recording                         # screen/audio recording
      | capture                           # screen capture
      | clip                              # generic clip name
      | screen[\s_-]?recording
      | VID_\d{8}_\d{6}                  # Android default (VID_20260519_125106)
      | MOV_\d{8}_\d{6}                  # iOS default
      | IMG_\d{4,}                        # iOS image sequence
      | DCIM_\d+                          # DCIM folder auto-name
      | \d{8}[_\-]\d{6}                  # raw timestamp only
    )(\.[a-z0-9]{2,4})?$                  # optional extension
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ATTR_USER_ID_RE = re.compile(r"\bUser\s+ID\s*:\s*\**\s*`?(-?\d+)`?", re.IGNORECASE)


def _bin_attribution_marker(message) -> Optional[Tuple[int, bool]]:
    """Return (source_file_message_id, is_admin_added) for BIN reply notes.

    Normal upload handlers write a bot reply next to the copied file:
    private uploads include ``User ID``, group/channel uploads include
    ``Group ID``/``Channel ID``. Admin grab flows write ``Grabbed`` notes.
    During startup seed we scan newest-to-oldest, so the attribution reply
    is usually encountered before the file it replies to.
    """
    reply = getattr(message, "reply_to_message", None)
    reply_id = getattr(reply, "id", None)
    if not reply_id:
        return None
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "")
    if not text:
        return None

    # /grab and /grablist are owner-only commands, so their BIN messages
    # count as admin-added even when the original source was elsewhere.
    if "Grabbed" in text:
        return int(reply_id), True

    m = _ATTR_USER_ID_RE.search(text)
    if m:
        try:
            from main.vars import Var
            return int(reply_id), int(m.group(1)) == int(Var.OWNER_ID)
        except Exception:
            return int(reply_id), False

    if re.search(r"\b(Group|Channel) ID\s*:", text, re.IGNORECASE):
        return int(reply_id), False

    return None


def _clean_file_name(name: str) -> str:
    """Strip device-generated / meaningless filenames so they don't
    pollute TMDB searches or appear as display names."""
    if not name:
        return name
    if _KURIGRAM_TS_RE.match(name):
        return ""
    if _DEVICE_NAME_RE.match(name):
        return ""
    return name


def _is_generic_media_title(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return True
    return bool(_KURIGRAM_TS_RE.match(value) or _DEVICE_NAME_RE.match(value))


_MIME_TO_EXT = {
    "mp4": "mp4", "x-matroska": "mkv", "quicktime": "mov",
    "x-msvideo": "avi", "webm": "webm", "x-ms-wmv": "wmv",
    "mpeg": "mpg", "3gpp": "3gp",
}

def _synthesize_filename(title: str, year, media) -> str:
    """Last-resort display name for video-type uploads with no file_name."""
    if not title:
        return ""
    mime = (getattr(media, "mime_type", "") or "").lower()
    sub = mime.split("/")[-1] if "/" in mime else "mp4"
    ext = _MIME_TO_EXT.get(sub, "mp4")
    return f"{title} ({year}).{ext}" if year else f"{title}.{ext}"


def _item_from_message(message) -> Optional[HubItem]:
    is_audio = _is_audio_message(message)
    if not _is_video_message(message) and not is_audio:
        return None
    media_kind = "audio" if is_audio else "video"
    media = _media_of(message)
    # For audio messages, Telegram extracts performer/title directly
    audio_obj = getattr(message, "audio", None)
    tg_performer = (getattr(audio_obj, "performer", None) or "").strip() if audio_obj else ""
    tg_track_title = (getattr(audio_obj, "title", None) or "").strip() if audio_obj else ""
    file_name = _clean_file_name(getattr(media, "file_name", None) or "")
    if not file_name and tg_track_title:
        # Use Telegram's extracted track title as display name
        file_name = tg_track_title
    # Video-type uploads (not documents) carry no file_name. Try the
    # caption first — it's often the original filename when the user
    # pastes it in. If the caption is absent or unhelpful, synthesise a
    # display name from the title + year + mime extension after parsing.
    if not file_name and not is_audio:
        cap = (message.caption or "").strip()
        if cap and re.search(
            r'\.(mkv|mp4|avi|mov|wmv|flv|webm|m4v|ts|m2ts)\s*$',
            cap, re.IGNORECASE,
        ):
            file_name = cap
    parsed = parse(message.caption or "")
    if parsed is None:
        parsed = IndexEntry(
            title=tg_track_title or title_from_filename(file_name),
            year=year_from_filename(file_name),
        )
    elif not parsed.title and tg_track_title:
        parsed.title = tg_track_title
        if parsed.year is None:
            parsed.year = year_from_filename(file_name)
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
        episode_end = sm.episode_end
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
        episode_end = None
        movie_key = ""
    else:
        series_key = ""
        series_title = ""
        season = None
        episode = None
        episode_end = None
        # Audio items belong to the music world — no movie_key so they
        # don't appear in the Movies shelf or grid.
        movie_key = "" if is_audio else compute_movie_key(
            parsed.title or file_name, parsed.year, file_name
        )

    # When the caption carries a TMDB id, the description we just parsed
    # is the TMDB overview written back by an earlier enrichment. Promote
    # it to the overview field so a re-seed-from-BIN keeps the watch
    # page's overview block populated without needing to re-hit TMDB.
    overview = parsed.description if parsed.tmdb_id else ""

    # Compute artist and album_key for audio items.
    # album_key is intentionally left empty at index time — the background
    # codec probe extracts the album title from ID3 tags and sets the key
    # from album title alone (not artist+album) so multi-artist soundtrack
    # albums group correctly. A per-artist key here would split them.
    artist = tg_performer
    album_key = ""

    return HubItem(
        message_id=message.id,
        secure_hash=get_hash(message),
        title=parsed.title,
        year=parsed.year,
        description=parsed.description,
        tags=parsed.tags,
        duration=int(getattr(media, "duration", 0) or 0),
        file_size=int(getattr(media, "file_size", 0) or 0),
        # Video/Document have thumbs (list); Audio.thumb is singular.
        has_thumb=bool(getattr(media, "thumbs", None) or getattr(media, "thumb", None)),
        quality=_extract_quality(parsed.title, file_name, parsed.description),
        file_name=file_name or _synthesize_filename(parsed.title, parsed.year, media),
        series_key=series_key,
        series_title=series_title,
        season=season,
        episode=episode,
        episode_end=episode_end,
        movie_key=movie_key,
        # Provider IDs + artwork paths round-trip through the caption
        # so a re-seed recovers them without needing to hit TMDB again.
        tmdb_id=parsed.tmdb_id,
        tmdb_kind=parsed.tmdb_kind,
        imdb_id=parsed.imdb_id,
        poster_path=parsed.poster_path,
        backdrop_path=parsed.backdrop_path,
        overview=overview,
        media_kind=media_kind,
        artist=artist,
        album_title="",      # filled by background music probe
        album_key=album_key,
        track_number=None,   # filled by background music probe
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
            if _store_active():
                try:
                    await _store.set_meta("latest_seen_id", _latest_seen_id)
                except Exception:
                    logging.debug("store.set_meta failed", exc_info=True)
        return
    async with _lock:
        # Preserve any sidecar subtitle pairings across re-indexing.
        existing = _items.get(item.message_id)
        if existing and existing.subtitles:
            item.subtitles = existing.subtitles
        _items[item.message_id] = item
        _hash_map[item.secure_hash] = item.message_id
        if item.message_id > _latest_seen_id:
            _latest_seen_id = item.message_id
        _persist_unlocked()
    # Write-through to Mongo (outside the lock — network call).
    await _store_upsert(item)
    if _store_active():
        try:
            await _store.set_meta("latest_seen_id", _latest_seen_id)
        except Exception:
            logging.debug("store.set_meta failed", exc_info=True)


def get_item(message_id: int) -> Optional[HubItem]:
    return _items.get(message_id)


def find_by_hash(secure_hash: str) -> Optional[HubItem]:
    """O(1) lookup by secure_hash via _hash_map index."""
    if not secure_hash:
        return None
    msg_id = _hash_map.get(secure_hash)
    return _items.get(msg_id) if msg_id else None


def find_exact_upload(secure_hash: str, file_size: int) -> Optional[HubItem]:
    """Return the oldest known upload for an exact file identity.

    ``secure_hash`` is intentionally short because it is embedded in URLs, so
    callers that are making destructive or de-duplication decisions should pair
    it with Telegram's file size to avoid treating hash-prefix collisions as
    duplicate media.
    """
    if not secure_hash or not file_size:
        return None
    matches = [
        it for it in _items.values()
        if it.secure_hash == secure_hash and int(it.file_size or 0) == int(file_size)
    ]
    if not matches:
        return None
    return min(matches, key=lambda it: it.message_id)


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


async def remove(message_id: int, bot=None) -> None:
    """Drop an entry from the catalogue.

    Writes-through to Mongo when configured so deletions survive
    restart; otherwise schedules a Telegram-snapshot save via the
    legacy path.
    """
    async with _lock:
        existed = _items.pop(message_id, None)
        if existed is not None:
            _hash_map.pop(existed.secure_hash, None)
            _persist_unlocked()
    if existed is None:
        return
    await _store_remove(message_id)
    # Drop the cached thumbnail too so the thumbs collection doesn't
    # accumulate orphans. No-op for non-Mongo stores.
    if _store_active():
        try:
            await _store.remove_thumb(message_id)
        except Exception:
            logging.debug("remove: thumb cleanup failed for bin:%d",
                          message_id, exc_info=True)
    if bot is not None:
        schedule_snapshot(bot)  # No-op when Mongo is active.


async def prune_stale(bot, channel_id: int, batch_size: int = 100) -> int:
    """Check every catalogue entry against BIN_CHANNEL and remove any whose
    messages no longer exist. Returns the count of removed entries.

    Called by the background prune loop and by the admin prune-stale route
    so both paths share identical logic.
    """
    ids = list(_items.keys())
    removed = 0
    for i in range(0, len(ids), batch_size):
        batch = ids[i: i + batch_size]
        try:
            msgs = await bot.get_messages(channel_id, batch)
        except Exception:
            logging.exception("media_index.prune_stale: batch fetch failed")
            continue
        for msg in msgs:
            if msg.empty:
                await remove(msg.id, bot=bot)
                removed += 1
    if removed:
        logging.info("media_index.prune_stale: removed %d stale entries", removed)
    return removed


async def prune_non_admin_uploads(bot, channel_id: int, batch_size: int = _FETCH_BATCH) -> int:
    """Remove catalogue rows whose BIN attribution proves non-admin origin.

    This does not delete BIN messages. Non-admin uploads may still back
    private stream links; they just should not appear in the curated hub.
    """
    ids = list(_items.keys())
    if not ids:
        return 0

    floor = min(ids)
    latest = max(_latest_seen_id, max(ids))
    try:
        probe = await bot.send_message(channel_id, ".")
        latest = max(latest, int(getattr(probe, "id", 0) or 0))
        await _delete_probe(bot, channel_id, probe.id)
    except Exception:
        logging.debug("media_index: prune_non_admin probe failed", exc_info=True)

    non_admin_ids: set[int] = set()
    high = latest
    while high >= floor:
        batch_ids = list(range(high, max(floor - 1, high - batch_size), -1))
        try:
            batch = await bot.get_messages(channel_id, batch_ids)
        except Exception:
            logging.exception(
                "media_index: prune_non_admin get_messages failed for %d..%d",
                batch_ids[-1], batch_ids[0],
            )
            break
        if not isinstance(batch, list):
            batch = [batch]
        for message in batch:
            marker = _bin_attribution_marker(message)
            if marker is None:
                continue
            source_file_id, is_admin_added = marker
            if not is_admin_added and source_file_id in _items:
                non_admin_ids.add(source_file_id)
        high -= batch_size
        await asyncio.sleep(0)

    removed = 0
    for mid in sorted(non_admin_ids):
        if mid in _items:
            await remove(mid)
            removed += 1
    if removed:
        schedule_snapshot(bot)
        logging.info("media_index: pruned %d non-admin catalogue rows", removed)
    return removed


async def persist_now() -> None:
    """Public helper: take the lock and flush the /tmp JSON cache.

    Used by helper modules (codec_probe etc.) that mutate item
    fields in place and need to durably record the change.
    """
    async with _lock:
        _persist_unlocked()


def _invalidate_hub_caches() -> None:
    try:
        from main.server.hub_routes import invalidate_render_cache
        invalidate_render_cache()
    except Exception:
        pass
    try:
        from main.server.spa_routes import invalidate_api_cache
        invalidate_api_cache()
    except Exception:
        pass


def _persist_unlocked() -> None:
    _invalidate_hub_caches()
    # When MongoDB is the durable store every mutation is already written
    # through there. Writing to /tmp JSON is redundant — Koyeb wipes /tmp
    # on restart and the bot re-seeds from Mongo anyway.
    if _store_active():
        return
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
                _hash_map[item.secure_hash] = item.message_id
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

    Bots can't call ``messages.getHistory``, so we discover the channel's
    current latest message id by sending a tiny ``.`` and reading the id
    Telegram returns, then immediately deleting it. The probe IS the only
    way for a bot to learn the high-water mark.

    We used to skip the probe on warm restarts (``_latest_seen_id`` was
    persisted to /tmp and the pinned snapshot), but that turned out to
    LOSE entries: uploads made between the last snapshot and a restart
    leave ``_latest_seen_id`` stale, and seed would then walk only DOWN
    from the stale value — missing every new upload above it. With a
    debounced snapshot save (up to 30s after a mutation), this race is
    realistic for users who upload a batch and restart soon after.

    Probing every seed adds one ``.`` send + delete per startup, which
    the retry logic in ``_delete_probe`` cleans up so the dot never
    stays visible.
    """
    global _seeded, _latest_seen_id
    if _seeded:
        return

    # Only load from /tmp JSON when Mongo isn't active. With Mongo, /tmp is
    # wiped on restart and seeding from the stale JSON would just be noise
    # before the Mongo load overwrites it anyway.
    if not _store_active():
        _load()

    # Durable Mongo store wins if configured. Loads the whole
    # catalogue in one shot, then we still probe BIN below to pick
    # up anything uploaded between the last Mongo write and now.
    if _store_active() and not _items:
        try:
            docs = await _store.load_all()
            async with _lock:
                for d in docs:
                    try:
                        item = _from_serializable(d)
                        _items[item.message_id] = item
                        _hash_map[item.secure_hash] = item.message_id
                    except Exception:
                        logging.debug(
                            "media_index: bad Mongo doc skipped",
                            exc_info=True,
                        )
                if _items:
                    local_max = max(_items.keys())
                    _latest_seen_id = max(_latest_seen_id, local_max)
                # Pull persisted meta values (latest_seen_id) so the seed
                # walk can resume from a sane high-water mark.
                try:
                    stored_latest = await _store.get_meta("latest_seen_id")
                    if isinstance(stored_latest, int) and stored_latest > _latest_seen_id:
                        _latest_seen_id = stored_latest
                except Exception:
                    pass
            logging.info(
                "media_index: loaded %d items from Mongo (latest=%d)",
                len(_items), _latest_seen_id,
            )
        except Exception:
            logging.exception(
                "media_index: Mongo load_all failed — falling through to"
                " legacy snapshot restore",
            )

    # If neither /tmp nor Mongo populated _items, try the
    # Telegram-pinned snapshot as a last resort.
    if not _items:
        try:
            await restore_from_telegram(bot)
        except Exception:
            logging.exception(
                "media_index: telegram-snapshot restore failed (non-fatal)"
            )

    # Always probe — we can't trust _latest_seen_id to reflect the
    # actual channel state after a /tmp wipe + stale snapshot.
    try:
        probe = await bot.send_message(channel_id, ".")
    except Exception:
        logging.exception("media_index: probe send failed; seed skipped")
        _seeded = True
        return
    latest_id = probe.id
    await _delete_probe(bot, channel_id, probe.id)

    if latest_id > _latest_seen_id:
        _latest_seen_id = latest_id

    floor = max(1, latest_id - _SEED_DEPTH)
    high = latest_id
    total_to_scan = high - floor + 1
    # Track which message ids the BIN actually still has — at end of
    # seed, any _items entry within the walk range that wasn't seen
    # corresponds to a deleted BIN message and gets pruned. Catches
    # deletions that happened while the bot was offline (when the
    # on_deleted_messages handler can't fire).
    seen_ids: set = set()
    source_admin_by_file_id: Dict[int, bool] = {}
    source_pruned_ids: set = set()
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
                    # Non-empty messages are alive in BIN. Track them
                    # regardless of whether they're media — text /
                    # photo posts still exist and shouldn't trigger
                    # a stale-prune for any matching id (defensive;
                    # we only prune _items entries which only ever
                    # come from video messages).
                    if m is not None and not getattr(m, "empty", False):
                        try:
                            seen_ids.add(int(m.id))
                        except (TypeError, ValueError):
                            pass
                    marker = _bin_attribution_marker(m)
                    if marker is not None:
                        source_file_id, is_admin_added = marker
                        source_admin_by_file_id[source_file_id] = is_admin_added
                        continue
                    try:
                        msg_id = int(getattr(m, "id", 0) or 0)
                    except (TypeError, ValueError):
                        msg_id = 0
                    if msg_id and source_admin_by_file_id.get(msg_id) is False:
                        existing = _items.pop(msg_id, None)
                        if existing is not None:
                            _hash_map.pop(existing.secure_hash, None)
                            source_pruned_ids.add(msg_id)
                        continue
                    new_item = _item_from_message(m)
                    if new_item is None:
                        continue
                    existing = _items.get(new_item.message_id)
                    if existing is not None and _store_active():
                        # ── MongoDB path ──────────────────────────────────
                        # MongoDB is the durable source of truth for every
                        # enrichment/admin-edited field.  The seed only
                        # provides three things the BIN message can change:
                        #   • file_size  (re-upload, compression)
                        #   • duration   (Telegram media property)
                        #   • has_thumb  (thumbnail availability)
                        # Everything else — title, year, file_name, series,
                        # TMDB, episode metadata, probe results, tags,
                        # trailer_key — lives in MongoDB and must not be
                        # overwritten by the raw-parse result.
                        existing.file_size = new_item.file_size or existing.file_size
                        existing.duration  = new_item.duration  or existing.duration
                        existing.has_thumb = new_item.has_thumb or existing.has_thumb
                        # If a parser improvement gives the item a
                        # series_key it didn't have before, reset
                        # enriched_at so the next enrich pass retries.
                        if (new_item.series_key
                                and not existing.series_key
                                and not existing.tmdb_id):
                            existing.enriched_at = 0.0
                        # Carry forward new music metadata if the
                        # existing item has none (e.g. re-seed after
                        # Phase 1 upgrade).
                        if new_item.artist and not existing.artist:
                            existing.artist = new_item.artist
                        if new_item.album_title and not existing.album_title:
                            existing.album_title = new_item.album_title
                        if new_item.album_key and not existing.album_key:
                            existing.album_key = new_item.album_key
                        if new_item.track_number is not None and existing.track_number is None:
                            existing.track_number = new_item.track_number
                        if new_item.media_kind and not existing.media_kind:
                            existing.media_kind = new_item.media_kind
                        _items[existing.message_id] = existing
                        _hash_map[existing.secure_hash] = existing.message_id
                    elif existing is not None:
                        # ── JSON-snapshot / no-store path ─────────────────
                        # Caption write-backs are used when there is no
                        # persistent store; the parsed caption carries
                        # enrichment data so new_item should win for TMDB
                        # fields.  Carry forward only the fields that are
                        # never in captions.
                        new_item.tmdb_genres  = existing.tmdb_genres  or new_item.tmdb_genres
                        new_item.tmdb_vote_average = existing.tmdb_vote_average or new_item.tmdb_vote_average
                        new_item.tmdb_vote_count = existing.tmdb_vote_count or new_item.tmdb_vote_count
                        new_item.tmdb_vote_checked_at = existing.tmdb_vote_checked_at or new_item.tmdb_vote_checked_at
                        new_item.episode_tmdb_vote_average = existing.episode_tmdb_vote_average or new_item.episode_tmdb_vote_average
                        new_item.episode_tmdb_vote_count = existing.episode_tmdb_vote_count or new_item.episode_tmdb_vote_count
                        new_item.episode_tmdb_vote_checked_at = existing.episode_tmdb_vote_checked_at or new_item.episode_tmdb_vote_checked_at
                        new_item.subtitles    = existing.subtitles    or new_item.subtitles
                        new_item.enriched_at  = existing.enriched_at  or new_item.enriched_at
                        caption_lost_enrichment = existing.tmdb_id and not new_item.tmdb_id
                        if caption_lost_enrichment:
                            new_item.title        = existing.title        or new_item.title
                            new_item.year         = existing.year         or new_item.year
                            new_item.tmdb_id      = existing.tmdb_id
                            new_item.tmdb_kind    = existing.tmdb_kind
                            new_item.imdb_id      = existing.imdb_id      or new_item.imdb_id
                            new_item.tmdb_vote_average = existing.tmdb_vote_average or new_item.tmdb_vote_average
                            new_item.tmdb_vote_count = existing.tmdb_vote_count or new_item.tmdb_vote_count
                            new_item.tmdb_vote_checked_at = existing.tmdb_vote_checked_at or new_item.tmdb_vote_checked_at
                            new_item.poster_path  = existing.poster_path  or new_item.poster_path
                            new_item.backdrop_path= existing.backdrop_path or new_item.backdrop_path
                            if existing.tmdb_kind == "tv":
                                new_item.series_title = existing.series_title or new_item.series_title
                                new_item.series_key   = existing.series_key   or new_item.series_key
                            if existing.movie_key and not new_item.movie_key:
                                new_item.movie_key = existing.movie_key
                        for attr in ("tags", "description", "overview", "file_name",
                                     "trailer_key", "episode_title", "episode_overview",
                                     "episode_still_path", "episode_air_date",
                                     "artist", "album_title", "album_key", "media_kind",
                                     "director"):
                            if getattr(existing, attr) and not getattr(new_item, attr):
                                setattr(new_item, attr, getattr(existing, attr))
                        if existing.cast and not new_item.cast:
                            new_item.cast = existing.cast
                        if existing.track_number is not None and new_item.track_number is None:
                            new_item.track_number = existing.track_number
                        if existing.probed_at:
                            new_item.probed_at   = existing.probed_at
                            new_item.video_codec = existing.video_codec or new_item.video_codec
                            new_item.pix_fmt     = existing.pix_fmt     or new_item.pix_fmt
                            if existing.quality:
                                new_item.quality = existing.quality
                            new_item.audio_codec       = existing.audio_codec       or new_item.audio_codec
                            new_item.audio_sample_rate = existing.audio_sample_rate or new_item.audio_sample_rate
                            new_item.audio_bit_depth   = existing.audio_bit_depth   or new_item.audio_bit_depth
                        if existing.admin_locked and not new_item.admin_locked:
                            new_item.admin_locked = existing.admin_locked
                        _items[new_item.message_id] = new_item
                        _hash_map[new_item.secure_hash] = new_item.message_id
                    else:
                        # ── New item not previously seen ───────────────────
                        _items[new_item.message_id] = new_item
                        _hash_map[new_item.secure_hash] = new_item.message_id
                _persist_unlocked()
            if source_pruned_ids:
                for mid in list(source_pruned_ids):
                    await _store_remove(mid)
                source_pruned_ids.clear()
            _seed_state["scanned"] += len(ids)
            _seed_state["indexed"] = len(_items)
            high -= _FETCH_BATCH
            # Yield to the loop so a long seed doesn't starve other work.
            await asyncio.sleep(0)
    finally:
        # Stale-prune pass: any _items entry whose message_id sits
        # within the walked range [floor, latest_id] but wasn't seen
        # in the seen_ids set is a deleted BIN message. Drop it so
        # the catalogue reflects offline deletions.
        pruned = 0
        if seen_ids:
            async with _lock:
                stale_ids = [
                    mid for mid in list(_items.keys())
                    if floor <= mid <= latest_id and mid not in seen_ids
                ]
                for mid in stale_ids:
                    pruned_item = _items.pop(mid, None)
                    if pruned_item is not None:
                        _hash_map.pop(pruned_item.secure_hash, None)
                    pruned += 1
                if pruned:
                    _persist_unlocked()
        if pruned:
            logging.info(
                "media_index: pruned %d stale entries (deleted from BIN)",
                pruned,
            )
            # Write-through to durable store so the prune survives restart.
            # Without this, pruned items remain in MongoDB and re-appear
            # every boot even after being deleted from BIN_CHANNEL.
            for mid in stale_ids:
                await _store_remove(mid)
            schedule_snapshot(bot)
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
    episode_label = ""
    if item.season is not None and item.episode is not None:
        episode_label = f"s{item.season:02d}e{item.episode:02d}"
    elif item.episode is not None:
        episode_label = f"episode {item.episode}"
    return " ".join([
        item.title.lower(),
        item.series_title.lower(),
        item.description.lower(),
        item.overview.lower(),
        item.episode_title.lower(),
        item.episode_overview.lower(),
        item.file_name.lower(),
        item.imdb_id.lower(),
        str(item.year or ""),
        item.quality.lower(),
        episode_label,
        " ".join(t.lower() for t in item.tags),
        " ".join(g.lower() for g in item.tmdb_genres),
        # Cast + director — lets users find titles by actor or director name
        " ".join(a.lower() for a in item.cast),
        item.director.lower(),
        # Music fields — artist and album so music is searchable by name
        (item.artist or "").lower(),
        (item.album_title or "").lower(),
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


def _search_score(q: str, item: HubItem) -> float:
    """Return a relevance score for a user search query.

    Scores are intentionally coarse: exact/title matches should outrank
    artist/album matches, which should outrank broad metadata hits.
    """
    ql = (q or "").strip().lower().lstrip("#")
    if not ql:
        return 0.0
    title = item.title.lower()
    series_title = item.series_title.lower()
    if ql == title or ql == series_title:
        return 1.2
    if ql in title or ql in series_title:
        return 1.0
    if item.media_kind == "audio":
        artist = (item.artist or "").lower()
        album = (item.album_title or "").lower()
        if ql == artist or ql == album:
            return 0.95
        if ql in artist or ql in album:
            return 0.9
    if ql in _haystack(item):
        return 0.6
    if len(ql) >= 3:
        fuzzy = _fuzzy_score(ql, item)
        if fuzzy > 0:
            return fuzzy * 0.8
    return 0.0


def _matches(item: HubItem, q: str, year: Optional[int], quality: str,
             tag: str, genre: str = "") -> bool:
    if year is not None and item.year != year:
        return False
    if quality and item.quality.lower() != quality.lower():
        return False
    if tag and tag.lstrip("#").lower() not in item.tags:
        return False
    if genre:
        gl = genre.lower()
        if not any(g.lower() == gl for g in (item.tmdb_genres or [])):
            return False
    if q:
        return _search_score(q, item) > 0
    return True


def query(
    *,
    q: str = "",
    year: Optional[int] = None,
    quality: str = "",
    tag: str = "",
    genre: str = "",
    sort: str = "newest",
    before_id: Optional[int] = None,
    limit: int = 24,
) -> Tuple[List[HubItem], Optional[int]]:
    """Unified filter + sort + paginate over the in-process catalogue."""
    q = (q or "").strip()
    key_fn, reverse = _SORT_KEYS.get(sort, _SORT_KEYS["newest"])
    items_all = sorted(_items.values(), key=key_fn, reverse=reverse)

    # Exclude hidden items from all public library views
    items_all = [it for it in items_all if not it.hidden]
    items_all = [it for it in items_all if _matches(it, q, year, quality, tag, genre)]
    if q and sort == "newest":
        items_all.sort(
            key=lambda item: (-_search_score(q, item), -item.message_id)
        )

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


def _preferred_art_item(items: List[HubItem]) -> HubItem:
    """Choose the group representative that gives cards the best artwork."""
    return max(
        items,
        key=lambda item: (
            bool(item.poster_path),
            bool(item.backdrop_path),
            bool(item.has_thumb),
            bool(item.tmdb_id),
            item.message_id,
        ),
    )


def best_group_art_item(
    item: HubItem,
    cache: Optional[dict[tuple[str, str], Optional[HubItem]]] = None,
) -> Optional[HubItem]:
    """Return the best TMDB-art sibling for a raw movie/series item.

    Raw cards must keep their own watch identity, but can borrow poster
    metadata from an enriched sibling in the same movie/series group.
    ``cache`` is request-scoped and avoids repeated full-catalog scans.
    """
    if (item.media_kind or "") == "audio" or item.poster_path:
        return None
    group_key: tuple[str, str] | None = None
    if item.series_key:
        group_key = ("series", item.series_key)
    elif item.movie_key:
        group_key = ("movie", item.movie_key)
    if group_key is None:
        return None
    if cache is not None and group_key in cache:
        cached = cache[group_key]
        return cached if cached is not None and cached.message_id != item.message_id else None
    if group_key[0] == "series":
        candidates = episodes_for_series(group_key[1])
    else:
        candidates = variants_for_movie(group_key[1])
    result = None
    if candidates:
        best = _preferred_art_item(candidates)
        if best.message_id != item.message_id and best.poster_path:
            result = best
    if cache is not None:
        cache[group_key] = result
    return result


def group_art_cache_for(items: Iterable[object]) -> dict[tuple[str, str], Optional[HubItem]]:
    wanted_series: set[str] = set()
    wanted_movies: set[str] = set()
    for item in items:
        if not isinstance(item, HubItem):
            continue
        if (item.media_kind or "") == "audio":
            continue
        if item.series_key:
            wanted_series.add(item.series_key)
        elif item.movie_key:
            wanted_movies.add(item.movie_key)
    if not wanted_series and not wanted_movies:
        return {}

    buckets: dict[tuple[str, str], list[HubItem]] = {}
    for candidate in _items.values():
        if candidate.hidden or (candidate.media_kind or "") == "audio":
            continue
        if candidate.series_key in wanted_series:
            buckets.setdefault(("series", candidate.series_key), []).append(candidate)
        elif candidate.movie_key in wanted_movies:
            buckets.setdefault(("movie", candidate.movie_key), []).append(candidate)

    cache: dict[tuple[str, str], Optional[HubItem]] = {}
    for series_key in wanted_series:
        key = ("series", series_key)
        candidates = buckets.get(key) or []
        best = _preferred_art_item(candidates) if candidates else None
        cache[key] = best if best is not None and best.poster_path else None
    for movie_key in wanted_movies:
        key = ("movie", movie_key)
        candidates = buckets.get(key) or []
        best = _preferred_art_item(candidates) if candidates else None
        cache[key] = best if best is not None and best.poster_path else None
    return cache


def poster_path_for_item(
    item: HubItem,
    cache: Optional[dict[tuple[str, str], Optional[HubItem]]] = None,
) -> str:
    art_item = best_group_art_item(item, cache=cache)
    return (art_item.poster_path if art_item is not None else item.poster_path) or ""


def _card_group_members(card: object) -> tuple[tuple[str, str] | None, list[HubItem]]:
    if isinstance(card, SeriesGroup):
        return ("series", card.series_key), episodes_for_series(card.series_key)
    if isinstance(card, MovieGroup):
        return ("movie", card.movie_key), variants_for_movie(card.movie_key)
    if not isinstance(card, HubItem):
        return None, []
    if (card.media_kind or "") == "audio":
        return None, []
    if card.series_key:
        return ("series", card.series_key), episodes_for_series(card.series_key)
    if card.movie_key:
        return ("movie", card.movie_key), variants_for_movie(card.movie_key)
    return ("item", str(card.message_id)), [card]


def _needs_art_recovery(members: list[HubItem]) -> bool:
    if not members:
        return False
    if any(item.poster_path for item in members):
        return False
    return any((item.media_kind or "") != "audio" for item in members)


def _art_recovery_is_suppressed(group_key: tuple[str, str]) -> bool:
    until = _art_recovery_negative_until.get(group_key)
    if not until:
        return False
    if time.time() < until:
        return True
    _art_recovery_negative_until.pop(group_key, None)
    return False


def _remember_art_recovery_miss(group_key: tuple[str, str]) -> None:
    _art_recovery_negative_until[group_key] = time.time() + _ART_RECOVERY_NEGATIVE_TTL


def _art_recovery_target(members: list[HubItem]) -> Optional[HubItem]:
    candidates = [
        item for item in members
        if not item.hidden and (item.media_kind or "") != "audio"
    ]
    if not candidates:
        return None
    enriched = next((item for item in candidates if item.tmdb_id), None)
    if enriched is not None:
        return enriched
    return max(
        candidates,
        key=lambda item: (
            len((item.series_title or item.title or item.file_name or "").strip()),
            item.message_id,
        ),
    )


async def ensure_card_art_enriched(card: object, *, bot=None) -> bool:
    """Best-effort TMDB recovery for one visible card/detail group.

    This is intentionally read-path scoped: it only runs when a visible
    movie/series has no TMDB poster on any sibling. Successful enrichment
    is persisted by ``enrich_one`` so the next request is a normal cache hit.
    """
    if not tmdb.is_configured():
        return False
    group_key, members = _card_group_members(card)
    if group_key is None or not _needs_art_recovery(members):
        return False
    if _art_recovery_is_suppressed(group_key):
        return False
    lock = _group_enrich_locks.setdefault(group_key, asyncio.Lock())
    async with lock:
        _group_key, members = _card_group_members(card)
        if not _needs_art_recovery(members):
            return False
        if _art_recovery_is_suppressed(group_key):
            return False
        target = _art_recovery_target(members)
        if target is None:
            return False
        enriched = await enrich_one(target.message_id, bot=bot)
        _group_key, refreshed_members = _card_group_members(card)
        if any(item.poster_path for item in refreshed_members):
            _art_recovery_negative_until.pop(group_key, None)
            return True
        _remember_art_recovery_miss(group_key)
        return False


def _group_art_task_done(group_key: tuple[str, str], task: asyncio.Task) -> None:
    if _group_art_tasks.get(group_key) is task:
        _group_art_tasks.pop(group_key, None)


async def ensure_cards_art_enriched(
    cards: Iterable[object],
    *,
    bot=None,
    limit: int = 3,
    timeout: float = 6.0,
) -> int:
    """Recover missing TMDB art for a small set of visible cards.

    The limit keeps grid/search requests from turning into a bulk
    enrichment job. Admin still owns full backfills via ``enrich_all``.
    """
    if not tmdb.is_configured():
        return 0
    targets: list[object] = []
    seen: set[tuple[str, str]] = set()
    for card in cards:
        group_key, members = _card_group_members(card)
        if group_key is None or group_key in seen:
            continue
        if _art_recovery_is_suppressed(group_key):
            continue
        if not _needs_art_recovery(members):
            continue
        seen.add(group_key)
        targets.append(card)
        if len(targets) >= limit:
            break
    if not targets:
        return 0

    tasks: list[asyncio.Task] = []
    for card in targets:
        group_key, _members = _card_group_members(card)
        if group_key is None:
            continue
        task = _group_art_tasks.get(group_key)
        if task is None or task.done():
            task = asyncio.create_task(ensure_card_art_enriched(card, bot=bot))
            _group_art_tasks[group_key] = task
            task.add_done_callback(
                lambda done_task, key=group_key: _group_art_task_done(key, done_task)
            )
        tasks.append(task)
    if not tasks:
        return 0

    done, _pending = await asyncio.wait(tasks, timeout=timeout)
    if not done:
        logging.warning("media_index: visible art recovery timed out")
        return 0
    recovered = 0
    for task in done:
        try:
            if task.result():
                recovered += 1
        except Exception:
            logging.exception("media_index: visible art recovery failed")
    return recovered


def _build_series_group(episodes: List[HubItem]) -> SeriesGroup:
    """Construct a SeriesGroup from at least one episode HubItem."""
    poster = next(
        (e for e in episodes if e.has_thumb),
        max(episodes, key=lambda e: e.message_id),
    )
    seasons = {e.season for e in episodes if e.season is not None}
    # Count distinct (season, episode) pairs so variant uploads of the
    # same episode (or the same range file) don't inflate the number.
    # Falls back to raw file count only when no episode numbers exist.
    distinct_eps = len(
        {(e.season, e.episode) for e in episodes if e.episode is not None}
    ) or len(episodes)
    return SeriesGroup(
        series_key=episodes[0].series_key,
        series_title=episodes[0].series_title or episodes[0].title,
        episode_count=distinct_eps,
        season_count=len(seasons) or 1,
        latest_message_id=max(e.message_id for e in episodes),
        poster_item=poster,
        art_item=_preferred_art_item(episodes),
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
        art_item=_preferred_art_item(variants),
        has_thumb=any(v.has_thumb for v in variants),
        total_size=sum(v.file_size for v in variants),
    )


def query_grouped(
    *,
    q: str = "",
    year: Optional[int] = None,
    quality: str = "",
    tag: str = "",
    genre: str = "",
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
    q = (q or "").strip()
    items_all = [
        it for it in _items.values()
        if not it.hidden
        and _matches(it, q, year, quality, tag, genre)
    ]
    search_scores = {
        it.message_id: _search_score(q, it)
        for it in items_all
    } if q and sort == "newest" else {}

    series_groups: dict = {}
    movie_groups: dict = {}
    standalone: List[HubItem] = []
    for it in items_all:
        # Audio items belong to the music view when browsing.
        # When a search query is active, include audio in results so
        # searching "Indra" returns the track (suggest shows it; grid should too).
        if getattr(it, "media_kind", "") == "audio" and not q:
            continue
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
    elif view == "music":
        # For music view: album groups (from filtered items only) + standalone audio.
        # Build album buckets from items_all (already filtered by q/year/quality/tag)
        # so filters apply to music just like they do to series/movies.
        audio_buckets: dict = {}
        audio_singles: List[HubItem] = []
        for it in items_all:
            if getattr(it, "media_kind", "") != "audio":
                continue
            ak = getattr(it, "album_key", "")
            if ak:
                audio_buckets.setdefault(ak, []).append(it)
            else:
                audio_singles.append(it)
        music_albums = [_build_album_group(tracks) for tracks in audio_buckets.values()]
        combined = music_albums + audio_singles
        combined = sorted(
            combined,
            key=(
                (lambda card: _grouped_search_key(card, search_scores))
                if search_scores else _grouped_sort_key(sort)
            ),
        )
        total = len(combined)
        page_items = combined[offset:offset + limit]
        return page_items, total

    combined = grouped_series + grouped_movies + standalone
    combined = sorted(
        combined,
        key=(
            (lambda card: _grouped_search_key(card, search_scores))
            if search_scores else _grouped_sort_key(sort)
        ),
    )
    total = len(combined)
    page = combined[offset : offset + limit]
    return page, total


def _card_message_id(card) -> int:
    if isinstance(card, (SeriesGroup, MovieGroup, AlbumGroup)):
        return card.latest_message_id
    return card.message_id


def _card_title(card) -> str:
    if isinstance(card, SeriesGroup):
        return card.series_title
    if isinstance(card, MovieGroup):
        return card.title
    if isinstance(card, AlbumGroup):
        return card.album_title or card.artist
    return card.title


def _card_file_size(card) -> int:
    if isinstance(card, MovieGroup):
        return card.total_size
    if isinstance(card, SeriesGroup):
        # Series rank by their largest single episode rather than the
        # sum, since otherwise long-running shows always dominate.
        return max((e.file_size for e in episodes_for_series(card.series_key)), default=0)
    if isinstance(card, AlbumGroup):
        return card.max_file_size  # pre-computed at build time, avoids O(N) scan
    return card.file_size


def _grouped_sort_key(sort: str):
    if sort == "oldest":
        return lambda card: _card_message_id(card)
    if sort == "title_az":
        return lambda card: _card_title(card).lower()
    if sort == "title_za":
        return lambda card: tuple(-ord(c) for c in _card_title(card).lower())
    if sort == "largest":
        return lambda card: -_card_file_size(card)
    return lambda card: -_card_message_id(card)


def _grouped_search_key(card, scores: dict[int, float]):
    if isinstance(card, SeriesGroup):
        score = max(
            (scores.get(e.message_id, 0.0) for e in episodes_for_series(card.series_key)),
            default=0.0,
        )
    elif isinstance(card, MovieGroup):
        score = max(
            (scores.get(v.message_id, 0.0) for v in variants_for_movie(card.movie_key)),
            default=0.0,
        )
    elif isinstance(card, AlbumGroup):
        score = max(
            (scores.get(t.message_id, 0.0) for t in tracks_for_album(card.album_key)),
            default=0.0,
        )
    else:
        score = scores.get(card.message_id, 0.0)
    return (-score, -_card_message_id(card))


def episodes_for_series(series_key: str) -> List[HubItem]:
    """All episodes for a series, sorted by season then episode."""
    eps = [it for it in _items.values() if it.series_key == series_key and not it.hidden]
    eps.sort(key=lambda e: (e.season or 0, e.episode or 0, e.message_id))
    return eps


import re as _re
_ARTIST_SPLIT_RE = _re.compile(r"[,;/&×]|\bfeat\.?\b|\bft\.?\b|\bx\b|\band\b", _re.IGNORECASE)


def _artist_slug(name: str) -> str:
    """URL-safe slug from a single artist name."""
    return _re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _artist_credits(artist_str: str) -> List[str]:
    """Split a multi-artist credit string into individual artist names."""
    parts = _ARTIST_SPLIT_RE.split(artist_str)
    return [p.strip() for p in parts if p.strip()]


def _primary_artist(artist_str: str) -> str:
    """Return the first/primary artist from a potentially multi-artist string."""
    parts = _artist_credits(artist_str)
    return parts[0] if parts else artist_str


def _person_slug(name: str) -> str:
    """URL-safe slug from a person name (actor/director).

    Delegates to series_parse.slugify (already imported at module level)
    which does NFKD decomposition so accented names like 'Clémence Poésy'
    produce clean ASCII slugs.
    """
    return series_parse.slugify(name or "")


def _director_credits(director_str: str) -> List[str]:
    """Split the comma-separated director field into individual names."""
    if not director_str:
        return []
    return [p.strip() for p in director_str.split(",") if p.strip()]


def items_by_cast_slug(slug: str) -> List[HubItem]:
    """Return all non-hidden items where any cast member matches the slug."""
    matches = [
        it for it in _items.values()
        if it.cast and not it.hidden
        and any(_person_slug(name) == slug for name in it.cast)
    ]
    matches.sort(key=lambda it: (-(it.year or 0), it.title or ""))
    return matches


def items_by_director_slug(slug: str) -> List[HubItem]:
    """Return all non-hidden items where any credited director matches the slug."""
    matches = [
        it for it in _items.values()
        if it.director and not it.hidden
        and any(_person_slug(name) == slug for name in _director_credits(it.director))
    ]
    matches.sort(key=lambda it: (-(it.year or 0), it.title or ""))
    return matches


def person_display_name(slug: str) -> str:
    """Resolve the canonical display name for a person slug."""
    for it in _items.values():
        for name in (it.cast or []):
            if _person_slug(name) == slug:
                return name
    for it in _items.values():
        if it.director:
            for name in _director_credits(it.director):
                if _person_slug(name) == slug:
                    return name
    return slug


def tracks_by_artist_slug(slug: str) -> List[HubItem]:
    """Return all non-hidden audio tracks where ANY credited artist matches slug.

    Handles multi-artist fields like 'Artist A, Artist B feat. Artist C'.
    """
    def _matches(artist_str: str) -> bool:
        return any(_artist_slug(a) == slug for a in _artist_credits(artist_str))

    matches = [
        it for it in _items.values()
        if it.media_kind == "audio" and it.artist and not it.hidden
        and _matches(it.artist)
    ]
    matches.sort(key=lambda t: (t.album_title or "", t.track_number or 999, t.title or ""))
    return matches


def artist_display_name(slug: str) -> str:
    """Find the display name for an artist slug by scanning the catalogue."""
    for it in _items.values():
        if it.media_kind == "audio" and it.artist:
            for credit in _artist_credits(it.artist):
                if _artist_slug(credit) == slug:
                    return credit
    return slug


def tracks_for_album(album_key: str) -> List[HubItem]:
    """All audio tracks for an album, sorted by track_number then message_id.

    Matches by stored album_key OR by slugify(album_title) so that tracks
    with stale legacy keys (artist+album) are still found via the corrected
    title-only slug without requiring a re-probe.
    """
    def _matches(it) -> bool:
        if getattr(it, "media_kind", "") != "audio":
            return False
        if getattr(it, "album_key", "") == album_key:
            return True
        at = getattr(it, "album_title", "") or ""
        return bool(at) and series_parse.slugify(at) == album_key
    tracks = [it for it in _items.values() if _matches(it) and not it.hidden]
    return sorted(tracks, key=lambda t: (
        t.track_number if t.track_number is not None else 9999,
        t.message_id,
    ))


def _build_album_group(tracks: List[HubItem]) -> AlbumGroup:
    """Construct an AlbumGroup from a list of tracks."""
    poster = next((t for t in tracks if t.has_thumb),
                  max(tracks, key=lambda t: t.message_id))
    rep = tracks[0]
    # Pick the best album_title from any track — tracks[0] may not yet be
    # probed so its album_title might be empty while a later track has it.
    best_album_title = next((t.album_title for t in tracks if t.album_title), "")
    # Use normalised title slug as the canonical key so the /album/ URL is
    # stable regardless of what legacy key is stored on individual tracks.
    canonical_key = series_parse.slugify(best_album_title) if best_album_title else (
        getattr(rep, "album_key", "") or ""
    )
    if not canonical_key:
        # No album identity at all — use artist slug as fallback so the
        # /album/ route regex ([a-z0-9][a-z0-9-]*) is always satisfied.
        canonical_key = series_parse.slugify(rep.artist or rep.title or str(rep.message_id))
    # Determine display artist: single name if all tracks share one,
    # "Various Artists" for soundtracks/compilations with multiple performers.
    unique_artists = {t.artist for t in tracks if t.artist}
    if len(unique_artists) == 1:
        display_artist = unique_artists.pop()
    elif len(unique_artists) > 1:
        display_artist = "Various Artists"
    else:
        display_artist = ""
    return AlbumGroup(
        album_key=canonical_key,
        # Don't use display_artist as album_title fallback — "Various Artists"
        # as a title is misleading. Use track title as a last resort instead.
        album_title=best_album_title or rep.title or "",
        artist=display_artist,
        track_count=len(tracks),
        latest_message_id=max(t.message_id for t in tracks),
        poster_item=poster,
        has_thumb=any(t.has_thumb for t in tracks),
        max_file_size=max((t.file_size or 0) for t in tracks),
    )


def all_albums() -> List:
    """All AlbumGroups, newest-first.

    Buckets by slugify(album_title) when available so tracks with stale
    legacy album_keys (artist+album slugs) still group correctly without
    requiring a re-probe.
    """
    buckets: dict = {}
    for it in _items.values():
        if getattr(it, "media_kind", "") != "audio":
            continue
        at = getattr(it, "album_title", "") or ""
        ak = getattr(it, "album_key", "") or ""
        # Prefer normalised title slug; fall back to stored key.
        bucket_key = series_parse.slugify(at) if at else ak
        if bucket_key:
            buckets.setdefault(bucket_key, []).append(it)
    groups = [_build_album_group(tracks) for tracks in buckets.values()]
    return sorted(groups, key=lambda g: -g.latest_message_id)


def standalone_audio_tracks() -> List[HubItem]:
    """Audio items with no album affiliation — ungrouped singles."""
    return sorted(
        [it for it in _items.values()
         if getattr(it, "media_kind", "") == "audio"
         and not it.hidden
         and not (getattr(it, "album_key", "") or getattr(it, "album_title", ""))],
        key=lambda t: -t.message_id,
    )


def next_episode(item: HubItem) -> Optional[dict]:
    """Return watch URL + label for the episode after ``item``, or None."""
    if not item.series_key or item.episode is None:
        return None
    eps = episodes_for_series(item.series_key)
    for i, ep in enumerate(eps):
        if ep.message_id == item.message_id and i + 1 < len(eps):
            nxt = eps[i + 1]
            label = nxt.episode_title or nxt.title or f"Episode {nxt.episode}"
            return {
                "url": f"/watch/{nxt.secure_hash}{nxt.message_id}",
                "title": label,
                "season": nxt.season,
                "episode": nxt.episode,
            }
    return None


def prev_track(item: HubItem) -> Optional[dict]:
    """Return watch URL + label for the previous track in the album, or None."""
    ak = getattr(item, "album_key", "") or ""
    if not ak or getattr(item, "media_kind", "") != "audio":
        return None
    tracks = tracks_for_album(ak)
    for i, t in enumerate(tracks):
        if t.message_id == item.message_id and i > 0:
            prv = tracks[i - 1]
            return {
                "url": f"/watch/{prv.secure_hash}{prv.message_id}",
                "title": prv.title or prv.file_name or f"Track {prv.track_number or i}",
                "track_number": prv.track_number,
                "secure_hash": prv.secure_hash,
                "message_id": prv.message_id,
            }
    return None


def next_track(item: HubItem) -> Optional[dict]:
    """Return watch URL + label for the next track in the album, or None."""
    ak = getattr(item, "album_key", "") or ""
    if not ak or getattr(item, "media_kind", "") != "audio":
        return None
    tracks = tracks_for_album(ak)
    for i, t in enumerate(tracks):
        if t.message_id == item.message_id and i + 1 < len(tracks):
            nxt = tracks[i + 1]
            return {
                "url": f"/watch/{nxt.secure_hash}{nxt.message_id}",
                "title": nxt.title or nxt.file_name or f"Track {nxt.track_number or i + 2}",
                "track_number": nxt.track_number,
                "secure_hash": nxt.secure_hash,
                "message_id": nxt.message_id,
                "duration": nxt.duration,
            }
    return None


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
        if it.hidden:
            continue
        score = _search_score(ql, it)
        if score > 0:
            scored.append((score, it))

    if not scored:
        return []

    # Highest score first, ties broken by recency.
    scored.sort(key=lambda x: (-x[0], -x[1].message_id))

    # Collapse by group so series/albums/movies surface one card each.
    seen_series: set = set()
    seen_album:  set = set()
    seen_movie:  set = set()
    art_cache = group_art_cache_for(it for _score, it in scored)
    suggestions: List[dict] = []
    for score, it in scored:
        if it.series_key:
            if it.series_key in seen_series:
                continue
            seen_series.add(it.series_key)
            url   = f"/series/{it.series_key}"
            title = it.series_title or it.title
            kind  = "series"
        elif it.album_key:
            if it.album_key in seen_album:
                continue
            seen_album.add(it.album_key)
            url   = f"/album/{it.album_key}"
            title = it.album_title or it.title
            kind  = "album"
        elif it.movie_key:
            if it.movie_key in seen_movie:
                continue
            seen_movie.add(it.movie_key)
            url   = f"/movie/{it.movie_key}"
            title = it.title
            kind  = "movie"
        else:
            url   = f"/watch/{it.secure_hash}{it.message_id}"
            title = it.title
            kind  = "audio" if it.media_kind == "audio" else "movie"
        suggestions.append({
            "title": title,
            "year": it.year,
            "kind": kind,
            "url": url,
            "poster_path": poster_path_for_item(it, cache=art_cache),
            "secure_hash": it.secure_hash,
            "message_id": it.message_id,
            "media_kind": it.media_kind,
        })
        if len(suggestions) >= limit:
            break

    return suggestions


def card_for_tmdb_id(tmdb_id: int, kind: str = "") -> object:
    """Return the catalogue card for a TMDB ID, or None if not present.

    Returns a SeriesGroup/MovieGroup when multiple items share the same
    key, otherwise the raw HubItem.
    """
    matches = [it for it in _items.values()
               if it.tmdb_id == tmdb_id and not it.hidden]
    if not matches:
        return None
    first = matches[0]
    if first.series_key:
        eps = episodes_for_series(first.series_key)
        if eps:
            return _build_series_group(eps)
    if first.movie_key:
        variants = variants_for_movie(first.movie_key)
        if len(variants) >= 2:
            return _build_movie_group(variants)
    return first


def variants_for_movie(movie_key: str) -> List[HubItem]:
    """All uploads of a given movie, sorted newest first."""
    vs = [it for it in _items.values() if it.movie_key == movie_key and not it.hidden]
    vs.sort(key=lambda v: v.message_id, reverse=True)
    return vs


async def set_hidden(message_id: int, hidden: bool) -> bool:
    """Toggle the hidden flag on an item. Returns True if item found."""
    async with _lock:
        item = _items.get(message_id)
        if item is None:
            return False
        item.hidden = hidden
        _persist_unlocked()
    await _store_upsert(item)
    return True


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


def _is_hero_candidate(item: HubItem) -> bool:
    if item.hidden or (item.media_kind or "") == "audio":
        return False
    title_signal = item.series_title or item.title or item.file_name or ""
    if _is_generic_media_title(title_signal):
        return False
    return True


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
    by_recent = sorted(
        (it for it in _items.values() if _is_hero_candidate(it)),
        key=lambda it: -it.message_id,
    )

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


def _hub_int_env(name: str, default: int, *, lo: int = 1, hi: int = 50) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return max(lo, min(n, hi))


# Genre-shelf cap is env-tunable (``HUB_GENRE_SHELVES``, default 3).
# Bump it when the catalogue has lots of TMDB-enriched items and you
# want the landing page to read deeper without clicking through to a
# genre filter. Clamped to [1, 20] so a typo can't blow up rendering.
def _genre_shelf_count() -> int:
    return _hub_int_env("HUB_GENRE_SHELVES", 3, lo=1, hi=20)


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
        if it.hidden:
            continue
        # Audio items belong to the Music shelf, not Series/Movies.
        if getattr(it, "media_kind", "") == "audio":
            continue
        if it.series_key:
            series_buckets.setdefault(it.series_key, []).append(it)
        elif it.movie_key:
            movie_buckets.setdefault(it.movie_key, []).append(it)
        else:
            singles.append(it)

    series_groups = [_build_series_group(eps) for eps in series_buckets.values()]
    series_group_by_key = {group.series_key: group for group in series_groups}
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

    def card_item(card):
        return getattr(card, "poster_item", card)

    def card_identity(card) -> str:
        return (
            getattr(card, "series_key", "")
            or getattr(card, "movie_key", "")
            or getattr(card, "album_key", "")
            or str(_card_message_id(card))
        )

    out: List[dict] = []

    if all_cards:
        recent_items = newest(all_cards)
        # Don't show the full catalogue count as a badge — the shelf only
        # displays per_shelf items and has no "see all" link, so showing
        # the total (e.g. 127) is misleading. Show the displayed count.
        out.append({
            "name": "Recently added",
            "items": recent_items,
            # view=list bypasses the shelf landing and shows the flat
            # newest-first grid. /?sort=newest would be stripped to /
            # by _canonical_url (newest is the default sort), looping
            # back to the shelf view.
            "link": "/?view=list",
            "total": len(recent_items),
        })

    new_episode_updates: List[SeriesGroup] = []
    seen_episode_keys: set = set()
    seen_series_keys: set = set()
    for it in sorted(_items.values(), key=lambda item: -item.message_id):
        if it.hidden or getattr(it, "media_kind", "") == "audio" or not it.series_key:
            continue
        episode_key = (it.series_key, it.season, it.episode, it.episode_end)
        if episode_key in seen_episode_keys:
            continue
        seen_episode_keys.add(episode_key)
        if it.series_key in seen_series_keys:
            continue
        group = series_group_by_key.get(it.series_key)
        if not group:
            continue
        seen_series_keys.add(it.series_key)
        new_episode_updates.append(replace(group, new_episode_item=it))
        if len(new_episode_updates) >= per_shelf:
            break
    if new_episode_updates:
        out.append({
            "name": "New episodes",
            "items": new_episode_updates,
            "link": "/?view=series",
            "total": len(new_episode_updates),
        })

    if series_groups:
        series_items = newest(series_groups, key=lambda s: s.latest_message_id)
        out.append({
            "name": "Series",
            "items": series_items,
            "link": "/?view=series",
            "total": len(series_items),  # displayed count, not full catalogue
        })
    if all_movies:
        movie_items = newest(all_movies)
        out.append({
            "name": "Recently added movies",
            "items": movie_items,
            "link": "/?view=movies",
            "total": len(movie_items),
        })

    if all_cards:
        recent_keys = {card_identity(card) for card in newest(all_cards)}
        hidden_gems = []
        for card in all_cards:
            item = card_item(card)
            if card_identity(card) in recent_keys:
                continue
            if not getattr(item, "tmdb_id", None):
                continue
            if not ((getattr(item, "overview", "") or getattr(item, "description", "")) and getattr(item, "tmdb_genres", None)):
                continue
            hidden_gems.append(card)
        hidden_gems = sorted(
            hidden_gems,
            key=lambda card: (
                -(len(getattr(card_item(card), "tmdb_genres", None) or [])),
                _card_message_id(card),
            ),
        )[:per_shelf]
        if len(hidden_gems) >= 3:
            out.append({
                "name": "Hidden gems",
                "items": hidden_gems,
                "link": "/?sort=oldest",
                "total": len(hidden_gems),
            })

    # Music shelf — albums + standalone tracks
    _albums = all_albums()
    _singles = standalone_audio_tracks()
    _music_all = _albums + _singles
    if _music_all:
        _music_items = sorted(
            _music_all,
            key=lambda c: -(c.latest_message_id if hasattr(c, 'latest_message_id') else c.message_id),
        )[:per_shelf]
        out.append({
            "name": "Music",
            "items": _music_items,
            "link": "/?view=music",
            "total": len(_music_items),
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
        # promotion logic as the all_cards bucketing above. Cap from
        # env so operators can show more rows on heavily-enriched
        # catalogues.
        genre_rows = sorted(by_genre.items(), key=lambda kv: -len(kv[1]))[:_genre_shelf_count()]
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
                out.append({
                    "name": genre,
                    "items": row_cards,
                    "link": "/?" + urlencode({"genre": genre}),
                    "total": len(row_cards),  # displayed count
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

    No-op when the durable Mongo store is active — every mutation is
    already written through to Mongo, so the Telegram snapshot is
    redundant. This guard applies regardless of how this function was
    called (schedule_snapshot, enrich_all, reindex, etc.).
    """
    if _store_active():
        return None
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
    global _latest_seen_id, _snapshot_msg_id
    result = await state_doc.load(bot)
    if result is None:
        return False
    payload, snapshot_id = result
    items_data = payload.get("items") or []
    persisted_latest = int(payload.get("latest_seen_id") or 0)
    loaded = 0
    async with _lock:
        for d in items_data:
            try:
                item = _from_serializable(d)
                _items[item.message_id] = item
                _hash_map[item.secure_hash] = item.message_id
                loaded += 1
            except Exception:
                continue
        local_max = max((it.message_id for it in _items.values()), default=0)
        _latest_seen_id = max(_latest_seen_id, persisted_latest, local_max)
        # Remember the snapshot's message id so the next snapshot_to_telegram
        # call can delete it before uploading the replacement. Without this
        # hint, cold-start saves can't dedup and BIN_CHANNEL accumulates
        # an unbounded stream of stale snapshot docs.
        _snapshot_msg_id = snapshot_id
        _persist_unlocked()
    logging.info(
        "media_index: restored %d entries from Telegram snapshot (latest=%d)",
        loaded, _latest_seen_id,
    )
    return loaded > 0


async def persist_canonical_to_bin(bot, message_id: int) -> bool:
    """Edit a BIN_CHANNEL message's caption to reflect the HubItem's
    current canonical state.

    Originally this was the durable backup: /tmp wipe + container
    restart could rehydrate the catalogue by walking BIN_CHANNEL and
    parsing structured captions. With ``STORE_BACKEND=mongo`` Mongo
    IS the durable store, so the caption rewrite is pure cosmetics
    — and it costs us MESSAGE_AUTHOR_REQUIRED errors on every
    legacy forwarded entry. Short-circuit when Mongo is active.

    Handles FloodWait by sleeping and retrying; MessageNotModified
    counts as success (the caption is already what we want). When
    Telegram returns MessageIdInvalid the in-memory entry is stale
    (the source message was deleted on the channel) — we drop it
    from the catalogue so subsequent seeds don't repeat the work.
    Returns True on successful edit (or skip when Mongo handles
    durability), False on hard failure.
    """
    # Mongo is the source of truth — BIN captions are now redundant.
    if _store_active():
        return True
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

    Fields listed in item.admin_locked are skipped — they were manually
    set by the admin and must survive bulk re-enrichment.  Lockable:
    "title", "year", "series_title".

    Genres are merged into the tag set so the existing tag filter and
    tag-cloud surface them, without dropping any user-set tags.
    """
    locked = set(item.admin_locked or [])

    # TMDB-sourced metadata — always written (purely TMDB-owned fields that
    # the admin would never manually set in the edit modal).
    item.tmdb_id = hit.tmdb_id
    item.tmdb_kind = hit.kind
    item.imdb_id = hit.imdb_id
    item.tmdb_vote_average = float(hit.vote_average or 0)
    item.tmdb_vote_count = int(hit.vote_count or 0)
    item.tmdb_vote_checked_at = time.time()
    item.poster_path = hit.poster_path
    item.backdrop_path = hit.backdrop_path
    item.overview = hit.overview
    item.tmdb_genres = list(hit.genres)
    item.cast = list(hit.cast)
    item.director = hit.director
    item.enriched_at = time.time()

    # Title and year: skip if the admin manually locked them.
    # TV episode titles are always preserved (show name ≠ episode title).
    if "title" not in locked:
        if hit.kind != "tv" and hit.title:
            item.title = hit.title
    if "year" not in locked:
        if hit.year and hit.kind != "tv":
            item.year = hit.year

    # Series/movie grouping.  If series_title is locked the admin chose
    # a specific grouping — skip the recomputation entirely so the
    # series_key/movie_key they set doesn't get reverted.
    if "series_title" in locked:
        # Keep existing grouping; only update movie_key if somehow missing.
        if not item.series_key and not item.movie_key:
            item.movie_key = compute_movie_key(item.title, item.year, item.file_name)
    else:
        # Recompute grouping keys against the canonical title.
        sm = series_parse.parse(item.file_name) or series_parse.parse(item.title)
        if sm:
            # Filename or title carries an SxxEyy / 1x03 / Season N pattern.
            item.series_key = sm.key
            item.series_title = hit.title if hit.kind == "tv" and hit.title else sm.title
            # Preserve admin-set season/episode (when non-None). Only fall back
            # to the filename parser's values when the item doesn't already
            # have an explicit number — otherwise an admin edit gets silently
            # reverted every time enrich_with_tmdb_id runs.
            if item.season is None:
                item.season = sm.season
            if item.episode is None:
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
            # Preserve admin-set season/episode (same rule as the SxxEyy branch).
            if item.season is None or item.episode is None:
                inferred_ep = (
                    series_parse.infer_episode_loose(item.file_name)
                    or series_parse.infer_episode_loose(item.title)
                )
                if inferred_ep is not None:
                    if item.season is None:
                        item.season = 1
                    if item.episode is None:
                        item.episode = inferred_ep
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


def clear_episode_tmdb_fields(item: HubItem) -> None:
    item.episode_title = ""
    item.episode_overview = ""
    item.episode_still_path = ""
    item.episode_air_date = ""
    item.episode_tmdb_vote_average = 0.0
    item.episode_tmdb_vote_count = 0
    item.episode_tmdb_vote_checked_at = 0.0


async def _fill_episode_metadata(item: HubItem) -> bool:
    """For a TV episode with known (season, episode), copy TMDB's
    per-episode name/overview/still onto the item.

    Returns True on a populated update. Uses tmdb.fetch_season's cache
    so all episodes of a season collapse onto one network call —
    the second-and-beyond episodes of a 500-ep show pay nothing.

    Two-pass lookup:
      1. Direct (season, episode) hit against the matching season.
      2. Fallback: if (1) misses, treat ``episode`` as an absolute
         episode count across the whole series and walk seasons in
         order. This catches anime/long-running shows where the
         filename uses global episode numbering (Naruto Shippuden
         "S16 EP351" really means the 351st episode of the entire
         show, not the 351st of season 16).
    """
    if (item.tmdb_kind != "tv" or not item.tmdb_id
            or item.season is None or item.episode is None):
        return False
    ep = None
    payload = await tmdb.fetch_season(item.tmdb_id, item.season)
    if payload:
        ep = tmdb.episode_from_season(payload, item.episode)
    if ep is None:
        # Fallback to absolute episode numbering.
        ep = await _resolve_absolute_episode(item.tmdb_id, item.episode)
    if not ep:
        return False
    new_title = (ep.get("name") or "").strip()
    new_overview = (ep.get("overview") or "").strip()
    new_still = (ep.get("still_path") or "").strip()
    new_air = (ep.get("air_date") or "").strip()
    try:
        new_vote_average = float(ep.get("vote_average") or 0)
    except (TypeError, ValueError):
        new_vote_average = 0.0
    try:
        new_vote_count = int(ep.get("vote_count") or 0)
    except (TypeError, ValueError):
        new_vote_count = 0
    changed = (
        new_title != item.episode_title
        or new_overview != item.episode_overview
        or new_still != item.episode_still_path
        or new_air != item.episode_air_date
        or new_vote_average != item.episode_tmdb_vote_average
        or new_vote_count != item.episode_tmdb_vote_count
        or not item.episode_tmdb_vote_checked_at
    )
    item.episode_title = new_title
    item.episode_overview = new_overview
    item.episode_still_path = new_still
    item.episode_air_date = new_air
    item.episode_tmdb_vote_average = new_vote_average
    item.episode_tmdb_vote_count = new_vote_count
    item.episode_tmdb_vote_checked_at = time.time()
    return changed


async def _resolve_absolute_episode(tmdb_id: int, abs_episode: int) -> Optional[dict]:
    """Walk seasons 1..N accumulating episode counts until we hit
    ``abs_episode``. Used as a fallback when a show's filename uses
    global episode numbering instead of per-season (common in anime
    and long-running serials).

    Each season fetch is cached at the tmdb module level, so for a
    bulk pass over a 500-episode show we do at most ~one fetch per
    season, then every absolute-lookup against that season is free.
    Caps at 50 seasons as a safety so a borked tmdb_id can't loop.
    """
    seen = 0
    for season_num in range(1, 51):
        payload = await tmdb.fetch_season(tmdb_id, season_num)
        if not payload:
            # No more seasons (TMDB 404'd) — give up.
            return None
        episodes = payload.get("episodes") or []
        if not episodes:
            return None
        for ep in episodes:
            seen += 1
            if seen == abs_episode:
                return ep
    return None


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

    # TMDB only covers films and TV — never enrich audio/music items.
    if item.media_kind == "audio":
        return False

    _search_signal = (item.series_title or item.title or "").strip()

    hit = None
    if item.tmdb_id and item.tmdb_kind in ("movie", "tv"):
        hit = await tmdb.fetch_by_id(item.tmdb_id, item.tmdb_kind)

    # Don't burn a TMDB search on items with no meaningful title.
    # Exact/admin TMDB IDs are fetched above; this guard only blocks
    # search-based matching for generic names like "Untitled".
    if hit is None and (len(_search_signal) < 3 or _is_generic_media_title(_search_signal)):
        async with _lock:
            item.enriched_at = time.time()
            _persist_unlocked()
        await _store_upsert(item)
        return False

    if hit is None and item.series_key and item.series_title:
        # TV: lookup the show. series_title is already clean per series.parse.
        hit = await tmdb.lookup_series(item.series_title, item.year)
    elif hit is None:
        # Movie: try the cleaned title forms. The earlier 'drop leading
        # words' fallback would generate generic variants like "The
        # Movie" / "Movie" which TMDB happily matched against random
        # films (Plankton: The Movie 2025 collided with F1 The Movie
        # 2025). Now that clean_for_search handles the channel-prefix
        # cases on its own, only the full-base queries are tried; if
        # they all miss, admin edit is the recovery path.
        # Try the raw item.title FIRST. clean_for_search aggressively strips
        # tokens it thinks are release tags / language markers — e.g. it
        # turns "Johnny English" into "Johnny" because "English" looks like a
        # language tag. That would replace the admin's careful title with a
        # mismatched TMDB record. Honour the operator's exact title before
        # falling back to cleaned variants.
        candidates: list = []
        seen: set = set()
        if item.title and len(item.title) >= 2:
            candidates.append(item.title)
            seen.add(item.title)
        for raw in (item.title, item.file_name):
            cleaned = clean_for_search(raw, item.file_name)
            if cleaned and cleaned not in seen and len(cleaned) >= 2:
                candidates.append(cleaned)
                seen.add(cleaned)

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
        await _store_upsert(item)
        return False

    async with _lock:
        _apply_tmdb_to_item(item, hit)
        clear_episode_tmdb_fields(item)
        # For TV series, propagate the show-level enrichment (tmdb_id,
        # poster, backdrop, overview, genres, series_title) to every
        # other episode sharing the same series_key. Without this, only
        # the explicitly-enriched episode gets the TMDB id, so the next
        # 'Fetch episode details' pass can't fill per-episode metadata
        # on its siblings.
        propagated: list = []
        if hit.kind == "tv" and item.tmdb_id and item.series_key:
            for sib in _items.values():
                if sib.message_id == item.message_id:
                    continue
                if sib.series_key != item.series_key:
                    continue
                if sib.tmdb_id == item.tmdb_id:
                    continue
                sib.tmdb_id      = item.tmdb_id
                sib.tmdb_kind    = item.tmdb_kind
                sib.imdb_id      = item.imdb_id      or sib.imdb_id
                sib.tmdb_vote_average = item.tmdb_vote_average
                sib.tmdb_vote_count = item.tmdb_vote_count
                sib.tmdb_vote_checked_at = item.tmdb_vote_checked_at
                sib.poster_path  = item.poster_path  or sib.poster_path
                sib.backdrop_path= item.backdrop_path or sib.backdrop_path
                sib.tmdb_genres  = list(item.tmdb_genres) or sib.tmdb_genres
                sib.overview     = sib.overview or item.overview
                sib.series_title = item.series_title or sib.series_title
                sib.enriched_at  = time.time()
                # Clear stale per-episode fields so the upcoming
                # _fill_episode_metadata pass re-resolves them against
                # the freshly-stamped TMDB id.
                clear_episode_tmdb_fields(sib)
                propagated.append(sib)
        _persist_unlocked()

    # Per-episode enrichment is async — do it outside the lock so the
    # TMDB season fetch doesn't block other catalogue mutations.
    await _fill_episode_metadata(item)
    # Same per-episode fill for the propagated siblings. fetch_season is
    # cached at the (tmdb_id, season) level so a 70-episode show only
    # costs one TMDB call per season regardless of sibling count.
    for sib in propagated:
        try:
            await _fill_episode_metadata(sib)
            await _store_upsert(sib)
        except Exception:
            logging.exception(
                "enrich_one: sibling fill failed for bin:%d", sib.message_id,
            )
    # Fetch YouTube trailer key if not already set (one extra TMDB call
    # per title but cached at the series/movie level by tmdb module).
    if not item.trailer_key and item.tmdb_id:
        item.trailer_key = await tmdb.fetch_trailer(
            item.tmdb_id, item.tmdb_kind or "movie"
        )
    await persist_now()
    await _store_upsert(item)

    if bot is not None:
        # Best-effort caption write-back. A failure here doesn't undo the
        # in-memory enrichment; the next enrich pass will retry.
        await persist_canonical_to_bin(bot, message_id)
        # Coalesce snapshot saves — bulk uploads fire enrich_one per
        # episode and we don't want 70 snapshot writes back-to-back.
        # The debouncer collapses them into one save after the burst
        # quiets down.
        schedule_snapshot(bot)
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
        clear_episode_tmdb_fields(item)
        # Propagate TV series enrichment to every sibling sharing the
        # same series_key — same logic as enrich_one(). One manual TMDB
        # override should stamp the whole series, not just one episode.
        propagated: list = []
        if hit.kind == "tv" and item.tmdb_id and item.series_key:
            for sib in _items.values():
                if sib.message_id == item.message_id:
                    continue
                if sib.series_key != item.series_key:
                    continue
                if sib.tmdb_id == item.tmdb_id:
                    continue
                sib.tmdb_id      = item.tmdb_id
                sib.tmdb_kind    = item.tmdb_kind
                sib.imdb_id      = item.imdb_id      or sib.imdb_id
                sib.tmdb_vote_average = item.tmdb_vote_average
                sib.tmdb_vote_count = item.tmdb_vote_count
                sib.tmdb_vote_checked_at = item.tmdb_vote_checked_at
                sib.poster_path  = item.poster_path  or sib.poster_path
                sib.backdrop_path= item.backdrop_path or sib.backdrop_path
                sib.tmdb_genres  = list(item.tmdb_genres) or sib.tmdb_genres
                sib.overview     = sib.overview or item.overview
                sib.series_title = item.series_title or sib.series_title
                sib.enriched_at  = time.time()
                # Clear stale per-episode fields so the next pass re-resolves.
                clear_episode_tmdb_fields(sib)
                propagated.append(sib)
        _persist_unlocked()
    await _fill_episode_metadata(item)
    for sib in propagated:
        try:
            await _fill_episode_metadata(sib)
            await _store_upsert(sib)
        except Exception:
            logging.exception(
                "enrich_with_tmdb_id: sibling fill failed for bin:%d", sib.message_id,
            )
    await persist_now()
    await _store_upsert(item)
    if bot is not None:
        await persist_canonical_to_bin(bot, message_id)
        # Manual admin override — coalesce snapshot like the auto path.
        # If the admin makes several override edits in a row, only one
        # snapshot lands at the end.
        schedule_snapshot(bot)
    return True


_episode_fill_state: dict = {
    "running": False, "done": 0, "total": 0, "filled": 0,
    "started_at": 0.0, "finished_at": 0.0,
}


def episode_fill_state() -> dict:
    return dict(_episode_fill_state)


# Mongo migration progress state. Same shape as the other pipelines
# so the admin status endpoint can serialise it uniformly.
_migrate_state: dict = {
    "running": False, "done": 0, "total": 0,
    "phase": "",          # "connecting" / "indexing" / "writing" / "done" / "failed"
    "error": "",          # populated on failure for the flash message
    "started_at": 0.0, "finished_at": 0.0,
}


def migrate_state() -> dict:
    return dict(_migrate_state)


async def migrate_to_mongo(uri: str, db_name: str,
                           items_coll: str, meta_coll: str) -> dict:
    """Background-friendly Mongo migration with progress + connectivity
    check.

    Phases:
      1. ``connecting``  — instantiate MongoStore, run init() (creates
                           indexes), and ping the cluster via a 1-doc
                           limit query against the items collection. A
                           bad URI / wrong region / blocked IP fails
                           here, before we touch the catalogue.
      2. ``indexing``    — snapshot the in-memory dict under the lock
                           and convert each item to a doc. ``total`` is
                           known after this phase.
      3. ``writing``     — bulk-upsert in batches of 500, updating
                           ``done`` after each batch so the admin
                           progress bar advances smoothly even on
                           large catalogues.

    Returns ``{"total", "migrated", "error"}``.
    """
    if _migrate_state["running"]:
        return {"already_running": True}

    _migrate_state.update(
        running=True, done=0, total=0,
        phase="connecting", error="",
        started_at=time.time(), finished_at=0.0,
    )

    try:
        client = _store_module.MongoStore(uri, db_name, items_coll, meta_coll)
        await client.init()
        # Connectivity probe — Atlas free tier can lazily build the
        # cluster connection, so we issue a cheap query and let any
        # auth/network error surface before we walk the catalogue.
        await client._items.find_one({}, projection={"_id": True})
    except Exception as exc:
        logging.exception("migrate: Mongo connection probe failed")
        _migrate_state.update(
            running=False, phase="failed",
            error=f"{exc.__class__.__name__}: {exc}",
            finished_at=time.time(),
        )
        return {"total": 0, "migrated": 0,
                "error": _migrate_state["error"]}

    _migrate_state["phase"] = "indexing"
    async with _lock:
        docs = [_to_serializable(it) for it in _items.values()]
    _migrate_state["total"] = len(docs)

    _migrate_state["phase"] = "writing"
    BATCH = 500
    try:
        from pymongo import ReplaceOne
        for i in range(0, len(docs), BATCH):
            batch = docs[i:i + BATCH]
            ops = [
                ReplaceOne({"message_id": int(d["message_id"])}, d, upsert=True)
                for d in batch
                if int(d.get("message_id") or 0) > 0
            ]
            if ops:
                await client._items.bulk_write(ops, ordered=False)
            _migrate_state["done"] = min(i + BATCH, len(docs))
        # Persist the high-water mark so the next boot from Mongo
        # resumes the seed walk from the right id.
        await client.set_meta("latest_seen_id", _latest_seen_id)
    except Exception as exc:
        logging.exception("migrate: bulk write failed")
        _migrate_state.update(
            running=False, phase="failed",
            error=f"{exc.__class__.__name__}: {exc}",
            finished_at=time.time(),
        )
        return {"total": _migrate_state["total"],
                "migrated": _migrate_state["done"],
                "error": _migrate_state["error"]}

    _migrate_state.update(
        running=False, phase="done", finished_at=time.time(),
    )
    return {"total": _migrate_state["total"],
            "migrated": _migrate_state["done"], "error": ""}


async def fill_episode_details(bot=None) -> dict:
    """Backfill TMDB per-episode metadata for TV rows that don't have
    it yet. Cheap thanks to the season-level cache in tmdb.fetch_season
    — one network call per (tv_id, season), no matter how many
    episodes share it.
    """
    if _episode_fill_state["running"]:
        return {"already_running": True}
    if not tmdb.is_configured():
        return {"skipped_no_api_key": True}
    targets = [
        it for it in _items.values()
        if it.tmdb_kind == "tv" and it.tmdb_id
           and it.season is not None and it.episode is not None
           and (not it.episode_title or not it.episode_tmdb_vote_checked_at)
    ]
    _episode_fill_state.update(
        running=True, done=0, total=len(targets), filled=0,
        started_at=time.time(), finished_at=0.0,
    )
    try:
        for it in targets:
            try:
                changed = await _fill_episode_metadata(it)
                if changed:
                    _episode_fill_state["filled"] += 1
            except Exception:
                logging.debug("episode fill failed for bin:%d",
                              it.message_id, exc_info=True)
            _episode_fill_state["done"] += 1
        await persist_now()
        # Flush touched rows to Mongo. We re-upsert the whole TV
        # subset for simplicity; the upsert is keyed by message_id
        # so this is idempotent.
        if _store_active():
            try:
                docs = [
                    _to_serializable(it) for it in _items.values()
                    if it.tmdb_kind == "tv"
                ]
                await _store.upsert_many(docs)
            except Exception:
                logging.exception(
                    "media_index: post-fill_episodes Mongo flush failed"
                )
        if bot is not None:
            schedule_snapshot(bot)
    finally:
        _episode_fill_state["running"] = False
        _episode_fill_state["finished_at"] = time.time()
    return {
        "total": _episode_fill_state["total"],
        "filled": _episode_fill_state["filled"],
    }


async def clear_audio_tmdb_mismatches() -> int:
    """Strip TMDB fields from any audio item that was previously mis-enriched.

    Returns the number of items fixed. Safe to call multiple times.
    """
    fixed = 0
    to_upsert = []
    async with _lock:
        for item in _items.values():
            if item.media_kind == "audio" and item.tmdb_id:
                item.tmdb_id = None
                item.tmdb_kind = ""
                item.imdb_id = ""
                item.tmdb_vote_average = 0.0
                item.tmdb_vote_count = 0
                item.tmdb_vote_checked_at = 0.0
                item.poster_path = ""
                item.backdrop_path = ""
                item.overview = ""
                item.tmdb_genres = []
                item.enriched_at = 0.0
                to_upsert.append(item)
                fixed += 1
        if fixed:
            _persist_unlocked()
    for item in to_upsert:
        await _store_upsert(item)
    return fixed


def _needs_tmdb_vote_backfill(item: HubItem) -> bool:
    return bool(
        item.tmdb_id
        and item.tmdb_kind in ("movie", "tv")
        and not getattr(item, "tmdb_vote_checked_at", 0)
    )


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
        if it.media_kind != "audio"
        and (force or not it.tmdb_id or _needs_tmdb_vote_backfill(it))
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


def _needs_credits_backfill(item: HubItem) -> bool:
    if (item.media_kind or "") == "audio":
        return False
    if not item.tmdb_id or item.tmdb_kind not in ("movie", "tv"):
        return False
    missing_cast = not bool(item.cast)
    # Show-level TV directors are often absent in TMDB. Do not keep
    # retrying every TV record just because director is blank.
    missing_director = item.tmdb_kind == "movie" and not bool(item.director)
    return missing_cast or missing_director


def needs_credits_backfill(item: HubItem) -> bool:
    return _needs_credits_backfill(item)


def _needs_tmdb_metadata_backfill(item: HubItem) -> bool:
    return _needs_credits_backfill(item) or _needs_tmdb_vote_backfill(item)


def _apply_credits_backfill(item: HubItem, hit: "tmdb.TMDBHit") -> bool:
    changed = False
    if not item.cast and hit.cast:
        item.cast = list(hit.cast)
        changed = True
    if not item.director and hit.director:
        item.director = hit.director
        changed = True
    if not item.imdb_id and hit.imdb_id:
        item.imdb_id = hit.imdb_id
        changed = True
    if not item.tmdb_vote_checked_at:
        item.tmdb_vote_average = float(hit.vote_average or 0)
        item.tmdb_vote_count = int(hit.vote_count or 0)
        item.tmdb_vote_checked_at = time.time()
        changed = True
    return changed


async def backfill_missing_credits(bot=None) -> dict:
    """Fill missing cast/director/rating data from existing TMDB IDs only.

    Unlike ``enrich_all(force=True)``, this never searches by title and
    never updates titles, years, grouping keys, posters, backdrops,
    overview, or genres. It is intended as the low-risk operational pass
    that repairs already-enriched items without rematching them.
    """
    if _credits_state["running"]:
        return {"already_running": True, "total": len(_items)}
    if _enrich_state["running"]:
        return {"already_running": True, "blocked_by_enrichment": True}
    if not tmdb.is_configured():
        return {"total": 0, "updated": 0, "skipped_no_api_key": True}

    targets = [
        mid for mid, item in list(_items.items())
        if _needs_tmdb_metadata_backfill(item)
    ]
    _credits_state.update(
        running=True,
        done=0,
        total=len(targets),
        updated=0,
        failed=0,
        started_at=time.time(),
        finished_at=0.0,
        last_title="",
    )

    try:
        for mid in targets:
            item = _items.get(mid)
            if item is None:
                _credits_state["done"] += 1
                continue
            _credits_state["last_title"] = item.title or item.file_name or f"bin:{mid}"
            try:
                hit = await tmdb.fetch_by_id(int(item.tmdb_id), item.tmdb_kind)
                if (
                    hit is None
                    or int(hit.tmdb_id) != int(item.tmdb_id)
                    or hit.kind != item.tmdb_kind
                ):
                    _credits_state["failed"] += 1
                    continue
                changed = False
                updated_item = None
                async with _lock:
                    current = _items.get(mid)
                    if current is not None:
                        changed = _apply_credits_backfill(current, hit)
                        updated_item = current
                        if changed:
                            _persist_unlocked()
                if changed and updated_item is not None:
                    await _store_upsert(updated_item)
                    _credits_state["updated"] += 1
            except Exception:
                logging.exception("credits backfill failed for bin:%d", mid)
                _credits_state["failed"] += 1
            finally:
                _credits_state["done"] += 1
    finally:
        _credits_state["running"] = False
        _credits_state["finished_at"] = time.time()

    if bot is not None and _credits_state["updated"]:
        schedule_snapshot(bot)

    return {
        "total": len(targets),
        "updated": _credits_state["updated"],
        "failed": _credits_state["failed"],
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

    # Push every (potentially-changed) row to Mongo in one bulk write.
    if _store_active():
        try:
            docs = [_to_serializable(it) for it in _items.values()]
            await _store.upsert_many(docs)
        except Exception:
            logging.exception("media_index: post-reindex Mongo flush failed")

    if bot is not None:
        try:
            await snapshot_to_telegram(bot)  # No-op when Mongo active.
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


def distinct_genres() -> List[str]:
    """TMDB-derived genres present in the catalogue, ordered by frequency.

    Powers the hub's Genre filter dropdown — only shows genres that
    actually have at least one item, so the list doesn't include the
    full TMDB taxonomy.
    """
    counter: dict = {}
    for it in _items.values():
        for g in it.tmdb_genres or []:
            counter[g] = counter.get(g, 0) + 1
    return [g for g, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def tag_cloud(limit: int = 30) -> List[Tuple[str, int]]:
    """Most-used tags with usage counts."""
    counter: Counter = Counter()
    for it in _items.values():
        counter.update(it.tags)
    return counter.most_common(limit)


def size() -> int:
    return len(_items)


def stats() -> dict:
    """Single-pass aggregation of catalogue health metrics for /admin.

    Cheap at any reasonable scale — we walk ``_items`` once and bucket
    into dict counters. At 10K items this is ~5ms; at 100K it'd be
    ~50ms which is still fine for an on-demand admin call.

    Returned shape::

        {
          "total": 127,
          "total_size_bytes": 12345678901,
          "kinds": {"series_episodes": 60, "movie_variants": 50,
                    "standalone": 17},
          "quality_buckets": [("1080p", 40), ("720p", 30), ("4K", 5),
                              ("480p", 2), ("unknown", 50)],
          "enrichment": {"enriched": 90, "attempted_no_match": 10,
                         "never_attempted": 27},
          "codec_health": {"probed_playable": 60, "probed_unplayable": 8,
                           "never_probed": 59},
          "top_genres": [("Action", 25), ("Drama", 18), ...],
          "missing_poster": 12,
          "missing_thumb": 4,
          "duplicate_groups": 3,        # secure_hash uniques w/ >1 item
          "duplicate_extras": 5,        # extras that would be deleted
        }
    """
    total = 0
    total_size = 0
    series_eps = 0
    movie_groups: dict = {}
    standalone = 0
    quality_counts: dict = {}
    enriched = 0
    attempted_no_match = 0
    never_attempted = 0
    probed_playable = 0
    probed_unplayable = 0
    never_probed = 0
    genre_counts: dict = {}
    missing_poster = 0
    missing_thumb = 0
    by_hash: dict = {}
    audio_count = 0
    album_keys_seen: set = set()
    from main.utils.codec_probe import is_browser_playable
    for it in _items.values():
        total += 1
        total_size += it.file_size or 0
        if it.series_key:
            series_eps += 1
        elif it.movie_key:
            movie_groups.setdefault(it.movie_key, []).append(it)
        else:
            standalone += 1
        if getattr(it, "media_kind", "") == "audio":
            audio_count += 1
            ak = getattr(it, "album_key", "")
            if ak:
                album_keys_seen.add(ak)
        qb = it.quality or "unknown"
        quality_counts[qb] = quality_counts.get(qb, 0) + 1
        if it.tmdb_id:
            enriched += 1
        elif it.enriched_at and it.enriched_at > 0:
            attempted_no_match += 1
        else:
            never_attempted += 1
        if it.probed_at and it.probed_at > 0:
            if is_browser_playable(it.video_codec or "", it.pix_fmt or ""):
                probed_playable += 1
            else:
                probed_unplayable += 1
        else:
            never_probed += 1
        for g in (it.tmdb_genres or []):
            genre_counts[g] = genre_counts.get(g, 0) + 1
        if it.tmdb_id and not it.poster_path:
            missing_poster += 1
        # "Missing thumbnail" — match the admin /no-thumb filter:
        # items uploaded as documents (no native Telegram thumbnail AND
        # no duration) where the ffmpeg fallback usually fails too.
        # Plain has_thumb=False with duration>0 still gets a generated
        # thumb via /thumb/* so it's not actually "missing" on the UI.
        if not it.has_thumb and not it.duration:
            missing_thumb += 1
        # Joint key (secure_hash, file_size) — secure_hash alone is the
        # first 6 chars of file_unique_id and shares ~4 chars of
        # constant prefix across bot-uploaded media, so 6-char hash
        # collisions are real at any non-trivial catalogue size.
        # file_size makes the combined key effectively collision-free.
        if it.secure_hash and it.file_size:
            by_hash.setdefault((it.secure_hash, it.file_size), []).append(it)
    # Movie-variant collapse: 2+ uploads of the same film count as
    # one "movie group", the rest become standalone-equivalent.
    # Movie counts:
    #   movie_items         — every item that has a movie_key, regardless
    #                          of how many variants of that movie exist.
    #                          Used so series_eps + movies + standalone
    #                          adds up to the total catalogue size.
    #   movie_variant_extras — extras across multi-variant groups, exposed
    #                          for the "duplicate uploads" / variant
    #                          insight. NOT used in the headline composition.
    movie_items = sum(len(v) for v in movie_groups.values())
    movie_variant_groups = sum(1 for v in movie_groups.values() if len(v) >= 2)
    movie_variant_extras = sum(len(v) - 1 for v in movie_groups.values() if len(v) >= 2)
    # Quality order — keep ranked from highest resolution down with
    # "unknown" last for cleaner rendering.
    quality_rank = ["4K", "1080p", "720p", "480p"]
    quality_buckets = [
        (q, quality_counts.get(q, 0)) for q in quality_rank if quality_counts.get(q)
    ]
    if quality_counts.get("unknown"):
        quality_buckets.append(("unknown", quality_counts["unknown"]))
    top_genres = sorted(
        genre_counts.items(), key=lambda kv: (-kv[1], kv[0]),
    )[:10]
    duplicate_groups = sum(1 for v in by_hash.values() if len(v) > 1)
    duplicate_extras = sum(len(v) - 1 for v in by_hash.values() if len(v) > 1)
    return {
        "total": total,
        "total_size_bytes": total_size,
        "kinds": {
            "series_episodes": series_eps,
            # All items with a movie_key, including single-variant movies.
            # series_eps + movies + standalone == total catalogue size.
            "movies": movie_items,
            # Number of multi-variant groups (movies with >=2 uploads) and
            # the extra-upload count across those groups — surfaced for the
            # 'Issues to clean up' / dedupe view.
            "movie_variant_groups": movie_variant_groups,
            "movie_variant_extras": movie_variant_extras,
            "standalone": standalone,
        },
        "quality_buckets": quality_buckets,
        "enrichment": {
            "enriched": enriched,
            "attempted_no_match": attempted_no_match,
            "never_attempted": never_attempted,
        },
        "codec_health": {
            "probed_playable": probed_playable,
            "probed_unplayable": probed_unplayable,
            "never_probed": never_probed,
        },
        "top_genres": top_genres,
        "missing_poster": missing_poster,
        "missing_thumb": missing_thumb,
        "duplicate_groups": duplicate_groups,
        "duplicate_extras": duplicate_extras,
        "audio_count": audio_count,
        "album_count": len(album_keys_seen),
    }


def dashboard_stats() -> dict:
    """Richer aggregation for the /admin/dashboard view.

    Builds on top of stats() with insights that don't belong in the slim
    inline summary: storage breakdown by quality + codec, recent additions,
    largest items, top series by episode count, year distribution.
    """
    base = stats()
    storage_by_quality: dict = {}
    storage_by_codec: dict = {}
    year_buckets: dict = {}
    series_episode_counts: dict = {}
    series_titles: dict = {}
    recent: list = []
    largest: list = []
    metadata_quality = {
        "video_items": 0,
        "tmdb_enriched_video_items": 0,
        "missing_tmdb_metadata": 0,
        "missing_credits": 0,
        "missing_ratings": 0,
        "missing_tmdb_id": 0,
        "missing_overview": 0,
        "missing_year": 0,
        "missing_cast": 0,
        "missing_episode_metadata": 0,
        "missing_playback_markers": 0,
        "health_score": 100,
    }

    def _has_range(start, end) -> bool:
        try:
            return float(end or 0) > float(start or 0)
        except (TypeError, ValueError):
            return False

    for it in _items.values():
        size = it.file_size or 0
        q = it.quality or "unknown"
        storage_by_quality[q] = storage_by_quality.get(q, 0) + size
        # Three distinct codec states:
        #   - named codec     → bucket by codec name
        #   - probed but blank → "unknown" (ffprobe ran, found no video stream)
        #   - probed_at == 0  → "not probed"
        if it.video_codec:
            codec = it.video_codec.lower()
        elif it.probed_at and it.probed_at > 0:
            codec = "unknown"
        else:
            codec = "not probed"
        storage_by_codec[codec] = storage_by_codec.get(codec, 0) + size
        if it.year:
            decade = (it.year // 10) * 10
            year_buckets[decade] = year_buckets.get(decade, 0) + 1
        if it.series_key:
            series_episode_counts[it.series_key] = series_episode_counts.get(it.series_key, 0) + 1
            if it.series_key not in series_titles and it.series_title:
                series_titles[it.series_key] = it.series_title
        if getattr(it, "media_kind", "") != "audio":
            metadata_quality["video_items"] += 1
            if it.tmdb_id:
                metadata_quality["tmdb_enriched_video_items"] += 1
                needs_credits = _needs_credits_backfill(it)
                needs_ratings = _needs_tmdb_vote_backfill(it)
                if needs_credits:
                    metadata_quality["missing_credits"] += 1
                if needs_ratings:
                    metadata_quality["missing_ratings"] += 1
                if needs_credits or needs_ratings:
                    metadata_quality["missing_tmdb_metadata"] += 1
            else:
                metadata_quality["missing_tmdb_id"] += 1
            if not (it.overview or it.description):
                metadata_quality["missing_overview"] += 1
            if not it.year:
                metadata_quality["missing_year"] += 1
            if it.tmdb_id and not (it.cast or it.director):
                metadata_quality["missing_cast"] += 1
            if it.series_key and it.tmdb_id and not (
                it.episode_title or it.episode_overview or it.episode_still_path
            ):
                metadata_quality["missing_episode_metadata"] += 1
            if (it.duration or 0) >= 20 * 60 and not (
                it.chapters or _has_range(it.intro_start, it.intro_end)
                or _has_range(it.recap_start, it.recap_end)
            ):
                metadata_quality["missing_playback_markers"] += 1
        recent.append(it)
        largest.append(it)

    recent.sort(key=lambda x: x.message_id, reverse=True)
    largest.sort(key=lambda x: x.file_size or 0, reverse=True)
    top_series = sorted(
        series_episode_counts.items(), key=lambda kv: (-kv[1], kv[0]),
    )[:10]

    quality_order = ["4K", "1080p", "720p", "480p", "unknown"]
    storage_quality_sorted = [
        (q, storage_by_quality.get(q, 0)) for q in quality_order if storage_by_quality.get(q)
    ]
    codec_sorted = sorted(
        storage_by_codec.items(), key=lambda kv: (-kv[1], kv[0]),
    )
    year_sorted = sorted(year_buckets.items(), key=lambda kv: kv[0])
    year_max = max((c for _, c in year_sorted), default=0)
    # dict form of quality_buckets so the template can look up item counts
    # by quality without map(attribute=…) / selectattr gymnastics.
    quality_counts_by_q = {q: n for q, n in base["quality_buckets"]}
    video_items = metadata_quality["video_items"]
    if video_items:
        issue_total = (
            metadata_quality["missing_overview"]
            + metadata_quality["missing_year"]
            + metadata_quality["missing_credits"]
            + metadata_quality["missing_ratings"]
            + metadata_quality["missing_episode_metadata"]
            + metadata_quality["missing_playback_markers"]
        )
        metadata_quality["health_score"] = max(
            0,
            min(100, round(100 - (issue_total / (video_items * 6)) * 100)),
        )

    return {
        **base,
        "storage_by_quality": storage_quality_sorted,
        "storage_by_codec": codec_sorted,
        "year_distribution": year_sorted,
        "year_distribution_max": year_max,
        "quality_counts": quality_counts_by_q,
        "metadata_quality": metadata_quality,
        "top_series": [
            {"key": k, "title": series_titles.get(k, k), "count": n}
            for k, n in top_series
        ],
        "recent_additions": [
            {
                "message_id": it.message_id,
                "secure_hash": it.secure_hash,
                "title": it.title,
                "year": it.year,
                "file_size": it.file_size,
                "series_title": it.series_title,
                "season": it.season,
                "episode": it.episode,
                "quality": it.quality,
            }
            for it in recent[:10]
        ],
        "largest_items": [
            {
                "message_id": it.message_id,
                "secure_hash": it.secure_hash,
                "title": it.title,
                "year": it.year,
                "file_size": it.file_size,
                "quality": it.quality,
            }
            for it in largest[:10]
        ],
    }
