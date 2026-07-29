"""Durable, TMDB-keyed title requests.

Each document is one user's interest in one canonical movie or TV show.  This
keeps duplicate requests idempotent while allowing the admin queue to group
real demand by title.  TV requests carry explicit season numbers, so a show
can progress from planned to partially available without overpromising future
seasons.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable


_OPEN_STATES = {"pending", "planned", "partial"}
_ALL_STATES = _OPEN_STATES | {"available", "declined", "cancelled"}
_MAX_OPEN_PER_USER = 5
_indexed = False


def _get_db():
    try:
        from main.utils import media_index as _mi
        store = _mi._store
        if store is None or not hasattr(store, "_client"):
            return None
        return store._client[store._db_name]
    except Exception:
        return None


def is_available() -> bool:
    return _get_db() is not None


async def _ensure_indexes() -> None:
    global _indexed
    if _indexed:
        return
    db = _get_db()
    if db is None:
        return
    try:
        coll = db["media_requests"]
        await coll.create_index([("user_id", 1), ("kind", 1), ("tmdb_id", 1)], unique=True)
        await coll.create_index([("status", 1), ("updated_at", -1)])
        await coll.create_index([("kind", 1), ("tmdb_id", 1), ("status", 1)])
        await coll.create_index([("user_id", 1), ("created_at", -1)])
        _indexed = True
    except Exception:
        logging.exception("request_store: ensure_indexes failed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_seasons(values: Iterable[object] | None) -> list[int]:
    out: set[int] = set()
    for value in values or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 200:
            out.add(number)
    return sorted(out)


def _safe(doc: dict) -> dict:
    return {
        "id": str(doc.get("request_id") or ""),
        "tmdbId": int(doc.get("tmdb_id") or 0),
        "kind": str(doc.get("kind") or ""),
        "title": str(doc.get("title") or ""),
        "year": doc.get("year"),
        "overview": str(doc.get("overview") or ""),
        "posterPath": str(doc.get("poster_path") or ""),
        "requestedSeasons": _as_seasons(doc.get("requested_seasons")),
        "availableSeasons": _as_seasons(doc.get("available_seasons")),
        "status": str(doc.get("status") or "pending"),
        "note": str(doc.get("note") or ""),
        "createdAt": doc.get("created_at").isoformat() if doc.get("created_at") else "",
        "updatedAt": doc.get("updated_at").isoformat() if doc.get("updated_at") else "",
    }


def library_availability(tmdb_id: int, kind: str) -> dict:
    """Return whether the enriched local catalogue covers this provider title."""
    try:
        from main.utils import media_index
        matches = [
            item for item in media_index._items.values()
            if not getattr(item, "hidden", False)
            and int(getattr(item, "tmdb_id", 0) or 0) == int(tmdb_id)
            and getattr(item, "tmdb_kind", "") == kind
        ]
    except Exception:
        matches = []
    seasons = sorted({
        int(getattr(item, "season", 1) or 1)
        for item in matches
        if kind == "tv" and int(getattr(item, "season", 1) or 1) >= 1
    })
    return {"inLibrary": bool(matches) if kind == "movie" else bool(seasons), "availableSeasons": seasons}


async def create(user_id: int, title: dict, requested_seasons: Iterable[object] | None) -> tuple[dict | None, str]:
    """Create one idempotent request. Returns ``(request, outcome)``."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None, "unavailable"
    kind = str(title.get("kind") or "")
    try:
        tmdb_id = int(title.get("tmdbId") or 0)
    except (TypeError, ValueError):
        tmdb_id = 0
    if kind not in {"movie", "tv"} or not tmdb_id:
        return None, "invalid"
    seasons = _as_seasons(requested_seasons) if kind == "tv" else []
    if kind == "tv" and not seasons:
        return None, "seasons_required"
    coll = db["media_requests"]
    try:
        existing = await coll.find_one({"user_id": user_id, "kind": kind, "tmdb_id": tmdb_id})
        if existing:
            if str(existing.get("status") or "") in {"cancelled", "declined"}:
                open_count = await coll.count_documents({"user_id": user_id, "status": {"$in": list(_OPEN_STATES)}})
                if open_count >= _MAX_OPEN_PER_USER:
                    return None, "limit"
                now = _now()
                await coll.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {
                        "requested_seasons": seasons, "available_seasons": [], "status": "pending",
                        "note": "", "updated_at": now,
                    }},
                )
                existing.update({
                    "requested_seasons": seasons, "available_seasons": [], "status": "pending",
                    "note": "", "updated_at": now,
                })
                return _safe(existing), "created"
            return _safe(existing), "duplicate"
        open_count = await coll.count_documents({"user_id": user_id, "status": {"$in": list(_OPEN_STATES)}})
        if open_count >= _MAX_OPEN_PER_USER:
            return None, "limit"
        now = _now()
        doc = {
            "request_id": uuid.uuid4().hex,
            "user_id": int(user_id),
            "tmdb_id": tmdb_id,
            "kind": kind,
            "title": str(title.get("title") or "")[:200],
            "year": title.get("year"),
            "overview": str(title.get("overview") or "")[:1000],
            "poster_path": str(title.get("posterPath") or "")[:300],
            "requested_seasons": seasons,
            "available_seasons": [],
            "status": "pending",
            "note": "",
            "created_at": now,
            "updated_at": now,
        }
        await coll.insert_one(doc)
        return _safe(doc), "created"
    except Exception:
        logging.exception("request_store: create failed uid=%d", user_id)
        return None, "error"


async def list_for_user(user_id: int) -> list[dict]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        docs = await db["media_requests"].find({"user_id": user_id}, sort=[("updated_at", -1)]).to_list(length=100)
        return [_safe(doc) for doc in docs]
    except Exception:
        logging.exception("request_store: list_for_user failed uid=%d", user_id)
        return []


async def requested_keys(user_id: int) -> set[tuple[int, str]]:
    """Keys already requested by a user, used to avoid repetitive AI prompts."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return set()
    try:
        docs = await db["media_requests"].find(
            {"user_id": user_id, "status": {"$in": list(_OPEN_STATES | {"available"})}},
            projection={"tmdb_id": 1, "kind": 1},
        ).to_list(length=1000)
        return {(int(doc.get("tmdb_id") or 0), str(doc.get("kind") or "")) for doc in docs}
    except Exception:
        logging.exception("request_store: requested_keys failed uid=%d", user_id)
        return set()


async def cancel(user_id: int, request_id: str) -> bool:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["media_requests"].update_one(
            {"user_id": user_id, "request_id": request_id, "status": {"$in": list(_OPEN_STATES)}},
            {"$set": {"status": "cancelled", "updated_at": _now()}},
        )
        return bool(result.modified_count)
    except Exception:
        logging.exception("request_store: cancel failed uid=%d id=%s", user_id, request_id)
        return False


async def list_admin(status: str = "") -> list[dict]:
    """Aggregate demand into one operator-facing row per TMDB title."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    query = {"status": status} if status in _ALL_STATES else {}
    try:
        docs = await db["media_requests"].find(query, sort=[("updated_at", -1)]).to_list(length=1000)
    except Exception:
        logging.exception("request_store: list_admin failed")
        return []
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for doc in docs:
        grouped[(str(doc.get("kind") or ""), int(doc.get("tmdb_id") or 0))].append(doc)
    rows: list[dict] = []
    for (_, _), group in grouped.items():
        newest = max(group, key=lambda row: row.get("updated_at") or _now())
        states = {str(row.get("status") or "pending") for row in group}
        requested = sorted({season for row in group for season in _as_seasons(row.get("requested_seasons"))})
        available = library_availability(int(newest.get("tmdb_id") or 0), str(newest.get("kind") or ""))
        rows.append({
            **_safe(newest),
            "requestCount": len(group),
            "requestedSeasons": requested,
            "states": sorted(states),
            "inLibrary": available["inLibrary"],
            "availableSeasons": available["availableSeasons"],
        })
    # Stable sorts keep the queue easy to scan: actionable first, then demand,
    # then the most recently touched title.
    rows.sort(key=lambda row: row["updatedAt"], reverse=True)
    rows.sort(key=lambda row: row["requestCount"], reverse=True)
    rows.sort(key=lambda row: row["status"] not in _OPEN_STATES)
    return rows


async def set_title_state(tmdb_id: int, kind: str, state: str, note: str = "") -> int:
    if state not in {"planned", "declined", "available"}:
        return 0
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    try:
        result = await db["media_requests"].update_many(
            {"tmdb_id": int(tmdb_id), "kind": kind, "status": {"$in": list(_OPEN_STATES)}},
            {"$set": {"status": state, "note": str(note or "")[:500], "updated_at": _now()}},
        )
        return int(result.modified_count)
    except Exception:
        logging.exception("request_store: set_title_state failed %s:%s", kind, tmdb_id)
        return 0


async def reconcile_item(item) -> int:
    """Update only requests matching one freshly enriched catalogue item."""
    try:
        tmdb_id = int(getattr(item, "tmdb_id", 0) or 0)
        kind = str(getattr(item, "tmdb_kind", "") or "")
    except (TypeError, ValueError):
        return 0
    if not tmdb_id or kind not in {"movie", "tv"}:
        return 0
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    availability = library_availability(tmdb_id, kind)
    if not availability["inLibrary"]:
        return 0
    try:
        docs = await db["media_requests"].find({
            "tmdb_id": tmdb_id, "kind": kind, "status": {"$in": list(_OPEN_STATES)},
        }).to_list(length=1000)
        changed = 0
        for doc in docs:
            wanted = _as_seasons(doc.get("requested_seasons"))
            have = availability["availableSeasons"]
            if kind == "movie" or set(wanted).issubset(have):
                state = "available"
            elif set(wanted).intersection(have):
                state = "partial"
            else:
                continue
            result = await db["media_requests"].update_one(
                {"_id": doc["_id"], "status": {"$in": list(_OPEN_STATES)}},
                {"$set": {"status": state, "available_seasons": have, "updated_at": _now()}},
            )
            changed += int(result.modified_count)
        return changed
    except Exception:
        logging.exception("request_store: reconcile_item failed %s:%s", kind, tmdb_id)
        return 0
