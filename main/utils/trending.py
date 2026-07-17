"""TMDB weekly trending — catalogue cross-reference.

Fetches /trending/movie/week and /trending/tv/week, then splits results:
  in_library  — tmdb_ids that exist in our catalogue  → user shelf
  missing     — tmdb_ids not in catalogue             → admin gap panel

One shared 24h in-process cache so TMDB is hit at most once per day
regardless of how many users/admin requests come in.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

import aiohttp

from main.utils import tmdb as _tmdb
from main.utils import media_index

_CACHE_TTL = 86_400       # 24 hours — trending is a weekly signal
_MAX_RESULTS = 20         # per media type
_MIN_SHELF_ITEMS = 3      # don't show the shelf if fewer matches

# In-process cache: {"ts": float, "in_library": [...], "missing": [...]}
_cache: Optional[Dict] = None
_cache_ts: float = 0.0


async def _fetch_endpoint(path: str) -> List[dict]:
    """Fetch a TMDB list endpoint and return its results."""
    if not _tmdb.is_configured():
        return []
    try:
        async with aiohttp.ClientSession() as session:
            data = await _tmdb._get(session, path)
        return (data or {}).get("results", [])[:_MAX_RESULTS]
    except Exception:
        logging.exception("trending: TMDB fetch failed for %s", path)
        return []


async def get_trending() -> Dict:
    """Return {"in_library": [HubItem/Group, ...], "missing": [{tmdb info}, ...]}.

    Fetches both /trending/{kind}/week AND /movie|tv/popular, deduplicates
    by tmdb_id, and uses whichever source has more catalogue coverage.
    """
    global _cache, _cache_ts

    if _cache is not None and (time.time() - _cache_ts) < _CACHE_TTL:
        return _cache

    # Fetch trending (weekly) + popular (stable) for both media types, all in parallel
    import asyncio
    t_movie, t_tv, p_movie, p_tv = await asyncio.gather(
        _fetch_endpoint("/trending/movie/week"),
        _fetch_endpoint("/trending/tv/week"),
        _fetch_endpoint("/movie/popular"),
        _fetch_endpoint("/tv/popular"),
    )

    # Merge and deduplicate by tmdb_id per kind, trending takes priority
    def _merge(trending: List[dict], popular: List[dict]) -> List[dict]:
        seen: set = set()
        out: List[dict] = []
        for r in trending + popular:
            tid = r.get("id")
            if tid and tid not in seen:
                seen.add(tid)
                out.append(r)
        return out[:_MAX_RESULTS]

    movie_results = _merge(t_movie, p_movie)
    tv_results    = _merge(t_tv,    p_tv)

    # Movie and TV TMDB IDs have separate namespaces. Respect ``tmdb_kind``
    # for standalone episodes that have not yet been grouped into a series.
    tmdb_in_catalogue: Dict[Tuple[int, str], object] = {}
    for it in media_index._items.values():
        if it.tmdb_id and not it.hidden:
            kind = "tv" if it.series_key or it.tmdb_kind == "tv" else "movie"
            key = (it.tmdb_id, kind)
            if key not in tmdb_in_catalogue:
                tmdb_in_catalogue[key] = media_index.card_for_tmdb_id(it.tmdb_id, kind)

    in_library: List = []
    missing: List[Dict] = []
    seen_cards: set = set()  # avoid duplicate series/movie entries

    def _process(results: List[dict], kind: str) -> None:
        for r in results:
            tid = r.get("id")
            if not tid:
                continue
            card = tmdb_in_catalogue.get((tid, kind))
            if card is not None:
                card_id = id(card)
                if card_id not in seen_cards:
                    seen_cards.add(card_id)
                    in_library.append(card)
            else:
                # Not in library — surface as a gap
                poster = r.get("poster_path", "")
                missing.append({
                    "tmdb_id":    tid,
                    "kind":       kind,
                    "title":      r.get("title") or r.get("name") or "",
                    "year":       (r.get("release_date") or r.get("first_air_date") or "")[:4],
                    "poster":     f"https://image.tmdb.org/t/p/w342{poster}" if poster else "",
                    "overview":   (r.get("overview") or "")[:200],
                    "popularity": r.get("popularity", 0),
                    "vote":       round(r.get("vote_average", 0), 1),
                    "tmdb_url":   f"https://www.themoviedb.org/{'movie' if kind == 'movie' else 'tv'}/{tid}",
                })

    _process(movie_results, "movie")
    _process(tv_results, "tv")

    # Sort missing by popularity (most-trending gap first)
    missing.sort(key=lambda x: -x["popularity"])

    _cache = {"in_library": in_library, "missing": missing[:30]}
    _cache_ts = time.time()
    return _cache


def invalidate() -> None:
    """Force a fresh fetch on the next request."""
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0.0
