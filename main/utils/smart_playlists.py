"""Smart playlists — rule-driven, auto-updating playlists.

A playlist becomes "smart" when it carries a ``rules`` document (see
rule_filter.py for the tree grammar) plus ``sort`` and ``limit``. Evaluation
runs against the in-process audio catalogue plus the user's play history
(watch_events) and ratings, then caches the materialized track list on the
playlist doc (``evaluated_tracks`` / ``evaluated_at``).

Refresh policy mirrors Navidrome's SmartPlaylistRefreshDelay: at most one
evaluation per playlist per _REFRESH_MIN_SECONDS, triggered only by client
access — never on a timer.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from main.utils import media_index, playlist_store, rule_filter, wh_store

_REFRESH_MIN_SECONDS = 5.0
# Per-user play-count / last-played aggregates, cached briefly so a page with
# several smart playlists shares one Mongo aggregation.
_PLAY_STATS_TTL = 60.0

_last_eval: dict[tuple[int, str], float] = {}

_play_stats_cache: dict[int, tuple[float, dict[str, dict]]] = {}

# Whitelisted rule fields. Extraction is cheap; play-count fields resolve
# from the per-user stats map passed via closure.
RULE_FIELDS = {
    "genre", "artist", "album", "title", "year",
    "playCount", "lastPlayedDays", "rating",
}


class RulesError(ValueError):
    """User-facing rule validation failure."""


def validate_rules(rules: Any) -> dict:
    try:
        return rule_filter.validate_rules(rules, RULE_FIELDS)
    except rule_filter.RuleValidationError as exc:
        raise RulesError(str(exc)) from exc


def validate_config(rules: Any, sort: Any, limit: Any) -> dict:
    """Validate the full smart-playlist config; returns the stored shape."""
    normalized_rules = validate_rules(rules)
    sort_key = str(sort or "recentlyAdded").strip()
    if sort_key not in _SORTS:
        raise RulesError(f"unknown sort: {sort_key!r}")
    try:
        limit_val = int(limit)
    except (TypeError, ValueError):
        limit_val = 50
    limit_val = max(1, min(limit_val, getattr(playlist_store, "_MAX_TRACKS", 500)))
    return {"rules": normalized_rules, "sort": sort_key, "limit": limit_val}


# ── per-user play statistics ─────────────────────────────────────────────────

async def _user_play_stats(user_id: int) -> dict[str, dict]:
    """{cw_key: {count, last_played}} from the user's watch events."""
    cached = _play_stats_cache.get(user_id)
    now = time.monotonic()
    if cached and now - cached[0] < _PLAY_STATS_TTL:
        return cached[1]
    db = wh_store._get_db()
    if db is None:
        return {}
    try:
        rows = await db[wh_store._EVENTS].aggregate([
            {"$match": {"user_id": user_id}},
            {"$group": {
                "_id": "$cw_key",
                "count": {"$sum": 1},
                "last": {"$max": "$watched_at"},
            }},
        ]).to_list(length=5000)
    except Exception:
        logging.exception("smart_playlists: play-stats aggregation failed uid=%d", user_id)
        return {}
    stats = {
        row["_id"]: {"count": int(row.get("count", 0)), "last": row.get("last")}
        for row in rows if row.get("_id")
    }
    _play_stats_cache[user_id] = (now, stats)
    return stats


# ── evaluation ────────────────────────────────────────────────────────────────

_SORTS = ("recentlyAdded", "playCount", "title", "random")


def _track_sort_key(sort: str, play_counts: dict[str, int]):
    if sort == "random":
        return None  # shuffle after selection
    if sort == "playCount":
        return lambda item: (
            play_counts.get(str(item.message_id), 0),
            int(item.duration or 0),
        )
    if sort == "title":
        return lambda item: (item.title or "").lower()
    # recentlyAdded: catalogue order is newest-first by message id.
    return lambda item: item.message_id


def _build_extractor(item, play_stats: dict[str, dict], ratings: dict[int, Any]) -> Callable[[str], Any]:

    def extract(field: str) -> Any:
        if field == "genre":
            # List of normalized genre tags; rule_filter matches a genre
            # leaf when ANY of them matches.
            genres = []
            for t in (item.tags or []):
                g = media_index.normalize_genre(t)
                if g and g.lower() not in [x.lower() for x in genres]:
                    genres.append(g)
            return genres
        if field == "artist":
            return (item.artist or "").lower()
        if field == "album":
            return (item.album_title or "").lower()
        if field == "title":
            return (item.title or "").lower()
        if field == "year":
            return item.year
        if field == "playCount":
            return play_stats.get(str(item.message_id), {}).get("count", 0)
        if field == "lastPlayedDays":
            last = play_stats.get(str(item.message_id), {}).get("last")
            return last if isinstance(last, datetime) else None
        if field == "rating":
            rating = ratings.get(item.message_id)
            if rating == "up":
                return 1
            if rating == "down":
                return -1
            return 0
        return None

    return extract


async def get_ratings(user_id: int) -> dict[int, Any]:
    """{message_id: 'up'|'down'} for the user, via ratings_store."""
    try:
        from main.utils import ratings_store
        rows = await ratings_store.get_user_ratings(user_id, limit=1000)
        return {int(r["message_id"]): r.get("rating") for r in rows if r.get("message_id")}
    except Exception:
        logging.exception("smart_playlists: ratings load failed uid=%d", user_id)
        return {}


async def evaluate(user_id: int, playlist: dict, *, force: bool = False) -> Optional[list[dict]]:
    """Materialize a smart playlist's tracks for ``user_id``.

    Returns the evaluated track-entry list (same shape as playlist_store
    tracks), or None when the playlist has no rules. Honors the per-playlist
    refresh delay unless ``force``; on the cached path returns stored
    ``evaluated_tracks``.
    """
    rules = playlist.get("rules")
    if not rules:
        return None
    playlist_id = playlist.get("playlist_id", "")

    key = (int(user_id), playlist_id)
    now = time.monotonic()
    last = _last_eval.get(key, 0.0)
    if not force and now - last < _REFRESH_MIN_SECONDS and playlist.get("evaluated_tracks") is not None:
        return playlist["evaluated_tracks"]
    _last_eval[key] = now

    limit = int(playlist.get("limit") or 50)
    sort = playlist.get("sort") or "recentlyAdded"

    play_stats = await _user_play_stats(user_id)
    ratings = await get_ratings(user_id)
    play_counts = {kid: int(v.get("count", 0)) for kid, v in play_stats.items()}

    candidates = []
    for item in media_index._items.values():
        if item.hidden or (item.media_kind or "") != "audio":
            continue
        extract = _build_extractor(item, play_stats, ratings)
        if rule_filter.matches(rules, extract):
            candidates.append(item)

    sort_key = _track_sort_key(sort, play_counts)
    if sort == "random":
        random.shuffle(candidates)
    else:
        candidates.sort(key=sort_key, reverse=(sort == "recentlyAdded" or sort == "playCount"))
    selected = candidates[:limit]

    evaluated = [
        {
            "message_id": item.message_id,
            "secure_hash": item.secure_hash,
            "title": (item.title or item.file_name or "Untitled")[:200],
            "artist": (item.artist or "")[:200],
        }
        for item in selected
    ]
    logging.info(
        "smart_playlists: evaluated playlist=%s uid=%d matched=%d limit=%d sort=%s",
        playlist_id, user_id, len(evaluated), limit, sort,
    )
    return evaluated


async def persist_evaluation(user_id: int, playlist_id: str, tracks: list[dict]) -> None:
    """Cache the evaluated track list on the playlist doc (best effort)."""
    try:
        db = playlist_store._get_db()
        if db is None:
            return
        await db["playlists"].update_one(
            {"user_id": user_id, "playlist_id": playlist_id},
            {"$set": {"evaluated_tracks": tracks, "evaluated_at": datetime.now(timezone.utc)}},
        )
    except Exception:
        logging.exception(
            "smart_playlists: persist failed uid=%d pid=%s", user_id, playlist_id,
        )


async def persist_config(user_id: int, playlist_id: str, *, rules: Any, sort: Any, limit: Any) -> None:
    """Validate and write the smart-playlist config on the playlist doc.

    ``rules`` may be falsy to clear the smart config (reverting the playlist
    to a static one). Raises RulesError on invalid rules/sort/limit.
    """
    if not rules:
        # Clearing: drop the config and any cached evaluation.
        db = playlist_store._get_db()
        if db is not None:
            await db["playlists"].update_one(
                {"user_id": user_id, "playlist_id": playlist_id},
                {"$unset": {"rules": "", "sort": "", "limit": "", "evaluated_tracks": "", "evaluated_at": ""}},
            )
        logging.info("smart_playlists: cleared config uid=%d pid=%s", user_id, playlist_id)
        return
    config = validate_config(rules, sort, limit)
    db = playlist_store._get_db()
    if db is None:
        raise RulesError("playlist storage unavailable")
    result = await db["playlists"].update_one(
        {"user_id": user_id, "playlist_id": playlist_id},
        {"$set": {
            **config,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    if result.matched_count == 0:
        raise RulesError("playlist not found")
    logging.info(
        "smart_playlists: config saved uid=%d pid=%s sort=%s limit=%s",
        user_id, playlist_id, config["sort"], config["limit"],
    )
