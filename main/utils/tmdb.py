"""TMDB enrichment client.

Async wrapper around themoviedb.org's REST API. Two entry points,
``lookup_movie`` and ``lookup_series``, return a normalised ``TMDBHit``
or ``None`` if no high-confidence match was found.

Confidence rule: title similarity (difflib ratio) must clear 0.7 AND
the year (if both sides have one) must match within ±1 — TMDB and
release filenames sometimes disagree by one year on late-December
releases. If we have a year and TMDB returns a candidate with no
release date at all, we reject it.

A small in-process cache keyed by (kind, normalised_title, year) keeps
the request count down — every episode of a series ends up looking up
the same show, so cache hits dominate after the first.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import aiohttp

from main.vars import Var


API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"

_TITLE_MATCH_THRESHOLD = 0.7
_YEAR_TOLERANCE = 1  # TMDB ↔ release-year off-by-one is common
_CACHE_TTL = 60 * 60 * 12  # 12h
_REQUEST_TIMEOUT = 10


@dataclass
class TMDBHit:
    """Normalised match result. ``kind`` is "movie" or "tv"."""
    tmdb_id: int
    kind: str
    title: str
    year: Optional[int]
    overview: str
    poster_path: str        # relative path, prepend IMAGE_BASE + "/wXXX"
    backdrop_path: str
    genres: List[str]
    imdb_id: str


# Tuple keys: (kind, lowercased normalized title, year-or-0).
_cache: dict = {}
_locks: dict = {}


def _now() -> float:
    return time.monotonic()


def _normalise(title: str) -> str:
    """Strip year, separators, and release noise from a title for matching."""
    if not title:
        return ""
    t = re.sub(r"\([^)]*\)", " ", title)         # parenthesised asides
    t = re.sub(r"\[[^\]]*\]", " ", t)            # bracketed tags
    t = re.sub(r"\b(19|20)\d{2}\b", " ", t)      # year
    t = re.sub(r"[._\-]+", " ", t)
    t = re.sub(r"[^a-zA-Z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, _normalise(a), _normalise(b)).ratio()


def _parse_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    m = re.match(r"(\d{4})", date_str)
    return int(m.group(1)) if m else None


def is_configured() -> bool:
    """True if TMDB_API_KEY is set and enrichment can run."""
    return bool(Var.TMDB_API_KEY)


async def _get(session: aiohttp.ClientSession, path: str, **params) -> Optional[dict]:
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    params["api_key"] = Var.TMDB_API_KEY
    try:
        async with session.get(f"{API_BASE}{path}", params=params,
                               timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as resp:
            if resp.status == 401:
                logging.warning("tmdb: 401 unauthorised — check TMDB_API_KEY")
                return None
            if resp.status == 429:
                logging.warning("tmdb: 429 rate limited")
                return None
            if resp.status >= 400:
                logging.debug("tmdb: %s %s → %d", path, params, resp.status)
                return None
            return await resp.json()
    except asyncio.TimeoutError:
        logging.warning("tmdb: timeout on %s", path)
        return None
    except Exception:
        logging.exception("tmdb: request failed on %s", path)
        return None


def _best_match(results: List[dict], title: str, year: Optional[int],
                year_field: str) -> Optional[dict]:
    """Pick the top candidate that clears the confidence bar."""
    best: Tuple[float, Optional[dict]] = (0.0, None)
    for r in results:
        candidate_title = r.get("title") or r.get("name") or ""
        cy = _parse_year(r.get(year_field))
        if year is not None and cy is None:
            continue
        if year is not None and cy is not None and abs(cy - year) > _YEAR_TOLERANCE:
            continue
        score = _similarity(candidate_title, title)
        # Boost when the year is an exact match — disambiguates two
        # similarly-titled films from different decades.
        if year is not None and cy == year:
            score += 0.1
        if score >= _TITLE_MATCH_THRESHOLD and score > best[0]:
            best = (score, r)
    return best[1]


async def _enrich_details(session: aiohttp.ClientSession,
                          kind: str, tmdb_id: int) -> Optional[dict]:
    return await _get(
        session, f"/{kind}/{tmdb_id}",
        append_to_response="external_ids",
    )


async def _lookup(kind: str, title: str, year: Optional[int]) -> Optional[TMDBHit]:
    if not is_configured():
        return None
    if not title:
        return None

    cache_key = (kind, _normalise(title), year or 0)
    cached = _cache.get(cache_key)
    if cached and (_now() - cached[0]) < _CACHE_TTL:
        return cached[1]

    lock = _locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = _cache.get(cache_key)
        if cached and (_now() - cached[0]) < _CACHE_TTL:
            return cached[1]

        async with aiohttp.ClientSession() as session:
            search_path = "/search/movie" if kind == "movie" else "/search/tv"
            year_param_name = "year" if kind == "movie" else "first_air_date_year"
            year_field = "release_date" if kind == "movie" else "first_air_date"

            payload = await _get(
                session, search_path,
                query=title,
                **{year_param_name: year},
            )
            results = (payload or {}).get("results") or []
            match = _best_match(results, title, year, year_field)
            if match is None:
                _cache[cache_key] = (_now(), None)
                return None

            details = await _enrich_details(session, kind, int(match["id"]))
            if details is None:
                details = match

            hit = TMDBHit(
                tmdb_id=int(match["id"]),
                kind=kind,
                title=details.get("title") or details.get("name") or "",
                year=_parse_year(details.get(year_field)),
                overview=(details.get("overview") or "").strip(),
                poster_path=details.get("poster_path") or "",
                backdrop_path=details.get("backdrop_path") or "",
                genres=[g["name"] for g in (details.get("genres") or []) if g.get("name")],
                imdb_id=(details.get("external_ids") or {}).get("imdb_id") or "",
            )
            _cache[cache_key] = (_now(), hit)
            return hit


async def lookup_movie(title: str, year: Optional[int] = None) -> Optional[TMDBHit]:
    return await _lookup("movie", title, year)


async def lookup_series(title: str, year: Optional[int] = None) -> Optional[TMDBHit]:
    return await _lookup("tv", title, year)
