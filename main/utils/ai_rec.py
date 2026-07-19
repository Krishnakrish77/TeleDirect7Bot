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
from typing import Optional

from main.utils import ai_rec_store, cw_store, gemini, media_index, rec_engine, wh_store

_MAX_CANDIDATES = 50

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


# ---- pure helpers (unit-tested in test_ai_rec.py) -------------------------

def _dedup_payloads(payloads: list, exclude_keys: set) -> list:
    """Drop duplicate cards (by href) and anything the user already engaged."""
    seen: set = set()
    out = []
    for p in payloads:
        href = p.get("href")
        if not href or href in seen:
            continue
        if p.get("watchKey") and p.get("watchKey") in exclude_keys:
            continue
        seen.add(href)
        out.append(p)
    return out


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
            "type": p.get("eyebrow") or ("Music" if p.get("aspect") == "square" else "Video"),
            "year": p.get("year"),
            "by": p.get("artist") or p.get("subtitle") or "",
            "genres": (p.get("genres") or [])[:4],
        })
    return index, prompt_items


def _apply_picks(picks: list, index: dict, limit: int) -> list:
    """Ground Gemini's picks: keep only real candidate ids, dedup, attach the
    reason + bucket, cap to ``limit``."""
    out = []
    seen: set = set()
    for pick in picks or []:
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


def _is_cold(profile: dict, stats: dict, payloads: list) -> bool:
    has_signal = bool(profile.get("seeds")) or bool(stats.get("top_genres")) or bool(stats.get("top_artists"))
    return (not has_signal) or len(payloads) < 6


def _taste_summary(profile: dict, stats: dict) -> str:
    parts = []
    genres = [g for g, _ in (stats.get("top_genres") or [])][:5]
    if genres:
        parts.append("Top genres: " + ", ".join(genres))
    if stats.get("top_director"):
        parts.append("Favourite director: " + str(stats["top_director"]))
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
        "For each pick write ONE short, specific reason (max ~14 words) that references the",
        "user's actual taste — not generic filler.",
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


async def _gather_candidates(user_id: int, profile: dict, stats: dict) -> list:
    """Assemble a diverse pool of catalogue objects: TMDB-based recs (comfort),
    fresh titles in top genres (discovery), top-artist tracks + fresh music, and
    globally popular items."""
    objs: list = []

    try:
        recs = await rec_engine.get_recommendations(user_id, profile=profile)
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


async def _trending_items(limit: int) -> list:
    from main.server import spa_routes as _spa
    try:
        items, _ = media_index.query_grouped(sort="newest", limit=limit)
        return [
            {**_spa._card(o), "recReason": "Fresh in your library", "bucket": "comfort"}
            for o in items
        ]
    except Exception:
        logging.debug("ai_rec: trending fallback failed", exc_info=True)
        return []


async def get_ai_recommendations(
    user_id: int,
    *,
    query: Optional[str] = None,
    limit: int = 12,
    refresh: bool = False,
) -> dict:
    """Return ``{items, message, coldStart}`` — catalogue-grounded AI picks."""
    query = (query or "").strip()
    use_cache = not query and not refresh

    if use_cache:
        cached = await ai_rec_store.get_cached(user_id)
        if cached:
            return {"items": cached, "message": "", "coldStart": False, "cached": True}

    from main.server import spa_routes as _spa  # lazy: card builders

    profile, history, cw_map = await asyncio.gather(
        rec_engine._collect_signal_profile(user_id),
        wh_store.get_recent(user_id, limit=80),
        cw_store.get_all(user_id),
    )
    stats = await _safe_stats(user_id)
    seen_keys = {e.get("cw_key") for e in history} | set(cw_map.keys())

    objs = await _gather_candidates(user_id, profile, stats)
    payloads = _dedup_payloads([_spa._card(o) for o in objs], seen_keys)
    random.shuffle(payloads)  # reduce the LLM's position bias
    payloads = payloads[:_MAX_CANDIDATES]

    if _is_cold(profile, stats, payloads) or not gemini.available():
        return {"items": await _trending_items(limit), "message": "", "coldStart": True}

    index, prompt_items = _index_candidates(payloads)
    prompt = _build_prompt(_taste_summary(profile, stats), prompt_items, query, limit)
    result = await gemini.generate_json(prompt, schema=_PICK_SCHEMA, timeout=45)

    if not result or not result.get("picks"):
        fallback = [{**p, "recReason": "From your library", "bucket": "comfort"} for p in payloads[:limit]]
        return {"items": fallback, "message": "", "coldStart": False}

    items = _apply_picks(result["picks"], index, limit)
    if not items:
        items = [{**p, "recReason": "From your library", "bucket": "comfort"} for p in payloads[:limit]]
    message = (result.get("message") or "").strip()

    if use_cache and items:
        await ai_rec_store.set_cached(user_id, items)
    return {"items": items, "message": message, "coldStart": False}
