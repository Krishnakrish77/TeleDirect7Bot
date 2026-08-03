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
_AI_REC_HISTORY_LIMIT = 200  # matches the retained server-side watch-history cap
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
_TASTE_MATCH_RE = re.compile(
    r"\b(?:will|would|should)\s+(?:i|we)\s+(?:like|enjoy|watch)\b|\b(?:for me|my kind of (?:show|movie|series))\b",
    re.I,
)

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
        "assessment": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "verdict": {"type": "string", "enum": ["likely", "maybe", "unlikely"]},
                "reason": {"type": "string"},
            },
            "required": ["id", "verdict", "reason"],
        },
    },
    "required": ["picks"],
}
_DECISION_PICK_SCHEMA = {**_PICK_SCHEMA, "required": ["picks", "assessment"]}

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
            "query": {"type": "string"}, "kind": {"type": "string", "enum": ["movies", "series"]},
            "year": {"type": "integer"}, "genre": {"type": "string"},
            "sort": {"type": "string", "enum": ["relevance", "newest", "oldest"]},
        }},
    },
    {
        "name": "browse_library",
        "description": "Browse playable library titles by type, genre, and a year range when search is too narrow.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["movies", "series"]}, "genre": {"type": "string"},
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

# Gemini may otherwise answer the initial turn in prose, leaving the agent
# with no grounded candidates. Title details require ids from a prior catalogue
# result, so only discovery tools are eligible for this required first call.
_AGENT_INITIAL_TOOL_CONFIG = {
    "functionCallingConfig": {
        "mode": "ANY",
        "allowedFunctionNames": ["search_library", "browse_library"],
    },
}


class AgentRunError(RuntimeError):
    """The bounded agent could not produce a grounded result."""

    def __init__(
        self, reason: str, *, tool_count: int = 0, candidate_count: int = 0,
        elapsed_ms: int = 0, source_counts: dict[str, int] | None = None,
    ):
        super().__init__(reason)
        self.tool_count = tool_count
        self.candidate_count = candidate_count
        self.elapsed_ms = elapsed_ms
        self.source_counts = source_counts or {}


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


def _video_payloads(payloads: list) -> list:
    """Keep AI Picks for movies and series; Mix owns music discovery."""
    return [
        payload for payload in payloads
        if payload.get("eyebrow") != "Music"
        and payload.get("aspect") != "square"
        and payload.get("mediaKind") != "audio"
    ]


def _balanced_buckets(items: list) -> list:
    """Guarantee both AI Picks sections whenever at least two cards survive."""
    if len(items) < 2:
        return [{**item, "bucket": "comfort"} for item in items]
    has_comfort = any(item.get("bucket") != "discovery" for item in items)
    has_discovery = any(item.get("bucket") == "discovery" for item in items)
    if has_comfort and has_discovery:
        return items
    discovery_start = len(items) - max(1, len(items) // 3)
    return [
        {**item, "bucket": "discovery" if index >= discovery_start else "comfort"}
        for index, item in enumerate(items)
    ]


def _fallback_items(payloads: list, limit: int) -> list:
    return _balanced_buckets([
        {**item, "recReason": "", "bucket": "comfort"}
        for item in payloads[:limit]
    ])


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


def _is_taste_match_question(query: str) -> bool:
    """Whether an Ask wants a direct personal fit verdict for a title."""
    return bool(_TASTE_MATCH_RE.search(query or ""))


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


def _local_rec_reason(payload: dict, profile: dict) -> str:
    """Give deterministic fill-ins a truthful, compact explanation."""
    preferred = {
        str(genre).casefold() for genre in (profile.get("seed_genres") or {}).keys()
    }
    for genre in payload.get("genres") or []:
        if str(genre).casefold() in preferred:
            return f"Matches your {genre} taste."
    themes = {str(theme).casefold() for theme in (profile.get("seed_keywords") or {}).keys()}
    for keyword in payload.get("keywords") or []:
        if str(keyword).casefold() in themes:
            return f"Shares a theme you often enjoy: {keyword}."
    return "A strong fit from your library."


def _rerank_agent_picks(
    model_items: list,
    catalogue,
    profile: dict,
    limit: int,
    *,
    pinned_id: str = "",
    metrics: dict | None = None,
) -> list:
    """Own the final AI shelf: grounded model choices, then safe local fills.

    Gemini supplies the language and initial buckets; deterministic ranking
    makes the shelf robust to short responses and prevents one genre/type from
    swallowing the 60/40 comfort/discovery mix.
    """
    pinned = catalogue.payloads.get(pinned_id) if pinned_id else None
    pinned_href = str(pinned.get("href") or "") if pinned else ""
    model_hrefs = {str(item.get("href") or "") for item in model_items}
    pool: list[tuple[dict, int, float, bool]] = []
    for position, item in enumerate(model_items):
        href = str(item.get("href") or "")
        pool.append((item, position, getattr(catalogue, "scores", {}).get(href, 0.0), href == pinned_href))
    payloads_by_href = catalogue.payloads_by_href() if hasattr(catalogue, "payloads_by_href") else [
        (str(payload.get("href") or ""), payload) for payload in catalogue.payloads.values()
    ]
    for href, payload in payloads_by_href:
        if href in model_hrefs:
            continue
        pool.append(({
            **payload,
            "recReason": _local_rec_reason(payload, profile),
            "bucket": "comfort",
        }, limit + len(pool), getattr(catalogue, "scores", {}).get(href, 0.0), href == pinned_href))
    pool.sort(key=lambda entry: (not entry[3], entry[1] >= limit, -entry[2], entry[1]))

    selected: list[dict] = []
    selected_hrefs: set[str] = set()
    genre_counts: Counter = Counter()
    kind_counts: Counter = Counter()
    for pass_index, (genre_cap, kind_cap) in enumerate(((2, 8), (3, 10), (10_000, 10_000))):
        before = len(selected)
        for item, _position, _score, is_pinned in pool:
            if len(selected) >= limit:
                break
            href = str(item.get("href") or "")
            if not href or href in selected_hrefs:
                continue
            genres = [str(genre) for genre in (item.get("genres") or [])[:2]]
            kind = str(item.get("tmdbKind") or item.get("eyebrow") or "movie")
            if not is_pinned and (any(genre_counts[genre] >= genre_cap for genre in genres) or kind_counts[kind] >= kind_cap):
                continue
            selected.append(item)
            selected_hrefs.add(href)
            kind_counts[kind] += 1
            for genre in genres:
                genre_counts[genre] += 1
        if metrics is not None and pass_index and len(selected) > before:
            metrics["diversity_relaxations"] = int(metrics.get("diversity_relaxations", 0)) + 1
        if len(selected) >= limit:
            break

    # A 12-card shelf is deliberately seven familiar picks and five more
    # adventurous ones.  Preserve model buckets whenever possible, otherwise
    # move the least-personalized non-pinned picks to satisfy the target.
    discovery_target = min(len(selected), limit - (limit * 3 // 5))
    discoveries = [index for index, item in enumerate(selected) if item.get("bucket") == "discovery"]
    if len(discoveries) < discovery_target:
        needed = discovery_target - len(discoveries)
        for index in reversed(range(len(selected))):
            if needed <= 0:
                break
            if str(selected[index].get("href") or "") == pinned_href or selected[index].get("bucket") == "discovery":
                continue
            selected[index] = {**selected[index], "bucket": "discovery"}
            needed -= 1
    elif len(discoveries) > discovery_target:
        for index in reversed(discoveries[discovery_target:]):
            if str(selected[index].get("href") or "") == pinned_href:
                continue
            selected[index] = {**selected[index], "bucket": "comfort"}
    return selected


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
        if not _video_payloads([item]):
            continue
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


def _recommendation_meta(
    origin: str, *, cached: bool = False, fallback: bool = False, generated_at: int | None = None,
) -> dict:
    """Small, user-safe provenance for the AI Picks UI.

    This intentionally describes the result rather than an upstream model or
    error. People need to know whether they are seeing saved, AI-curated, or
    resilient catalogue picks—not Gemini internals.
    """
    safe_origin = origin if origin in {"agent", "library", "fresh"} else "library"
    return {
        "origin": safe_origin,
        "cached": bool(cached),
        "fallback": bool(fallback),
        "generatedAt": max(0, int(time.time() if generated_at is None else generated_at)),
    }


def _with_recommendation_meta(result: dict, origin: str, *, fallback: bool = False) -> dict:
    result["recommendationMeta"] = _recommendation_meta(origin, fallback=fallback)
    return result


def _profile_title_anchors(keys: object, limit: int) -> list[str]:
    """Resolve a small, ordered set of local TMDB keys to display titles.

    This deliberately turns detailed history into a few bounded taste anchors.
    Gemini never receives the underlying play/rating records, timestamps, or
    the rest of a person's viewing history.
    """
    if not isinstance(keys, (list, tuple, set)):
        return []
    # ``seeds`` keeps its weighted order, while rating sets need a stable
    # order so the same private profile produces the same model context.
    values = sorted(keys, key=repr) if isinstance(keys, set) else keys
    anchors: list[str] = []
    seen: set[str] = set()
    for key in values:
        try:
            tmdb_id, kind = key
            card = media_index.card_for_tmdb_id(int(tmdb_id), str(kind))
        except (TypeError, ValueError):
            continue
        if card is None:
            continue
        item = getattr(card, "poster_item", card)
        title = (
            getattr(card, "series_title", "")
            or getattr(card, "title", "")
            or getattr(item, "series_title", "")
            or getattr(item, "title", "")
        )
        title = re.sub(r"\s+", " ", str(title or "")).strip()[:120]
        normalized = title.casefold()
        if not title or normalized in seen:
            continue
        seen.add(normalized)
        anchors.append(title)
        if len(anchors) >= limit:
            break
    return anchors


def _counter_terms(values: object, limit: int) -> list[str]:
    """Return the strongest non-empty derived terms from a Counter-like value."""
    if not hasattr(values, "most_common"):
        return []
    return [
        re.sub(r"\s+", " ", str(term)).strip()[:80]
        for term, _weight in values.most_common(limit)
        if str(term).strip()
    ][:limit]


def _taste_summary(profile: dict, stats: dict) -> str:
    """Build the bounded, privacy-preserving profile shared with Gemini."""
    parts = []
    likes = _profile_title_anchors(profile.get("liked_tmdb"), 4)
    if likes:
        parts.append("Explicit likes: " + ", ".join(likes))
    anchors = _profile_title_anchors(profile.get("seeds"), 6)
    if anchors:
        parts.append("Strong viewing signals: " + ", ".join(anchors))
    dislikes = _profile_title_anchors(profile.get("disliked_tmdb"), 4)
    if dislikes:
        parts.append("Explicit dislikes (avoid close matches): " + ", ".join(dislikes))
    genres = _counter_terms(profile.get("seed_genres"), 5) or [g for g, _ in (stats.get("top_genres") or [])[:5]]
    if genres:
        parts.append("Strong genres: " + ", ".join(genres))
    keywords = _counter_terms(profile.get("seed_keywords"), 6)
    if keywords:
        parts.append("Preferred themes: " + ", ".join(keywords))
    avoided_genres = _counter_terms(profile.get("negative_genres"), 4)
    if avoided_genres:
        parts.append("Genres to avoid unless requested: " + ", ".join(avoided_genres))
    director = stats.get("top_director")
    if isinstance(director, (list, tuple)):  # stats stores ("Name", count)
        director = director[0] if director else None
    if director:
        parts.append("Favourite director: " + str(director))
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
    if _is_taste_match_question(query):
        lines += [
            "",
            "This is a taste-match question, not just a search. Return `assessment` for the named title:",
            "use its exact candidate id, choose likely/maybe/unlikely, and give one concrete reason (max 18 words)",
            "grounded only in the candidate fields and User taste. Do not assess a title that is absent from Candidates.",
            "Put the assessed title first in picks, then add related library choices when available.",
        ]
    lines += [
        "",
        "Candidates (JSON):",
        json.dumps(prompt_items, ensure_ascii=False),
        "",
        f"Choose up to {limit} picks. Also set 'message' to one friendly sentence about the set.",
    ]
    return "\n".join(lines)


def _validated_assessment(raw: object, payloads: dict) -> dict | None:
    """Keep a direct verdict grounded to a candidate returned this run."""
    if not isinstance(raw, dict):
        return None
    identifier = str(raw.get("id") or "")
    payload = payloads.get(identifier)
    verdict = str(raw.get("verdict") or "")
    reason = re.sub(r"\s+", " ", str(raw.get("reason") or "")).strip()[:180]
    if not payload or verdict not in {"likely", "maybe", "unlikely"} or not reason:
        return None
    return {"title": str(payload.get("title") or "Title")[:140], "verdict": verdict, "reason": reason}


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
    """Assemble a diverse pool of movie and series candidates."""
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
        payloads = _video_payloads(payloads)
        payloads = _dedup_payloads(payloads, exclude_keys or set(), exclude_item_ids)
        payloads = _exclude_tmdb_payloads(payloads, excluded_tmdb or set())
        return _fallback_items(payloads, limit)
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


async def _cached_ai_recommendations(
    user_id: int, *, profile: dict, history: list, cw_map: dict, dismissed: set,
) -> dict | None:
    """Return a revalidated successful agent cache entry, if it is useful.

    A deterministic library fallback is intentionally not reused.  It exists
    to make one failed agent request graceful, but keeping it for the cache
    TTL makes the next panel open noticeably weaker than an explicit Refresh.
    Older fallback cache documents are likewise ignored until they expire.
    """
    entry = await ai_rec_store.get_cached_entry(user_id)
    if not entry:
        return None
    if str(entry.get("origin") or "") != "agent":
        return None
    cached = entry.get("items")
    if not isinstance(cached, list):
        return None
    seen_keys = {str(entry.get("cw_key") or "") for entry in history} | set(cw_map)
    valid = _validate_cached(
        cached,
        exclude_keys=seen_keys,
        exclude_item_ids=_watched_card_ids(seen_keys),
        excluded_tmdb=set(profile.get("exclude_tmdb") or set()) | set(dismissed or set()),
    )
    if len(valid) < 3:  # Too stale to present as a complete shelf.
        return None
    return {
        "items": valid,
        "externalItems": await _requestable_picks(user_id, profile, dismissed, ""),
        "message": "", "coldStart": False, "cached": True,
        "recommendationMeta": _recommendation_meta(
            str(entry.get("origin") or "library"), cached=True,
            generated_at=int(entry.get("cachedAt") or 0),
        ),
    }


async def get_cached_ai_recommendations(user_id: int) -> dict | None:
    """Fast cache check for a panel open, with the usual safety revalidation."""
    profile, history, cw_map, dismissed = await asyncio.gather(
        rec_engine._collect_signal_profile(user_id), wh_store.get_recent(user_id, limit=_AI_REC_HISTORY_LIMIT),
        cw_store.get_all(user_id), dismissed_store.get_dismissed_ids(user_id),
    )
    return await _cached_ai_recommendations(
        user_id, profile=profile, history=history, cw_map=cw_map, dismissed=dismissed,
    )


def _clean_agent_args(name: str, raw: object) -> dict:
    """Validate model function arguments before they reach catalogue code."""
    raw = raw if isinstance(raw, dict) else {}
    text = lambda key, maximum: re.sub(r"\s+", " ", str(raw.get(key) or "").strip())[:maximum]
    kind = text("kind", 12).lower()
    if kind not in {"movies", "series"}:
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

    def __init__(self, *, profile: dict, seen_keys: set[str], watched_ids: set[str], excluded_tmdb: set[tuple[int, str]]):
        self.profile = profile
        self.seen_keys = seen_keys
        self.watched_ids = watched_ids
        self.excluded_tmdb = excluded_tmdb
        self._art_cache: dict = {}
        self._by_href: dict[str, str] = {}
        self.payloads: dict[str, dict] = {}
        self.scores: dict[str, float] = {}
        self.source_counts: Counter = Counter()
        self._last_filter_counts: dict[str, int] = {}

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

    def payloads_by_href(self):
        return [(str(payload.get("href") or ""), payload) for payload in self.payloads.values()]

    def _register(self, ranked_cards: list[tuple[object, float]]) -> list[dict]:
        from main.server import spa_routes as _spa
        card_payloads = [(_spa._card(card, art_cache=self._art_cache), score) for card, score in ranked_cards]
        payloads = [payload for payload, _score in card_payloads]
        video_payloads = _video_payloads(payloads)
        unseen_payloads = _dedup_payloads(video_payloads, self.seen_keys, self.watched_ids)
        included_payloads = _exclude_tmdb_payloads(unseen_payloads, self.excluded_tmdb)
        # A second cache-style validation covers hidden/deleted single uploads
        # and makes all three tools subject to identical eligibility rules.
        payloads = _validate_cached(
            included_payloads, exclude_keys=self.seen_keys, exclude_item_ids=self.watched_ids,
            excluded_tmdb=self.excluded_tmdb,
        )
        self._last_filter_counts = {
            "ranked": len(card_payloads),
            "video": len(video_payloads),
            "unwatched": len(unseen_payloads),
            "included": len(included_payloads),
            "valid": len(payloads),
        }
        score_by_href = {str(payload.get("href") or ""): score for payload, score in card_payloads}
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
            self.scores[href] = max(self.scores.get(href, float("-inf")), score_by_href.get(href, 0.0))
            result.append(self._compact(identifier, self.payloads[identifier]))
        return result

    def run(self, name: str, raw_args: object) -> list[dict]:
        args = _clean_agent_args(name, raw_args)
        if name == "get_title_details":
            return [self._compact(identifier, self.payloads[identifier]) for identifier in args["ids"] if identifier in self.payloads]
        if name not in {"search_library", "browse_library"}:
            return []
        view = {"movies": "movies", "series": "series"}.get(args["kind"], "")
        if name == "search_library":
            cards, _ = media_index.query_grouped(
                q=args["query"], year=args["year"], genre=args["genre"],
                view=view, sort="newest" if args["sort"] == "relevance" else args["sort"],
                limit=_AGENT_TOOL_RESULT_LIMIT * 3,
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
        # Rank the full bounded reserve, then apply watched/hidden filtering in
        # _register.  Ranking only twelve raw cards first can leave the model
        # with one or two candidates after those safety filters run.
        ranked = rec_engine.rank_catalogue_cards(
            list(cards), self.profile, query=args.get("query") or "", limit=len(cards),
        )
        result = self._register(ranked)
        self.source_counts[name] += len(result)
        for stage, count in self._last_filter_counts.items():
            self.source_counts[f"{name}:{stage}"] += count
        return result

    def recover_with_broad_browse(self) -> list[dict]:
        """Seed a safe local reserve when model-selected searches are all empty.

        Tool calling is probabilistic: a model can make three perfectly valid
        but overly specific searches.  A bounded broad browse keeps the final
        curation grounded in the user's *unwatched* library without another
        model round trip.
        """
        cards, _ = media_index.query_grouped(
            sort="newest", limit=_MAX_CANDIDATES * 3,
        )
        ranked = rec_engine.rank_catalogue_cards(
            list(cards), self.profile, limit=len(cards),
        )
        result = self._register(ranked)
        self.source_counts["recovery_browse"] += len(result)
        for stage, count in self._last_filter_counts.items():
            self.source_counts[f"recovery_browse:{stage}"] += count
        return result


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
        rec_engine._collect_signal_profile(user_id), wh_store.get_recent(user_id, limit=_AI_REC_HISTORY_LIMIT),
        cw_store.get_all(user_id), dismissed_store.get_dismissed_ids(user_id),
    )
    stats = await _safe_stats(user_id)
    seen_keys = {str(entry.get("cw_key") or "") for entry in history} | set(cw_map)
    watched_ids = _watched_card_ids(seen_keys)
    excluded = set(profile.get("exclude_tmdb") or set()) | set(dismissed or set())
    catalogue = _AgentCatalogue(profile=profile, seen_keys=seen_keys, watched_ids=watched_ids, excluded_tmdb=excluded)
    intent = query or "Refresh the user's library picks with a useful, varied set."
    contents = [{"role": "user", "parts": [{"text": "\n".join([
        "You curate a private playable media library.",
        "First call search_library or browse_library to find candidates before recommending anything.",
        "For a question about whether the user will enjoy a named title, find that title first, then explore related library titles for comparison.",
        "Never ask for or reveal private catalogue data beyond tool results. Do not use unavailable titles.",
        "You have at most three total tool calls; explore efficiently.",
        f"User taste summary: {_taste_summary(profile, stats)}",
        f"User request: {intent}",
    ])}]}]
    await _emit_agent_status(progress, "Searching your library")
    calls_used = 0

    def failed(reason: str) -> AgentRunError:
        return AgentRunError(
            reason,
            tool_count=calls_used,
            candidate_count=len(catalogue.payloads),
            elapsed_ms=round((time.monotonic() - started) * 1000),
            source_counts=dict(catalogue.source_counts),
        )

    explored = False
    while calls_used < _AGENT_MAX_TOOL_CALLS:
        remaining = _AGENT_BUDGET_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            raise failed("budget")
        response = await gemini.generate_content(
            contents,
            tools=_AGENT_TOOLS,
            tool_config=_AGENT_INITIAL_TOOL_CONFIG if calls_used == 0 else None,
            timeout=remaining,
        )
        model_content, calls = _function_calls(response)
        if response is None or model_content is None:
            raise failed("model")
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
        catalogue.recover_with_broad_browse()
    if not catalogue.payloads:
        raise failed("no_candidates")
    await _emit_agent_status(progress, "Curating picks")
    remaining = _AGENT_BUDGET_SECONDS - (time.monotonic() - started)
    if remaining <= 0:
        raise failed("budget")
    candidates = [catalogue._compact(identifier, payload) for identifier, payload in catalogue.payloads.items()]
    prompt = _build_prompt(_taste_summary(profile, stats), candidates, query, limit)
    result = await gemini.generate_json(
        prompt,
        schema=_DECISION_PICK_SCHEMA if _is_taste_match_question(query) else _PICK_SCHEMA,
        timeout=remaining,
    )
    picks = result.get("picks") if isinstance(result, dict) else None
    items = _apply_picks(picks, catalogue.payloads, limit)
    # Revalidate after model work in case a deletion/hide/finish raced a tool.
    assessment_raw = result.get("assessment") if isinstance(result, dict) else None
    pinned_id = str(assessment_raw.get("id") or "") if isinstance(assessment_raw, dict) else ""
    model_pick_count = len(items)
    rerank_metrics: dict = {"diversity_relaxations": 0}
    items = _rerank_agent_picks(items, catalogue, profile, limit, pinned_id=pinned_id, metrics=rerank_metrics)
    items = _validate_cached(
        items, exclude_keys=seen_keys, exclude_item_ids=watched_ids, excluded_tmdb=excluded,
    )
    if not items:
        raise failed("invalid_picks")
    if refresh:
        await rec_store.clear_cached(user_id)
    if refresh or not query:
        await ai_rec_store.set_cached(user_id, items, origin="agent")
    logging.info(
        "ai_rec_agent tool_count=%d elapsed_ms=%d candidate_count=%d scored_candidate_count=%d "
        "model_pick_count=%d final_pick_count=%d comfort_count=%d discovery_count=%d "
        "source_counts=%s diversity_relaxations=%d fallback_reason=%s",
        calls_used, round((time.monotonic() - started) * 1000), len(catalogue.payloads), len(getattr(catalogue, "scores", {})),
        model_pick_count, len(items), sum(item.get("bucket") != "discovery" for item in items),
        sum(item.get("bucket") == "discovery" for item in items), dict(getattr(catalogue, "source_counts", {})),
        rerank_metrics["diversity_relaxations"], "",
    )
    return _with_recommendation_meta({
        "items": items,
        "externalItems": await _requestable_picks(user_id, profile, dismissed, query),
        "message": str(result.get("message") or "").strip()[:240] if isinstance(result, dict) else "",
        "assessment": _validated_assessment(result.get("assessment"), catalogue.payloads) if _is_taste_match_question(query) and isinstance(result, dict) else None,
        "coldStart": False,
    }, "agent")


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
                logging.info(
                    "ai_rec_agent tool_count=%d elapsed_ms=%d candidate_count=%d final_pick_count=%d "
                    "source_counts=%s fallback_reason=%s",
                    getattr(exc, "tool_count", 0), getattr(exc, "elapsed_ms", 0),
                    getattr(exc, "candidate_count", 0), 0, getattr(exc, "source_counts", {}),
                    str(exc) or type(exc).__name__,
                )
                await _emit_agent_status(progress, "Curating picks")
                return _with_recommendation_meta(
                    await _generate(
                        user_id, query=query, limit=limit, refresh=refresh,
                        rank_with_gemini=False, cache_result=False,
                    ),
                    "library", fallback=True,
                )
        return _with_recommendation_meta(
            await _generate(user_id, query=query, limit=limit, refresh=refresh), "library",
        )
    except Exception:
        logging.exception("ai_rec: generation failed, serving trending fallback")
        # An optional ranking dependency must not turn into an endless client
        # spinner or re-suggest a watched grouped title. Make one bounded
        # best-effort pass over local activity before serving the fallback.
        try:
            history, cw_map = await asyncio.gather(
                wh_store.get_recent(user_id, limit=_AI_REC_HISTORY_LIMIT),
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
        return _with_recommendation_meta({
            "items": items,
            "externalItems": [],
            "message": "We couldn't tailor picks just now; showing fresh titles instead. Try Refresh.",
            "coldStart": False,
        }, "fresh", fallback=True)


async def _generate(
    user_id: int, *, query: Optional[str], limit: int, refresh: bool,
    rank_with_gemini: bool = True, cache_result: bool = True,
) -> dict:
    query = (query or "").strip()
    read_cache = not query and not refresh
    # A fallback may be shown for this request, but it must never pin weaker
    # deterministic results over the next cache-first panel open.
    write_cache = not query and cache_result  # refresh recomputes AND refreshes the stored cache

    from main.server import spa_routes as _spa  # lazy: card builders

    profile, history, cw_map, dismissed = await asyncio.gather(
        rec_engine._collect_signal_profile(user_id),
        wh_store.get_recent(user_id, limit=_AI_REC_HISTORY_LIMIT),
        cw_store.get_all(user_id),
        dismissed_store.get_dismissed_ids(user_id),
    )
    if read_cache:
        cached = await _cached_ai_recommendations(
            user_id, profile=profile, history=history, cw_map=cw_map, dismissed=dismissed,
        )
        if cached:
            return cached
    stats = await _safe_stats(user_id)

    # A deliberate refresh should regenerate its TMDB-derived candidate pool,
    # not ask Gemini to reshuffle the same 24-hour local recommendation cache.
    if refresh:
        await rec_store.clear_cached(user_id)

    async def _finish(result: dict) -> dict:
        if write_cache and result.get("items"):
            await ai_rec_store.set_cached(user_id, result["items"], origin="library")
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
    payloads = _video_payloads(payloads)
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
        return _fallback_items(payloads, limit)

    index, prompt_items = _index_candidates(payloads)
    prompt = _build_prompt(_taste_summary(profile, stats), prompt_items, query, limit)
    result = await gemini.generate_json(prompt, schema=_PICK_SCHEMA, timeout=45) if rank_with_gemini else None

    picks = result.get("picks") if isinstance(result, dict) else None
    if not isinstance(picks, list) or not picks:
        return await _finish({"items": _raw_fallback(), "externalItems": await _requestable_picks(user_id, profile, dismissed, query), "message": "", "coldStart": False})

    items = _balanced_buckets(_apply_picks(picks, index, limit) or _raw_fallback())
    message = (result.get("message") or "").strip()
    return await _finish({"items": items, "externalItems": await _requestable_picks(user_id, profile, dismissed, query), "message": message, "coldStart": False})
