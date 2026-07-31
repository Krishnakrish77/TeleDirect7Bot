"""TMDB-based recommendation engine.

Algorithm:
1. Collect seed (tmdb_id, kind) tuples plus genre and keyword weights from watch
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
    rec_feedback_store,
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
TmdbKey = Tuple[int, str]


def _tmdb_for_item(item) -> Tuple[Optional[int], str]:
    if item is None or not item.tmdb_id:
        return None, ""
    kind = "tv" if item.series_key or getattr(item, "tmdb_kind", "") == "tv" else "movie"
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


def _card_kind(card) -> str:
    _tmdb_id, kind = _card_tmdb(card)
    return kind or ("tv" if getattr(card, "series_key", "") else "movie")


def _card_key(card) -> tuple:
    tmdb_id, kind = _card_tmdb(card)
    if tmdb_id:
        return ("tmdb", int(tmdb_id), kind)
    return (
        "series", getattr(card, "series_key", ""),
    ) if getattr(card, "series_key", "") else (
        "movie", getattr(card, "movie_key", ""),
    ) if getattr(card, "movie_key", "") else ("item", _card_message_id(card))


def _director_names(value: object) -> list[str]:
    return [name.strip().casefold() for name in re.split(r"[,;/&]", str(value or "")) if name.strip()]


def _card_personal_score(card, profile: dict, *, related_count: int = 0, max_message_id: int = 1) -> float:
    """Score an eligible catalogue card from a user's local derived profile."""
    item = _card_item(card)
    genres = _genres_for_card(card)
    keywords = [str(keyword).casefold() for keyword in (getattr(item, "tmdb_keywords", None) or [])]
    directors = _director_names(getattr(item, "director", ""))
    seed_genres: Counter = profile.get("seed_genres") or Counter()
    seed_keywords: Counter = profile.get("seed_keywords") or Counter()
    seed_directors: Counter = profile.get("seed_directors") or Counter()
    negative_genres: Counter = profile.get("negative_genres") or Counter()
    score = float(related_count) * 7.5
    score += sum(seed_genres.get(genre, 0) * 1.4 for genre in genres)
    score += sum(seed_keywords.get(keyword, 0) * 0.18 for keyword in keywords)
    score += sum(seed_directors.get(director, 0) * 2.6 for director in directors)
    score -= sum(negative_genres.get(genre, 0) * 1.8 for genre in genres)
    score += (_card_message_id(card) / max(1, max_message_id)) * 2.0
    if _card_kind(card) == "tv":
        score += 0.4
    if getattr(item, "overview", ""):
        score += 0.35
    return score


def rank_catalogue_cards(
    cards: list,
    profile: dict,
    *,
    related_counts: Counter | None = None,
    query: str = "",
    limit: int = _MAX_RECS,
) -> list[tuple[object, float]]:
    """Score and diversify local cards for both normal and agentic retrieval.

    A direct search keeps catalogue relevance primary.  Browse calls use the
    derived local profile as their primary ordering.  The two caps diversify
    the first pass, then deliberately relax so small libraries still fill.
    """
    related_counts = related_counts or Counter()
    max_message_id = max((it.message_id for it in media_index._items.values()), default=1)
    scored: list[tuple[object, float, float]] = []
    seen: set[tuple] = set()
    for card in cards:
        key = _card_key(card)
        if key in seen:
            continue
        seen.add(key)
        tmdb_id, kind = _card_tmdb(card)
        related = related_counts.get((tmdb_id, kind), 0) if tmdb_id else 0
        relevance = media_index.card_search_score(card, query) if query else 0.0
        scored.append((card, _card_personal_score(card, profile, related_count=related, max_message_id=max_message_id), relevance))
    scored.sort(key=lambda entry: (-entry[2], -entry[1], -_card_message_id(entry[0])))

    selected: list[tuple[object, float]] = []
    selected_keys: set[tuple] = set()
    genre_counts: Counter = Counter()
    kind_counts: Counter = Counter()
    for genre_cap, kind_cap in ((2, 8), (3, 10), (10_000, 10_000)):
        for card, score, _relevance in scored:
            if len(selected) >= limit or _card_key(card) in selected_keys:
                continue
            genres = _genres_for_card(card)[:2]
            kind = _card_kind(card)
            if any(genre_counts[genre] >= genre_cap for genre in genres) or kind_counts[kind] >= kind_cap:
                continue
            selected.append((card, score))
            selected_keys.add(_card_key(card))
            kind_counts[kind] += 1
            for genre in genres:
                genre_counts[genre] += 1
        if len(selected) >= limit:
            break
    return selected


def _item_for_cw_key(cw_key: str):
    match = _CW_KEY_RE.match(cw_key or "")
    if not match:
        return None
    return media_index.get_item(int(match.group(2)))


def _item_for_tmdb_key(tmdb_id: int, kind: str):
    card = media_index.card_for_tmdb_id(tmdb_id, kind)
    return _card_item(card) if card is not None else None


def _genre_link(genre: str) -> str:
    from urllib.parse import urlencode
    return "/?" + urlencode({"genre": genre})


async def _collect_signal_profile(user_id: int) -> dict:
    """Collect lightweight local intent signals for ranking and shelves."""
    history, watchlist_ids, ratings, continue_map, feedback = await asyncio.gather(
        wh_store.get_recent(user_id, limit=80),
        watchlist_store.get_ids(user_id),
        ratings_store.get_user_ratings(user_id, limit=200),
        cw_store.get_all(user_id),
        rec_feedback_store.get_recent_opens(user_id, limit=80),
    )

    seeds: List[Tuple[int, str]] = []
    seed_weights: Counter = Counter()
    seen_seed_tmdb: set[TmdbKey] = set()
    seed_genres: Counter = Counter()
    seed_keywords: Counter = Counter()
    seed_directors: Counter = Counter()
    negative_genres: Counter = Counter()
    exclude_tmdb: set[TmdbKey] = set()
    liked_tmdb: set[TmdbKey] = set()
    disliked_tmdb: set[TmdbKey] = set()
    partial_tmdb: set[TmdbKey] = set()
    def add_seed(
        item,
        weight: float,
        *,
        exclude: bool = True,
    ) -> None:
        tid, kind = _tmdb_for_item(item)
        if not tid:
            return
        if exclude:
            exclude_tmdb.add((tid, kind))
        if (tid, kind) not in seen_seed_tmdb:
            seen_seed_tmdb.add((tid, kind))
            seeds.append((tid, kind))
        seed_weights[(tid, kind)] += weight
        for genre in getattr(item, "tmdb_genres", None) or []:
            seed_genres[genre] += weight
        for keyword in getattr(item, "tmdb_keywords", None) or []:
            seed_keywords[keyword.lower()] += weight
        for director in _director_names(getattr(item, "director", "")):
            seed_directors[director] += weight

    for index, entry in enumerate(history):
        item = _item_for_cw_key(entry.get("cw_key", ""))
        play_count = min(5, int(entry.get("play_count") or 1))
        recency = max(0.25, 1.0 - (index * 0.035))
        add_seed(item, (2.2 + play_count * 0.35) * recency)

    for key, entry in list(continue_map.items())[:40]:
        item = _item_for_cw_key(key)
        try:
            pct = float(entry.get("pos") or 0) / float(entry.get("dur") or 0)
        except (TypeError, ValueError, ZeroDivisionError):
            pct = 0
        if pct <= 0.02 or pct >= 0.95:
            continue
        tid, kind = _tmdb_for_item(item)
        if tid:
            partial_tmdb.add((tid, kind))
        add_seed(item, 1.0 + min(0.95, pct) * 1.8)

    for iid in watchlist_ids[:80]:
        tid, kind = _tmdb_for_wl_id(iid)
        if not tid:
            continue
        item = media_index.card_for_tmdb_id(tid, kind)
        add_seed(_card_item(item), 1.35)

    for entry in ratings:
        item = media_index.get_item(int(entry.get("message_id") or 0))
        tid, kind = _tmdb_for_item(item)
        if not tid:
            continue
        exclude_tmdb.add((tid, kind))
        if entry.get("rating") == "up":
            liked_tmdb.add((tid, kind))
            add_seed(item, 4.0)
        elif entry.get("rating") == "down":
            disliked_tmdb.add((tid, kind))
            for genre in getattr(item, "tmdb_genres", None) or []:
                negative_genres[genre] += 3.0

    # Recommendation engagements are a deliberately gentle, short-lived
    # signal. A card open/play says "this was interesting" but must never
    # outweigh an explicit rating or completed watch.
    feedback_weights = {"open": 0.35, "play": 0.8, "save": 0.2}
    for index, entry in enumerate(feedback):
        try:
            tmdb_id = int(entry.get("tmdb_id") or 0)
        except (TypeError, ValueError):
            tmdb_id = 0
        kind = str(entry.get("tmdb_kind") or "")
        item = _item_for_tmdb_key(tmdb_id, kind) if tmdb_id and kind else None
        if item is None:
            continue
        weight = feedback_weights.get(str(entry.get("action") or ""), 0)
        if weight:
            add_seed(item, weight * max(0.2, 1.0 - index * 0.05), exclude=False)

    return {
        # TMDB calls are deliberately capped.  Select the strongest distinct
        # signals rather than whichever source happened to be read first: a
        # large watch history must not crowd out an explicit like.
        "seeds": sorted(seeds, key=lambda key: seed_weights[key], reverse=True)[:_MAX_SEEDS],
        "seed_genres": seed_genres,
        "seed_keywords": seed_keywords,
        "seed_directors": seed_directors,
        "negative_genres": negative_genres,
        "exclude_tmdb": exclude_tmdb,
        "liked_tmdb": liked_tmdb,
        "disliked_tmdb": disliked_tmdb,
        "partial_tmdb": partial_tmdb,
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

    counter: Counter[TmdbKey] = Counter()
    for rec_list in results:
        if isinstance(rec_list, Exception):
            logging.warning("rec_engine: TMDB recommendation call failed: %s", rec_list)
            continue
        for rec_id, rec_kind in rec_list:
            key = (rec_id, rec_kind)
            if key not in exclude:
                counter[key] += 1

    return [(tid, kind, count) for (tid, kind), count in counter.most_common(80)]


def _rank_candidate_cards(candidates: List[Tuple[int, str, int]], profile: dict) -> list:
    catalogue_tmdb_ids = {
        _tmdb_for_item(it) for it in media_index._items.values()
        if it.tmdb_id and not it.hidden
    }
    cards: list = []
    related_counts: Counter = Counter()
    for tid, kind, tmdb_count in candidates:
        if (tid, kind) not in catalogue_tmdb_ids:
            continue
        card = media_index.card_for_tmdb_id(tid, kind)
        if card is None:
            continue
        cards.append(card)
        related_counts[(tid, kind)] = tmdb_count
    return [card for card, _score in rank_catalogue_cards(cards, profile, related_counts=related_counts)]


async def get_recommendations(
    user_id: int,
    *,
    profile: Optional[dict] = None,
    dismissed: Optional[set[TmdbKey]] = None,
) -> Optional[List]:
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

    if profile is None or dismissed is None:
        collected_profile, collected_dismissed = await asyncio.gather(
            _collect_signal_profile(user_id),
            dismissed_store.get_dismissed_ids(user_id),
        )
        profile = profile if profile is not None else collected_profile
        dismissed = dismissed if dismissed is not None else collected_dismissed
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


async def get_personal_shelves(
    user_id: int,
    limit: int = 18,
    *,
    profile: Optional[dict] = None,
    dismissed: Optional[set[TmdbKey]] = None,
) -> list[dict]:
    if profile is None or dismissed is None:
        collected_profile, collected_dismissed = await asyncio.gather(
            _collect_signal_profile(user_id),
            dismissed_store.get_dismissed_ids(user_id),
        )
        profile = profile if profile is not None else collected_profile
        dismissed = dismissed if dismissed is not None else collected_dismissed
    seed_genres: Counter = profile.get("seed_genres") or Counter()
    exclude_tmdb = set(profile.get("exclude_tmdb") or set()) | dismissed
    shelves: list[dict] = []
    used_names: set[str] = set()
    for genre, _weight in seed_genres.most_common(2):
        cards, _total = media_index.query_grouped(
            genre=genre,
            sort="newest",
            limit=limit * 2,
        )
        filtered = []
        seen_groups: set[str] = set()
        for card in cards:
            tid, kind = _card_tmdb(card)
            if tid and (tid, kind) in exclude_tmdb:
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


async def get_recommendation_reasons(
    user_id: int,
    cards: List,
    *,
    profile: Optional[dict] = None,
) -> List[str]:
    """Explain recommendation cards using the user's local seed genres.

    This intentionally avoids additional TMDB calls: the recommendations have
    already been fetched, and both seed cards + result cards should have enough
    catalogue metadata to produce useful lightweight labels.
    """
    if not cards:
        return []
    if profile is None:
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
