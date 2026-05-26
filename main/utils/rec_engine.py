"""TMDB-based recommendation engine.

Algorithm:
1. Collect seed (tmdb_id, kind) tuples from the user's watch history +
   watchlist (items that have been TMDB-enriched).
2. Call /movie|tv/{id}/recommendations for each seed (max 5 calls).
3. Score candidate tmdb_ids by how many seeds recommended them.
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

from main.utils import tmdb, media_index, wh_store, watchlist_store, rec_store

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


async def _collect_seeds(user_id: int) -> List[Tuple[int, str]]:
    """Return up to _MAX_SEEDS (tmdb_id, kind) from watch history + watchlist."""
    seeds: List[Tuple[int, str]] = []
    seen: set = set()

    # Watch history first — strongest signal (completed views)
    history = await wh_store.get_recent(user_id, limit=10)
    for entry in history:
        m = _CW_KEY_RE.match(entry.get("cw_key", ""))
        if not m:
            continue
        item = media_index.get_item(int(m.group(2)))
        tid, kind = _tmdb_for_item(item)
        if tid and tid not in seen:
            seen.add(tid)
            seeds.append((tid, kind))
            if len(seeds) >= _MAX_SEEDS:
                return seeds

    # Watchlist supplements when history is sparse
    for iid in await watchlist_store.get_ids(user_id):
        tid, kind = _tmdb_for_wl_id(iid)
        if tid and tid not in seen:
            seen.add(tid)
            seeds.append((tid, kind))
            if len(seeds) >= _MAX_SEEDS:
                break

    return seeds


async def _fetch_recs_for_seeds(
    seeds: List[Tuple[int, str]],
    exclude: set,
) -> List[Tuple[int, str]]:
    """Call TMDB recommendations for each seed, return scored candidates."""
    calls = [tmdb.fetch_recommendations(tid, kind) for tid, kind in seeds[:_MAX_CALLS]]
    results = await asyncio.gather(*calls, return_exceptions=True)

    counter: Counter = Counter()
    kind_map: dict = {}
    for rec_list in results:
        if isinstance(rec_list, Exception):
            continue
        for rec_id, rec_kind in rec_list:
            if rec_id not in exclude:
                counter[rec_id] += 1
                kind_map[rec_id] = rec_kind

    return [(tid, kind_map[tid]) for tid, _ in counter.most_common(50)]


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

    seeds = await _collect_seeds(user_id)
    if not seeds:
        return None

    exclude = {tid for tid, _ in seeds}
    candidates = await _fetch_recs_for_seeds(seeds, exclude)

    cards = []
    to_cache = []
    for tid, kind in candidates:
        card = media_index.card_for_tmdb_id(tid, kind)
        if card is not None:
            cards.append(card)
            to_cache.append((tid, kind))
            if len(cards) >= _MAX_RECS:
                break

    if cards:
        await rec_store.set_cached(user_id, to_cache)
        return cards

    return None
