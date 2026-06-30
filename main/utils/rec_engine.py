"""TMDB-based recommendation engine.

Algorithm:
1. Collect seed (tmdb_id, kind) tuples and genre weights from watch
   history, continue-watching progress, watchlist entries, and ratings.
2. Call /movie|tv/{id}/recommendations for each seed (max 5 calls).
3. Score candidate tmdb_ids by TMDB frequency, liked/down-rated genres,
   catalogue freshness, and lightweight diversity penalties.
4. Cross-reference against the local catalogue via card_for_tmdb_id().
5. Return the top 12 matching cards.

Results are cached in MongoDB for 24 h. On a cache miss the TMDB calls
run in-request; subsequent page loads get the cached result instantly.

Falls back to None (no shelf shown) when TMDB is not configured, the user
has no enriched history, or no candidates overlap with the catalogue.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from typing import List, Optional, Tuple

from main.utils import (
    cw_store,
    dismissed_store,
    media_index,
    ratings_store,
    rec_store,
    tmdb,
    watchlist_store,
    wh_store,
)

_CW_KEY_RE = re.compile(r'^([A-Za-z0-9_-]*[A-Za-z_-])(\d+)$')
_MAX_SEEDS = 8   # max seed items to collect
_MAX_CALLS = 5   # max TMDB recommendation calls per request
_MAX_RECS = 12   # max items in shelf


def _tmdb_for_item(item) -> Tuple[Optional[int], str]:
    if item is None or not item.tmdb_id:
        return None, ""
    kind = "tv" if item.series_key else "movie"
    return item.tmdb_id, kind


def _tmdb_for_wl_id(item_id: str) -> Tuple[Optional[int], str]:
    """Resolve a watchlist item_id to (tmdb_id, kind)."""
    if item_id.startswith("movie:"):
        variants = media_index.variants_for_movie(item_id[6:])
        if variants:
            return _tmdb_for_item(variants[0])
    elif item_id.startswith("series:"):
        eps = media_index.episodes_for_series(item_id[7:])
        if eps:
            return _tmdb_for_item(eps[0])
    elif item_id.isdigit():
        return _tmdb_for_item(media_index.get_item(int(item_id)))
    return None, ""


def _genres_for_card(card) -> list[str]:
    item = getattr(card, "poster_item", card)
    return list(getattr(item, "tmdb_genres", None) or [])


def _card_item(card):
    return getattr(card, "poster_item", card)


def _card_message_id(card) -> int:
    item = _card_item(card)
    return int(getattr(item, "message_id", 0) or 0)


def _card_tmdb(card) -> Tuple[Optional[int], str]:
    return _tmdb_for_item(_card_item(card))


def _item_for_cw_key(cw_key: str):
    match = _CW_KEY_RE.match(cw_key or "")
    if not match:
        return None
    return media_index.get_item(int(match.group(2)))


def _genre_link(genre: str) -> str:
    from urllib.parse import urlencode
    return "/?" + urlencode({"genre": genre})


async def _collect_signal_profile(user_id: int) -> dict:
    """Collect lightweight local intent signals for ranking and shelves."""
    history, watchlist_ids, ratings, continue_map = await asyncio.gather(
        wh_store.get_recent(user_id, limit=80),
        watchlist_store.get_ids(user_id),
        ratings_store.get_user_ratings(user_id, limit=200),
        cw_store.get_all(user_id),
    )

    seeds: List[Tuple[int, str]] = []
    seen_seed_tmdb: set[int] = set()
    seed_genres: Counter = Counter()
    negative_genres: Counter = Counter()
    exclude_tmdb: set[int] = set()
    liked_tmdb: set[int] = set()
    partial_tmdb: set[int] = set()
    # Only completed watches may feed "Because you watched ..." copy.
    recent_titles: list[str] = []

    def add_seed(
        item,
        weight: float,
        *,
        exclude: bool = True,
        include_recent_title: bool = False,
    ) -> None:
        tid, kind = _tmdb_for_item(item)
        if not tid:
            return
        if exclude:
            exclude_tmdb.add(tid)
        if tid not in seen_seed_tmdb:
            seen_seed_tmdb.add(tid)
            seeds.append((tid, kind))
        for genre in getattr(item, "tmdb_genres", None) or []:
            seed_genres[genre] += weight
        if include_recent_title:
            title = getattr(item, "series_title", "") or getattr(item, "title", "")
            if title and len(recent_titles) < 4:
                recent_titles.append(title)

    for index, entry in enumerate(history):
        item = _item_for_cw_key(entry.get("cw_key", ""))
        play_count = min(5, int(entry.get("play_count") or 1))
        recency = max(0.25, 1.0 - (index * 0.035))
        add_seed(item, (2.2 + play_count * 0.35) * recency, include_recent_title=True)

    for key, entry in list(continue_map.items())[:40]:
        item = _item_for_cw_key(key)
        try:
            pct = float(entry.get("pos") or 0) / float(entry.get("dur") or 0)
        except (TypeError, ValueError, ZeroDivisionError):
            pct = 0
        if pct <= 0.02 or pct >= 0.95:
            continue
        tid, _kind = _tmdb_for_item(item)
        if tid:
            partial_tmdb.add(tid)
        add_seed(item, 1.0 + min(0.95, pct) * 1.8)

    for iid in watchlist_ids[:80]:
        tid, kind = _tmdb_for_wl_id(iid)
        if not tid:
            continue
        item = media_index.card_for_tmdb_id(tid, kind)
        add_seed(_card_item(item), 1.35)

    for entry in ratings:
        item = media_index.get_item(int(entry.get("message_id") or 0))
        tid, _kind = _tmdb_for_item(item)
        if not tid:
            continue
        exclude_tmdb.add(tid)
        if entry.get("rating") == "up":
            liked_tmdb.add(tid)
            add_seed(item, 4.0)
        elif entry.get("rating") == "down":
            for genre in getattr(item, "tmdb_genres", None) or []:
                negative_genres[genre] += 3.0

    return {
        "seeds": seeds[:_MAX_SEEDS],
        "seed_genres": seed_genres,
        "negative_genres": negative_genres,
        "exclude_tmdb": exclude_tmdb,
        "liked_tmdb": liked_tmdb,
        "partial_tmdb": partial_tmdb,
        "recent_titles": recent_titles,
    }


async def _fetch_recs_for_seeds(
    seeds: List[Tuple[int, str]],
    exclude: set,
) -> List[Tuple[int, str, int]]:
    """Call TMDB recommendations for each seed, return scored candidates."""
    import aiohttp as _aiohttp
    async with _aiohttp.ClientSession() as session:
        calls = [tmdb.fetch_recommendations(tid, kind, session=session)
                 for tid, kind in seeds[:_MAX_CALLS]]
        results = await asyncio.gather(*calls, return_exceptions=True)

    counter: Counter = Counter()
    kind_map: dict = {}
    for rec_list in results:
        if isinstance(rec_list, Exception):
            logging.warning("rec_engine: TMDB recommendation call failed: %s", rec_list)
            continue
        for rec_id, rec_kind in rec_list:
            if rec_id not in exclude:
                counter[rec_id] += 1
                kind_map[rec_id] = rec_kind

    return [(tid, kind_map[tid], count) for tid, count in counter.most_common(80)]


def _rank_candidate_cards(candidates: List[Tuple[int, str, int]], profile: dict) -> list:
    catalogue_tmdb_ids = {
        it.tmdb_id for it in media_index._items.values()
        if it.tmdb_id and not it.hidden
    }
    max_message_id = max((it.message_id for it in media_index._items.values()), default=1)
    seed_genres: Counter = profile.get("seed_genres") or Counter()
    negative_genres: Counter = profile.get("negative_genres") or Counter()
    scored: list[tuple[float, int, str, object, list[str]]] = []

    for tid, kind, tmdb_count in candidates:
        if tid not in catalogue_tmdb_ids:
            continue
        card = media_index.card_for_tmdb_id(tid, kind)
        if card is None:
            continue
        genres = _genres_for_card(card)
        score = float(tmdb_count) * 7.5
        score += sum(seed_genres.get(genre, 0) * 1.4 for genre in genres)
        score -= sum(negative_genres.get(genre, 0) * 1.8 for genre in genres)
        score += (_card_message_id(card) / max_message_id) * 2.0
        if kind == "tv":
            score += 0.4
        if getattr(_card_item(card), "overview", ""):
            score += 0.35
        scored.append((score, tid, kind, card, genres))

    selected: list = []
    genre_counts: Counter = Counter()
    kind_counts: Counter = Counter()
    pool = scored[:]
    while pool and len(selected) < _MAX_RECS:
        best_index = 0
        best_score = float("-inf")
        for index, (score, _tid, kind, _card, genres) in enumerate(pool):
            primary = genres[0] if genres else ""
            adjusted = score
            if primary:
                adjusted -= genre_counts[primary] * 2.2
            adjusted -= max(0, kind_counts[kind] - 5) * 1.25
            if adjusted > best_score:
                best_score = adjusted
                best_index = index
        _score, _tid, kind, card, genres = pool.pop(best_index)
        selected.append(card)
        kind_counts[kind] += 1
        for genre in genres[:2]:
            genre_counts[genre] += 1
    return selected


async def get_recommendations(user_id: int) -> Optional[List]:
    """Return up to 12 catalogue cards, or None if nothing available."""
    if not tmdb.is_configured():
        return None

    # Serve from cache if available (24 h TTL)
    cached = await rec_store.get_cached(user_id)
    if cached is not None:
        cards = [media_index.card_for_tmdb_id(tid, kind) for tid, kind in cached]
        cards = [c for c in cards if c is not None]
        if cards:
            return cards
        # All cached items were pruned from the catalogue — invalidate so the
        # next path regenerates rather than paying this dead-cache miss every load.
        await rec_store.clear_cached(user_id)

    profile, dismissed = await asyncio.gather(
        _collect_signal_profile(user_id),
        dismissed_store.get_dismissed_ids(user_id),
    )
    seeds = profile["seeds"]
    if not seeds:
        return None

    exclude = set(profile.get("exclude_tmdb") or set()) | dismissed
    candidates = await _fetch_recs_for_seeds(seeds, exclude)
    cards = _rank_candidate_cards(candidates, profile)
    to_cache = [_card_tmdb(card) for card in cards]
    to_cache = [(tid, kind) for tid, kind in to_cache if tid]

    if cards:
        await rec_store.set_cached(user_id, to_cache)
        return cards

    return None


async def get_personal_shelves(user_id: int, limit: int = 18) -> list[dict]:
    profile, dismissed = await asyncio.gather(
        _collect_signal_profile(user_id),
        dismissed_store.get_dismissed_ids(user_id),
    )
    seed_genres: Counter = profile.get("seed_genres") or Counter()
    exclude_tmdb = set(profile.get("exclude_tmdb") or set()) | dismissed
    shelves: list[dict] = []
    used_names: set[str] = set()
    recent_label = profile.get("recent_titles", [""])[0] if profile.get("recent_titles") else ""

    for genre, _weight in seed_genres.most_common(2):
        cards, _total = media_index.query_grouped(
            genre=genre,
            sort="newest",
            limit=limit * 2,
        )
        filtered = []
        seen_groups: set[str] = set()
        for card in cards:
            tid, _kind = _card_tmdb(card)
            if tid and tid in exclude_tmdb:
                continue
            group_key = getattr(card, "series_key", "") or getattr(card, "movie_key", "") or str(_card_message_id(card))
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            filtered.append(card)
            if len(filtered) >= limit:
                break
        if len(filtered) < 3:
            continue
        name = f"Because you like {genre}"
        if recent_label and not shelves:
            clean_title = re.sub(r"\s+", " ", recent_label).strip()
            if clean_title:
                name = f"Because you watched {clean_title[:42]}"
        if name in used_names:
            continue
        used_names.add(name)
        shelves.append({
            "name": name,
            "items": filtered,
            "link": _genre_link(genre),
            "total": len(filtered),
        })

    return shelves


async def get_recommendation_reasons(user_id: int, cards: List) -> List[str]:
    """Explain recommendation cards using the user's local seed genres.

    This intentionally avoids additional TMDB calls: the recommendations have
    already been fetched, and both seed cards + result cards should have enough
    catalogue metadata to produce useful lightweight labels.
    """
    if not cards:
        return []
    try:
        profile = await _collect_signal_profile(user_id)
    except Exception:
        logging.exception("rec_engine: reason seed collection failed")
        profile = {}

    seed_genres: Counter = profile.get("seed_genres") or Counter()

    reasons: List[str] = []
    for card in cards:
        card_genres = _genres_for_card(card)
        matched = sorted(
            (genre for genre in card_genres if seed_genres.get(genre)),
            key=lambda genre: (-seed_genres[genre], genre),
        )
        if matched:
            reasons.append(f"Because you like {matched[0]}")
        elif card_genres:
            reasons.append(f"Because it matches {card_genres[0]}")
        else:
            reasons.append("Based on your watch history")
    return reasons
