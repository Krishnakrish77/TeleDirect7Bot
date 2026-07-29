"""Per-user AI recommendation agent (Gemini, catalogue-grounded).

Pipeline (RAG re-ranking — see the feature plan for the research rationale):
  1. Build a taste profile from the user's real signals + stats aggregation.
  2. Retrieve a bounded, diverse candidate pool from the catalogue.
  3. Ask Gemini to RANK/LABEL candidates (never invent) into a balanced
     comfort/discovery mix, each with a one-line personal reason.
  4. Ground the response against the candidate set (drop hallucinated ids),
     map to SPA cards, cache per user.

Everything degrades gracefully: no key / cold start / Gemini failure all fall
back to trending or the raw candidate pool.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections import Counter
from typing import Awaitable, Callable, Optional

from main.utils import (
    ai_rec_store, cw_store, dismissed_store, gemini, media_index, rec_engine, rec_store, request_store, tmdb, wh_store,
)

_MAX_CANDIDATES = 50
_AGENT_MAX_TOOL_CALLS = 3
_AGENT_TOOL_RESULT_LIMIT = 12
_AGENT_BUDGET_SECONDS = 25.0
_QUERY_CANDIDATE_RESERVE = 24
_QUERY_TERM_LIMIT = 5
_MIX_SIZE = 20
_MIX_CANDIDATE_LIMIT = 60
_EXTERNAL_PICK_TTL = 15 * 60
_external_pick_cache: dict[int, tuple[float, list[dict]]] = {}
_QUERY_STOP_WORDS = frozenset({
    "about", "also", "and", "any", "are", "best", "can", "could", "find",
    "for", "from", "give", "good", "i", "in", "like", "me", "media",
    "movie", "movies", "my", "of", "or", "please", "recommend",
    "recommendation", "recommendations", "series", "show", "shows", "similar",
    "something", "that", "the", "to", "want", "watch", "with", "you",
})

_PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "reason": {"type": "string"},
                    "bucket": {"type": "string", "enum": ["comfort", "discovery"]},
                },
                "required": ["id", "reason", "bucket"],
            },
        },
        "message": {"type": "string"},
    },
    "required": ["picks"],
}

_MIX_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "picks": {
            "type": "array",
            "items": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        },
    },
    "required": ["picks"],
}

_AGENT_TOOLS = [{"functionDeclarations": [
    {
        "name": "search_library",
        "description": "Search playable titles in the private library. Use this for names, people, moods, genres, and keywords.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "kind": {"type": "string", "enum": ["movies", "series", "music"]},
            "year": {"type": "integer"}, "genre": {"type": "string"},
            "sort": {"type": "string", "enum": ["relevance", "newest", "oldest"]},
        }},
    },
    {
        "name": "browse_library",
        "description": "Browse playable library titles by type, genre, and a year range when search is too narrow.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["movies", "series", "music"]}, "genre": {"type": "string"},
            "year_from": {"type": "integer"}, "year_to": {"type": "integer"},
            "sort": {"type": "string", "enum": ["newest", "relevance"]},
        }},
    },
    {
        "name": "get_title_details",
        "description": "Get compact details only for ids returned by an earlier library tool call.",
        "parameters": {"type": "object", "properties": {
            "ids": {"type": "array", "items": {"type": "string"}},
        }, "required": ["ids"]},
    },
]}]


class AgentRunError(RuntimeError):
    """The bounded agent could not produce a grounded result."""


# ---- pure helpers (unit-tested in test_ai_rec.py) -------------------------

def _dedup_payloads(
    payloads: list,
    exclude_keys: set[str],
    exclude_item_ids: set[str] | None = None,
) -> list:
    """Drop duplicate cards and titles the user already watched/listened to."""
    exclude_item_ids = exclude_item_ids or set()
    seen: set = set()
    out = []
    for p in payloads:
        href = p.get("href")
        if not href or href in seen:
            continue
        if str(p.get("itemId") or "") in exclude_item_ids:
            continue
        if p.get("watchKey") and p.get("watchKey") in exclude_keys:
            continue
        seen.add(href)
        out.append(p)
    return out


def _exclude_tmdb_payloads(payloads: list, excluded: set) -> list:
    """Keep non-TMDB cards, but never surface a title the user excluded.

    Candidate sources such as newest-by-genre and query matches do not all
    apply the recommendation engine's exclusion set themselves.
    """
    out = []
    for payload in payloads:
        try:
            key = (int(payload.get("tmdbId") or 0), str(payload.get("tmdbKind") or ""))
        except (TypeError, ValueError):
            key = (0, "")
        if key[0] and key in excluded:
            continue
        out.append(payload)
    return out


def _select_candidate_payloads(payloads: list, query_hrefs: set[str]) -> list:
    """Bound the prompt while retaining ranked matches for an explicit ask."""
    query_matches = [payload for payload in payloads if payload.get("href") in query_hrefs]
    other_matches = [payload for payload in payloads if payload.get("href") not in query_hrefs]
    # Query matches already come from catalogue relevance ordering. Do not let
    # diversity shuffling remove the title or genre the user explicitly named.
    retained_query = query_matches[:_QUERY_CANDIDATE_RESERVE]
    random.shuffle(other_matches)
    return (retained_query + other_matches)[:_MAX_CANDIDATES]


def _index_candidates(payloads: list) -> tuple[dict, list]:
    """Assign each candidate a stable id and build the compact prompt list."""
    index: dict = {}
    prompt_items = []
    for i, p in enumerate(payloads):
        cid = f"c{i}"
        index[cid] = p
        prompt_items.append({
            "id": cid,
            "title": p.get("title") or "",
            "type": p.get("kind") or p.get("eyebrow") or ("Music" if p.get("aspect") == "square" else "Video"),
            "year": p.get("year"),
            "by": p.get("artist") or p.get("subtitle") or "",
            "genres": (p.get("genres") or [])[:4],
            "keywords": (p.get("keywords") or [])[:6],
            "summary": (p.get("overview") or "")[:220],
        })
    return index, prompt_items


def _query_terms(query: str) -> list[str]:
    """Extract useful catalogue-search terms from a natural-language ask.

    This is deliberately a retrieval aid, not an attempt to interpret the
    request. Gemini still decides relevance, but it now receives candidates
    that match named titles, genres, people, and TMDB keywords in the query.
    """
    terms: list[str] = []
    for term in re.findall(r"[\w'-]+", (query or "").lower()):
        if len(term) < 3 or term in _QUERY_STOP_WORDS or term in terms:
            continue
        terms.append(term)
        if len(terms) >= _QUERY_TERM_LIMIT:
            break
    return terms


def _mix_title(prompt: str) -> str:
    cleaned = re.sub(r"\s+", " ", (prompt or "").strip())
    return f"{cleaned[:58]} Mix" if cleaned else "Your AI Mix"


def _mix_text(item) -> str:
    return " ".join([
        str(getattr(item, "title", "") or ""),
        str(getattr(item, "artist", "") or ""),
        str(getattr(item, "album_title", "") or ""),
        " ".join(str(value) for value in (getattr(item, "tags", None) or [])),
        " ".join(str(value) for value in (getattr(item, "tmdb_genres", None) or [])),
    ]).lower()


def _rank_mix_candidates(items: list, history_items: list, prompt: str, discovery: str) -> list:
    """Rank local audio tracks without trusting the model to invent a result."""
    artist_weights: Counter = Counter()
    tag_weights: Counter = Counter()
    genre_weights: Counter = Counter()
    listened_ids: set[int] = set()
    for rank, item in enumerate(history_items):
        if item is None or getattr(item, "media_kind", "") != "audio":
            continue
        weight = max(1.0, 6.0 - rank * 0.08)
        listened_ids.add(int(getattr(item, "message_id", 0) or 0))
        for artist in media_index._artist_credits(getattr(item, "artist", "") or ""):
            artist_weights[media_index._artist_slug(artist)] += weight
        for tag in getattr(item, "tags", None) or []:
            tag_weights[str(tag).lower()] += weight
        for genre in getattr(item, "tmdb_genres", None) or []:
            genre_weights[str(genre).lower()] += weight

    terms = _query_terms(prompt)
    scored: list[tuple[float, int, object]] = []
    newest_id = max((int(getattr(item, "message_id", 0) or 0) for item in items), default=1)
    for item in items:
        if getattr(item, "hidden", False) or getattr(item, "media_kind", "") != "audio":
            continue
        item_id = int(getattr(item, "message_id", 0) or 0)
        text = _mix_text(item)
        query_score = sum(10 for term in terms if term in text)
        affinity = 0.0
        for artist in media_index._artist_credits(getattr(item, "artist", "") or ""):
            affinity += artist_weights[media_index._artist_slug(artist)] * 2.5
        affinity += sum(tag_weights[str(tag).lower()] for tag in (getattr(item, "tags", None) or []))
        affinity += sum(genre_weights[str(genre).lower()] for genre in (getattr(item, "tmdb_genres", None) or []))
        freshness = item_id / newest_id
        already_known = item_id in listened_ids
        if discovery == "familiar":
            score = query_score * 2 + affinity * 2 + (1.5 if already_known else 0) + freshness * 0.2
        elif discovery == "discover":
            score = query_score * 2.5 + affinity * 0.65 + (0 if already_known else 2.5) + freshness
        else:
            score = query_score * 2.25 + affinity + (0.5 if already_known else 1.2) + freshness * 0.5
        # A prompt should still produce a pleasant local mix when catalogue
        # metadata is thin, so each valid track receives a small floor.
        scored.append((score, item_id, item))
    random.shuffle(scored)
    # Keep the shuffle as the tiebreaker so regenerating an otherwise-equal
    # request can surface a fresh order rather than always the newest IDs.
    scored.sort(key=lambda row: row[0], reverse=True)
    return [item for _, _, item in scored[:_MIX_CANDIDATE_LIMIT]]


def _mix_prompt_items(items: list) -> tuple[dict, list]:
    index: dict = {}
    prompt_items: list = []
    for number, item in enumerate(items):
        cid = f"m{number}"
        index[cid] = item
        prompt_items.append({
            "id": cid,
            "title": getattr(item, "title", "") or "",
            "artist": getattr(item, "artist", "") or "",
            "album": getattr(item, "album_title", "") or "",
            "genres": list(getattr(item, "tmdb_genres", None) or [])[:4],
            "tags": list(getattr(item, "tags", None) or [])[:6],
        })
    return index, prompt_items


def _mix_items_from_picks(picks: object, index: dict, limit: int) -> list:
    selected: list = []
    seen: set[int] = set()
    for pick in picks if isinstance(picks, list) else []:
        if not isinstance(pick, dict):
            continue
        item = index.get(str(pick.get("id") or ""))
        item_id = int(getattr(item, "message_id", 0) or 0) if item is not None else 0
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


async def get_ai_mix(user_id: int, *, prompt: str = "", discovery: str = "balanced") -> dict:
    """Create one finite, grounded music mix from the user's local catalogue."""
    prompt = re.sub(r"\s+", " ", (prompt or "").strip())[:240]
    discovery = discovery if discovery in {"familiar", "balanced", "discover"} else "balanced"
    history = await wh_store.get_recent(user_id, limit=100)
    history_items = [rec_engine._item_for_cw_key(str(entry.get("cw_key") or "")) for entry in history]
    audio_items = [
        item for item in media_index._items.values()
        if not getattr(item, "hidden", False) and getattr(item, "media_kind", "") == "audio"
    ]
    candidates = _rank_mix_candidates(audio_items, history_items, prompt, discovery)
    if len(candidates) < 3:
        return {"error": "Not enough music in your library to build a mix."}

    chosen = candidates[:_MIX_SIZE]
    title = _mix_title(prompt)
    description = "A personal mix from your library."
    generated = False
    if gemini.available():
        index, prompt_items = _mix_prompt_items(candidates)
        generation_prompt = "\n".join([
            "You are sequencing a finite, personal music mix from a user's private library.",
            "Use ONLY the candidate ids. Return up to 20 distinct tracks in a satisfying listening order.",
            f"Listener request: {prompt or 'A mix tuned to their listening history'}",
            f"Discovery setting: {discovery}.",
            "Create a concise title (max 60 characters) and a description (max 100 characters).",
            "Candidates:",
            json.dumps(prompt_items, ensure_ascii=False),
        ])
        result = await gemini.generate_json(generation_prompt, schema=_MIX_SCHEMA, timeout=45)
        selected = _mix_items_from_picks(result.get("picks") if isinstance(result, dict) else None, index, _MIX_SIZE)
        if len(selected) >= 3:
            selected_ids = {item.message_id for item in selected}
            # Gemini occasionally returns fewer than requested IDs. Keep its
            # deliberate sequence, then fill from the same grounded pool.
            chosen = (selected + [item for item in candidates if item.message_id not in selected_ids])[:_MIX_SIZE]
            title = str(result.get("title") or title).strip()[:60] or title
            description = str(result.get("description") or description).strip()[:100] or description
            generated = True

    from main.server import spa_routes as _spa
    return {
        "title": title,
        "description": description,
        "prompt": prompt,
        "discovery": discovery,
        "tracks": [_spa._track_payload(item) for item in chosen],
        "generated": generated,
    }


def _apply_picks(picks: list, index: dict, limit: int) -> list:
    """Ground Gemini's picks: keep only real candidate ids, dedup, attach the
    reason + bucket, cap to ``limit``."""
    out = []
    seen: set = set()
    for pick in picks or []:
        if not isinstance(pick, dict):  # defend against a stray non-object pick
            continue
        cid = str(pick.get("id") or "")
        payload = index.get(cid)
        if payload is None:  # hallucinated / stale id — drop it
            continue
        href = payload.get("href")
        if href in seen:
            continue
        seen.add(href)
        bucket = "discovery" if pick.get("bucket") == "discovery" else "comfort"
        out.append({
            **payload,
            "recReason": (pick.get("reason") or "").strip(),
            "bucket": bucket,
        })
        if len(out) >= limit:
            break
    return out


def _validate_cached(
    items: list,
    *,
    exclude_keys: set[str] | None = None,
    exclude_item_ids: set[str] | None = None,
    excluded_tmdb: set[tuple[int, str]] | None = None,
) -> list:
    """Drop stale, watched, and explicitly excluded cached cards.

    This validation is deliberately repeated on cache reads: a person may
    finish a title, or an admin may hide/delete an upload, after the set was
    written. Grouped movie, series, and album cards are checked against their
    live members as well as ordinary numeric upload cards.
    """
    exclude_keys = exclude_keys or set()
    exclude_item_ids = exclude_item_ids or set()
    excluded_tmdb = excluded_tmdb or set()
    out = []
    for item in items or []:
        if str(item.get("watchKey") or "") in exclude_keys:
            continue
        try:
            tmdb_key = (int(item.get("tmdbId") or 0), str(item.get("tmdbKind") or ""))
        except (TypeError, ValueError):
            tmdb_key = (0, "")
        if tmdb_key[0] and tmdb_key in excluded_tmdb:
            continue
        iid = str(item.get("itemId") or "")
        if iid and iid in exclude_item_ids:
            continue
        if iid.isdigit():
            obj = media_index.get_item(int(iid))
            if obj is None or getattr(obj, "hidden", False):
                continue
        elif ":" in iid:
            group_kind, group_key = iid.split(":", 1)
            group_field = {"movie": "movie_key", "series": "series_key", "album": "album_key"}.get(group_kind)
            if group_field and not any(
                getattr(obj, group_field, "") == group_key and not getattr(obj, "hidden", False)
                for obj in media_index._items.values()
            ):
                continue
        out.append(item)
    return out


def _has_user_activity(profile: dict, stats: dict, history: list, cw_map: dict) -> bool:
    """Whether we have real behaviour, independent of recommendation health.

    A user can have a substantial history whose older uploads have not yet
    been TMDB-enriched. They are *not* a cold-start user; we may temporarily
    fall back to fresh titles, but must not tell them that we are still
    learning their taste. Likewise, Gemini/TMDB availability and candidate
    pool size describe the ranking pipeline, not the user's activity.
    """
    return bool(
        history
        or cw_map
        or profile.get("seeds")
        or stats.get("top_genres")
        or stats.get("top_artists")
    )


def _watched_card_ids(watch_keys: set[str]) -> set[str]:
    """Map watched uploads to the grouped card IDs used by AI Picks.

    Watch history stores a specific upload key, while a recommendation card
    can represent a whole movie or series and choose another upload as its
    poster/play source. Comparing only ``watchKey`` can therefore re-suggest
    a title the user has already watched.
    """
    ids: set[str] = set()
    for key in watch_keys:
        item = rec_engine._item_for_cw_key(key)
        if item is None:
            continue
        if getattr(item, "series_key", ""):
            ids.add(f"series:{item.series_key}")
        elif getattr(item, "movie_key", ""):
            ids.add(f"movie:{item.movie_key}")
        else:
            message_id = int(getattr(item, "message_id", 0) or 0)
            if message_id:
                ids.add(str(message_id))
    return ids


def _now() -> float:
    """Monotonic clock for the short-lived external-picks cache."""
    return time.monotonic()


def _taste_summary(profile: dict, stats: dict) -> str:
    parts = []
    genres = [g for g, _ in (stats.get("top_genres") or [])][:5]
    if genres:
        parts.append("Top genres: " + ", ".join(genres))
    director = stats.get("top_director")
    if isinstance(director, (list, tuple)):  # stats stores ("Name", count)
        director = director[0] if director else None
    if director:
        parts.append("Favourite director: " + str(director))
    artists = [a for a, _ in (stats.get("top_artists") or [])][:3]
    if artists:
        parts.append("Top artists: " + ", ".join(artists))
    pers = stats.get("personality")
    if isinstance(pers, dict) and pers.get("title"):
        parts.append("Listener type: " + str(pers["title"]))
    elif isinstance(pers, str) and pers:
        parts.append("Listener type: " + pers)
    return "; ".join(parts) or "Not much history yet."


def _build_prompt(taste: str, prompt_items: list, query: str, limit: int) -> str:
    lines = [
        "You are a personal media curator for a single user's PRIVATE library.",
        "Recommend ONLY items from the candidate list, using their exact id. Never invent titles.",
        "Return a balanced mix: some 'comfort' picks close to the user's taste and some",
        "'discovery' picks that are more adventurous but still justified by their taste.",
        "For each pick write ONE concrete, useful 'why for you' reason (max 9 words). Anchor it",
        "to the user's request, a specific title/creator, mood, or taste signal. Do not start with",
        "'Fans of' and do not merely restate the item's genres, format, or 'from your library'.",
        "",
        f"User taste: {taste}",
    ]
    if query:
        lines += ["", f"The user asked for: {query!r}. Prioritise picks matching this request."]
    lines += [
        "",
        "Candidates (JSON):",
        json.dumps(prompt_items, ensure_ascii=False),
        "",
        f"Choose up to {limit} picks. Also set 'message' to one friendly sentence about the set.",
    ]
    return "\n".join(lines)


# ---- orchestration -------------------------------------------------------

async def _safe_stats(user_id: int) -> dict:
    try:
        from main.server.stats_routes import _stats_payload
        return await _stats_payload(user_id)
    except Exception:
        logging.debug("ai_rec: stats payload failed", exc_info=True)
        return {}


async def _gather_candidates(
    user_id: int,
    profile: dict,
    stats: dict,
    dismissed,
) -> list:
    """Assemble a diverse pool of catalogue objects: TMDB-based recs (comfort),
    fresh titles in top genres (discovery), top-artist tracks + fresh music, and
    globally popular items."""
    objs: list = []

    try:
        # Pass profile AND dismissed so get_recommendations doesn't recompute the
        # (4-Mongo-call) signal profile internally.
        recs = await rec_engine.get_recommendations(user_id, profile=profile, dismissed=dismissed)
        if recs:
            objs += list(recs)
    except Exception:
        logging.debug("ai_rec: get_recommendations failed", exc_info=True)

    video_genres = [g for g, _ in (stats.get("top_genres") or [])][:3]
    if not video_genres:
        video_genres = [g for g, _ in (profile.get("seed_genres") or {}).most_common(3)] \
            if hasattr(profile.get("seed_genres"), "most_common") else []
    for genre in video_genres:
        try:
            items, _ = media_index.query_grouped(genre=genre, sort="newest", limit=8)
            objs += items
        except Exception:
            pass

    for name in [a for a, _ in (stats.get("top_artists") or [])][:4]:
        try:
            slug = media_index._artist_slug(media_index._primary_artist(name))
            objs += media_index.tracks_by_artist_slug(slug)[:4]
        except Exception:
            pass
    try:
        music_items, _ = media_index.query_grouped(view="music", sort="newest", limit=12)
        objs += music_items
    except Exception:
        pass

    try:
        for entry in await wh_store.get_top_plays(limit=15):
            item = rec_engine._item_for_cw_key(entry.get("cw_key", ""))
            if item is not None:
                objs.append(item)
    except Exception:
        pass

    return objs


async def _gather_query_candidates(query: str) -> list:
    """Retrieve ranked catalogue matches for a user's explicit AI request."""
    objs: list = []
    for term in [query, *_query_terms(query)]:
        try:
            matches, _ = media_index.query_grouped(q=term, sort="newest", limit=8)
            objs += matches
        except Exception:
            logging.debug("ai_rec: query candidate retrieval failed", exc_info=True)
    return objs


async def _trending_items(
    limit: int,
    *,
    exclude_keys: set[str] | None = None,
    exclude_item_ids: set[str] | None = None,
    excluded_tmdb: set[tuple[int, str]] | None = None,
) -> list:
    """Return a safe cold-start fallback.

    Trending is a fallback for insufficient taste signals, not an exception
    to the "don't recommend something already watched" rule.
    """
    from main.server import spa_routes as _spa
    try:
        # Ask for a small reserve because filters can legitimately remove the
        # user's recent history from an otherwise short catalogue shelf.
        items, _ = media_index.query_grouped(sort="newest", limit=max(limit * 3, limit))
        payloads = [_spa._card(item) for item in items]
        payloads = _dedup_payloads(payloads, exclude_keys or set(), exclude_item_ids)
        payloads = _exclude_tmdb_payloads(payloads, excluded_tmdb or set())
        return [{**item, "recReason": "", "bucket": "comfort"} for item in payloads[:limit]]
    except Exception:
        logging.debug("ai_rec: trending fallback failed", exc_info=True)
        return []


async def _requestable_picks(user_id: int, profile: dict, dismissed: set, query: str) -> list[dict]:
    """Return a tiny set of verified, requestable titles outside the library.

    Unlike normal AI cards these have no play URL. Direct user asks use TMDB
    search; passive discovery uses the same TMDB recommendation graph as the
    local recommender. Both paths are filtered against library, dismissals,
    and titles the user has already requested.
    """
    if not tmdb.is_configured() or not request_store.is_available():
        return []
    if not query:
        cached = _external_pick_cache.get(user_id)
        if cached and _now() - cached[0] < _EXTERNAL_PICK_TTL:
            return cached[1]
    requested = await request_store.requested_keys(user_id)
    excluded = set(profile.get("exclude_tmdb") or set()) | set(dismissed or set()) | requested
    if query:
        rows = await tmdb.search_titles(query, limit=8)
        candidate_keys = [(int(row["tmdbId"]), str(row["kind"])) for row in rows]
    else:
        candidates = await rec_engine._fetch_recs_for_seeds(profile.get("seeds") or [], excluded)
        candidate_keys = [(tid, kind) for tid, kind, _score in candidates[:16]]
    chosen: list[tuple[int, str]] = []
    for tmdb_id, kind in candidate_keys:
        if (tmdb_id, kind) in excluded or request_store.library_availability(tmdb_id, kind)["inLibrary"]:
            continue
        if (tmdb_id, kind) not in chosen:
            chosen.append((tmdb_id, kind))
        if len(chosen) >= 3:
            break
    details = await asyncio.gather(
        *(tmdb.fetch_request_title(tmdb_id, kind) for tmdb_id, kind in chosen),
        return_exceptions=True,
    )
    out = [{**detail, "recReason": "A related title beyond your library."} for detail in details if isinstance(detail, dict)]
    if not query:
        _external_pick_cache[user_id] = (_now(), out)
    return out


def _clean_agent_args(name: str, raw: object) -> dict:
    """Validate model function arguments before they reach catalogue code."""
    raw = raw if isinstance(raw, dict) else {}
    text = lambda key, maximum: re.sub(r"\s+", " ", str(raw.get(key) or "").strip())[:maximum]
    kind = text("kind", 12).lower()
    if kind not in {"movies", "series", "music"}:
        kind = ""
    sort = text("sort", 12).lower()
    allowed_sorts = {"relevance", "newest", "oldest"} if name == "search_library" else {"relevance", "newest"}
    if sort not in allowed_sorts:
        sort = "newest" if name == "browse_library" else "relevance"

    def year(key: str) -> int | None:
        try:
            value = int(raw.get(key))
        except (TypeError, ValueError):
            return None
        return value if 1888 <= value <= 2100 else None

    if name == "get_title_details":
        ids = raw.get("ids")
        return {"ids": [str(value)[:40] for value in ids[:_AGENT_TOOL_RESULT_LIMIT]] if isinstance(ids, list) else []}
    args = {"kind": kind, "genre": text("genre", 60), "sort": sort}
    if name == "search_library":
        args.update({"query": text("query", 120), "year": year("year")})
    else:
        start, end = year("year_from"), year("year_to")
        if start and end and start > end:
            start, end = end, start
        args.update({"year_from": start, "year_to": end})
    return args


class _AgentCatalogue:
    """A per-request, filtered view of grouped cards exposed to Gemini."""

    def __init__(self, *, seen_keys: set[str], watched_ids: set[str], excluded_tmdb: set[tuple[int, str]]):
        self.seen_keys = seen_keys
        self.watched_ids = watched_ids
        self.excluded_tmdb = excluded_tmdb
        self._art_cache: dict = {}
        self._by_href: dict[str, str] = {}
        self.payloads: dict[str, dict] = {}

    def _compact(self, identifier: str, payload: dict) -> dict:
        return {
            "id": identifier,
            "title": str(payload.get("title") or "")[:140],
            "kind": str(payload.get("eyebrow") or "Video")[:24],
            "year": payload.get("year"),
            "creator": str(payload.get("artist") or payload.get("subtitle") or "")[:120],
            "genres": list(payload.get("genres") or [])[:4],
            "keywords": list(payload.get("keywords") or [])[:6],
            "overview": str(payload.get("overview") or "")[:240],
            "availability": {"playable": True, "itemId": str(payload.get("itemId") or "")[:80]},
        }

    def _register(self, cards: list) -> list[dict]:
        from main.server import spa_routes as _spa
        payloads = [_spa._card(card, art_cache=self._art_cache) for card in cards]
        payloads = _dedup_payloads(payloads, self.seen_keys, self.watched_ids)
        payloads = _exclude_tmdb_payloads(payloads, self.excluded_tmdb)
        # A second cache-style validation covers hidden/deleted single uploads
        # and makes all three tools subject to identical eligibility rules.
        payloads = _validate_cached(
            payloads, exclude_keys=self.seen_keys, exclude_item_ids=self.watched_ids,
            excluded_tmdb=self.excluded_tmdb,
        )
        result: list[dict] = []
        for payload in payloads[:_AGENT_TOOL_RESULT_LIMIT]:
            href = str(payload.get("href") or "")
            if not href:
                continue
            identifier = self._by_href.get(href)
            if not identifier:
                identifier = f"card_{len(self.payloads) + 1}"
                self._by_href[href] = identifier
                self.payloads[identifier] = payload
            result.append(self._compact(identifier, self.payloads[identifier]))
        return result

    def run(self, name: str, raw_args: object) -> list[dict]:
        args = _clean_agent_args(name, raw_args)
        if name == "get_title_details":
            return [self._compact(identifier, self.payloads[identifier]) for identifier in args["ids"] if identifier in self.payloads]
        if name not in {"search_library", "browse_library"}:
            return []
        view = {"movies": "movies", "series": "series", "music": "music"}.get(args["kind"], "")
        if name == "search_library":
            cards, _ = media_index.query_grouped(
                q=args["query"], year=args["year"], genre=args["genre"],
                view=view, sort="newest" if args["sort"] == "relevance" else args["sort"],
                limit=_AGENT_TOOL_RESULT_LIMIT,
            )
        else:
            # query_grouped supports exact years; a small newest page is then
            # range-filtered. The result remains bounded and grouped.
            cards, _ = media_index.query_grouped(
                genre=args["genre"], view=view, sort="newest", limit=_AGENT_TOOL_RESULT_LIMIT * 3,
            )
            start, end = args["year_from"], args["year_to"]
            if start or end:
                def card_year(card) -> int:
                    return int(getattr(card, "year", None) or getattr(getattr(card, "poster_item", None), "year", 0) or 0)
                cards = [card for card in cards if (not start or card_year(card) >= start)
                         and (not end or card_year(card) <= end)]
        return self._register(list(cards))


def _function_calls(response: object) -> tuple[dict | None, list[dict]]:
    """Extract Gemini function calls without trusting response structure."""
    try:
        content = response["candidates"][0]["content"]
        parts = content.get("parts") or []
    except (KeyError, IndexError, TypeError):
        return None, []
    calls = [part["functionCall"] for part in parts if isinstance(part, dict) and isinstance(part.get("functionCall"), dict)]
    return content if isinstance(content, dict) else None, calls


async def _emit_agent_status(callback: Callable[[str], Awaitable[None]] | None, text: str) -> None:
    if callback is not None:
        await callback(text)


async def _generate_agentic(
    user_id: int, *, query: str, refresh: bool, limit: int,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    """Use at most three read-only catalogue tool calls, then rank discovered ids."""
    started = time.monotonic()
    profile, history, cw_map, dismissed = await asyncio.gather(
        rec_engine._collect_signal_profile(user_id), wh_store.get_recent(user_id, limit=80),
        cw_store.get_all(user_id), dismissed_store.get_dismissed_ids(user_id),
    )
    stats = await _safe_stats(user_id)
    seen_keys = {str(entry.get("cw_key") or "") for entry in history} | set(cw_map)
    watched_ids = _watched_card_ids(seen_keys)
    excluded = set(profile.get("exclude_tmdb") or set()) | set(dismissed or set())
    catalogue = _AgentCatalogue(seen_keys=seen_keys, watched_ids=watched_ids, excluded_tmdb=excluded)
    intent = query or "Refresh the user's library picks with a useful, varied set."
    contents = [{"role": "user", "parts": [{"text": "\n".join([
        "You curate a private playable media library.",
        "Use the read-only tools to find candidates before recommending anything.",
        "Never ask for or reveal private catalogue data beyond tool results. Do not use unavailable titles.",
        "You have at most three total tool calls; explore efficiently.",
        f"User taste summary: {_taste_summary(profile, stats)}",
        f"User request: {intent}",
    ])}]}]
    await _emit_agent_status(progress, "Searching your library")
    calls_used = 0
    explored = False
    while calls_used < _AGENT_MAX_TOOL_CALLS:
        remaining = _AGENT_BUDGET_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            raise AgentRunError("budget")
        response = await gemini.generate_content(contents, tools=_AGENT_TOOLS, timeout=remaining)
        model_content, calls = _function_calls(response)
        if response is None or model_content is None:
            raise AgentRunError("model")
        if not explored:
            await _emit_agent_status(progress, "Exploring related titles")
            explored = True
        if not calls:
            break
        contents.append(model_content)
        answers = []
        for call in calls[:_AGENT_MAX_TOOL_CALLS - calls_used]:
            name = str(call.get("name") or "")
            result = catalogue.run(name, call.get("args"))
            answers.append({"functionResponse": {"name": name, "response": {"items": result}}})
            calls_used += 1
        contents.append({"role": "user", "parts": answers})
    if not catalogue.payloads:
        raise AgentRunError("no_candidates")
    await _emit_agent_status(progress, "Curating picks")
    remaining = _AGENT_BUDGET_SECONDS - (time.monotonic() - started)
    if remaining <= 0:
        raise AgentRunError("budget")
    candidates = [catalogue._compact(identifier, payload) for identifier, payload in catalogue.payloads.items()]
    prompt = _build_prompt(_taste_summary(profile, stats), candidates, query, limit)
    result = await gemini.generate_json(prompt, schema=_PICK_SCHEMA, timeout=remaining)
    picks = result.get("picks") if isinstance(result, dict) else None
    items = _apply_picks(picks, catalogue.payloads, limit)
    # Revalidate after model work in case a deletion/hide/finish raced a tool.
    items = _validate_cached(items, exclude_keys=seen_keys, exclude_item_ids=watched_ids, excluded_tmdb=excluded)
    if not items:
        raise AgentRunError("invalid_picks")
    if refresh:
        await rec_store.clear_cached(user_id)
        await ai_rec_store.set_cached(user_id, items)
    logging.info("ai_rec_agent tool_count=%d elapsed_ms=%d candidate_count=%d final_pick_count=%d fallback_reason=%s",
                 calls_used, round((time.monotonic() - started) * 1000), len(catalogue.payloads), len(items), "")
    return {
        "items": items,
        "externalItems": await _requestable_picks(user_id, profile, dismissed, query),
        "message": str(result.get("message") or "").strip()[:240] if isinstance(result, dict) else "",
        "coldStart": False,
    }


async def get_ai_recommendations(
    user_id: int,
    *,
    query: Optional[str] = None,
    limit: int = 12,
    refresh: bool = False,
    agentic: bool = False,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    """Return ``{items, message, coldStart}`` — catalogue-grounded AI picks.

    Any unexpected failure degrades to trending so the endpoint never 500s.
    """
    try:
        if agentic:
            try:
                return await _generate_agentic(
                    user_id, query=(query or "").strip(), refresh=refresh, limit=limit, progress=progress,
                )
            except Exception as exc:
                # The fallback intentionally uses no function calls and no
                # extra model call. A bad response must feel like a slightly
                # less tailored shelf, never an error page or endless spinner.
                logging.info("ai_rec_agent tool_count=%d elapsed_ms=%d candidate_count=%d final_pick_count=%d fallback_reason=%s",
                             0, 0, 0, 0, type(exc).__name__)
                await _emit_agent_status(progress, "Curating picks")
                return await _generate(user_id, query=query, limit=limit, refresh=refresh, rank_with_gemini=False)
        return await _generate(user_id, query=query, limit=limit, refresh=refresh)
    except Exception:
        logging.exception("ai_rec: generation failed, serving trending fallback")
        # An optional ranking dependency must not turn into an endless client
        # spinner or re-suggest a watched grouped title. Make one bounded
        # best-effort pass over local activity before serving the fallback.
        try:
            history, cw_map = await asyncio.gather(
                wh_store.get_recent(user_id, limit=80),
                cw_store.get_all(user_id),
            )
            seen_keys = {str(entry.get("cw_key") or "") for entry in history} | set(cw_map)
            items = await _trending_items(
                limit,
                exclude_keys=seen_keys,
                exclude_item_ids=_watched_card_ids(seen_keys),
            )
        except Exception:
            logging.exception("ai_rec: fallback activity filter failed")
            items = await _trending_items(limit)
        return {
            "items": items,
            "externalItems": [],
            "message": "We couldn't tailor picks just now; showing fresh titles instead. Try Refresh.",
            "coldStart": False,
        }


async def _generate(
    user_id: int, *, query: Optional[str], limit: int, refresh: bool,
    rank_with_gemini: bool = True,
) -> dict:
    query = (query or "").strip()
    read_cache = not query and not refresh
    write_cache = not query  # refresh recomputes AND refreshes the stored cache

    from main.server import spa_routes as _spa  # lazy: card builders

    profile, history, cw_map, dismissed = await asyncio.gather(
        rec_engine._collect_signal_profile(user_id),
        wh_store.get_recent(user_id, limit=80),
        cw_store.get_all(user_id),
        dismissed_store.get_dismissed_ids(user_id),
    )
    if read_cache:
        cached = await ai_rec_store.get_cached(user_id)
        if cached:
            cache_excluded = set(profile.get("exclude_tmdb") or set()) | set(dismissed or set())
            valid = _validate_cached(
                cached,
                exclude_keys={str(entry.get("cw_key") or "") for entry in history} | set(cw_map),
                exclude_item_ids=_watched_card_ids(
                    {str(entry.get("cw_key") or "") for entry in history} | set(cw_map)
                ),
                excluded_tmdb=cache_excluded,
            )
            if len(valid) >= 3:  # else the cache is too stale — regenerate below
                return {
                    "items": valid,
                    "externalItems": await _requestable_picks(user_id, profile, dismissed, ""),
                    "message": "", "coldStart": False, "cached": True,
                }
    stats = await _safe_stats(user_id)

    # A deliberate refresh should regenerate its TMDB-derived candidate pool,
    # not ask Gemini to reshuffle the same 24-hour local recommendation cache.
    if refresh:
        await rec_store.clear_cached(user_id)

    async def _finish(result: dict) -> dict:
        if write_cache and result.get("items"):
            await ai_rec_store.set_cached(user_id, result["items"])
        return result

    # ``coldStart`` is exclusively about the absence of user activity. A
    # missing Gemini key or a thin candidate pool is a service/catalogue
    # fallback, not proof that the user has no watch history.
    has_activity = _has_user_activity(profile, stats, history, cw_map)
    has_signal = bool(profile.get("seeds")) or bool(stats.get("top_genres")) or bool(stats.get("top_artists"))
    seen_keys = {str(entry.get("cw_key") or "") for entry in history} | set(cw_map.keys())
    watched_card_ids = _watched_card_ids(seen_keys)
    excluded = set(profile.get("exclude_tmdb") or set()) | set(dismissed or set())
    if not has_signal:
        return await _finish({
            "items": await _trending_items(
                limit, exclude_keys=seen_keys, exclude_item_ids=watched_card_ids, excluded_tmdb=excluded,
            ),
            "externalItems": [],
            "message": (
                "Your activity is saved; some watched titles still need library metadata before picks can be tailored."
                if has_activity else ""
            ),
            "coldStart": not has_activity,
        })
    if not gemini.available():
        return await _finish({
            "items": await _trending_items(
                limit, exclude_keys=seen_keys, exclude_item_ids=watched_card_ids, excluded_tmdb=excluded,
            ),
            "externalItems": [],
            "message": "Personalized ranking is temporarily unavailable; here are fresh picks." if has_activity else "",
            "coldStart": not has_activity,
        })

    objs = await _gather_candidates(user_id, profile, stats, dismissed)
    query_objs = await _gather_query_candidates(query) if query else []
    art_cache: dict = {}
    query_payloads = [_spa._card(obj, art_cache=art_cache) for obj in query_objs]
    query_hrefs = {payload.get("href") for payload in query_payloads if payload.get("href")}
    payloads = _dedup_payloads(
        query_payloads + [_spa._card(obj, art_cache=art_cache) for obj in objs], seen_keys, watched_card_ids,
    )
    payloads = _exclude_tmdb_payloads(payloads, excluded)
    payloads = _select_candidate_payloads(payloads, query_hrefs)

    if len(payloads) < 6:
        return await _finish({
            "items": await _trending_items(
                limit, exclude_keys=seen_keys, exclude_item_ids=watched_card_ids, excluded_tmdb=excluded,
            ),
            "externalItems": [],
            "message": "Your history is applied; here are fresh picks while we find more close matches." if has_activity else "",
            "coldStart": not has_activity,
        })

    def _raw_fallback() -> list:
        return [{**p, "recReason": "", "bucket": "comfort"} for p in payloads[:limit]]

    index, prompt_items = _index_candidates(payloads)
    prompt = _build_prompt(_taste_summary(profile, stats), prompt_items, query, limit)
    result = await gemini.generate_json(prompt, schema=_PICK_SCHEMA, timeout=45) if rank_with_gemini else None

    picks = result.get("picks") if isinstance(result, dict) else None
    if not isinstance(picks, list) or not picks:
        return await _finish({"items": _raw_fallback(), "externalItems": await _requestable_picks(user_id, profile, dismissed, query), "message": "", "coldStart": False})

    items = _apply_picks(picks, index, limit) or _raw_fallback()
    message = (result.get("message") or "").strip()
    return await _finish({"items": items, "externalItems": await _requestable_picks(user_id, profile, dismissed, query), "message": message, "coldStart": False})
