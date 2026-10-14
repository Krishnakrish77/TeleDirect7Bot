"""Scrobbling — submit played tracks to ListenBrainz.

Design:
- ``scrobble_settings`` Mongo collection: {user_id, provider, token, filter,
  updated_at}. The token is a secret: it is validated at save time via
  ListenBrainz ``/1/validate-token`` and NEVER logged (repo rule).
- Plays enter ``enqueue`` from the watch-history completion path; the queue
  is a bounded in-process asyncio.Queue drained by one background task that
  batches up to 10 listens per ``POST /1/submit-listens`` call.
- The scrobble filter reuses the shared rule_filter engine (same grammar as
  smart playlists) so e.g. ``{"all":[{"is":{"genre":"Christmas"}}]}`` keeps
  holiday music off the user's profile while local play counts stay intact.
- Retries: 3 attempts with exponential backoff, then the batch is dropped
  with a log line. Failures never propagate to the play-recording path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import aiohttp

from main.utils import media_index, rule_filter

_LISTENBRAINZ_API = "https://api.listenbrainz.org"
_VALIDATE_TIMEOUT = 10
_SUBMIT_TIMEOUT = 15
_BATCH_SIZE = 10
_MAX_QUEUE = 100
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF = 2.0  # seconds; doubles per attempt

_queue: Optional[asyncio.Queue] = None
_worker_task: Optional[asyncio.Task] = None
_settings_cache: dict[int, tuple[float, Optional[dict]]] = {}
_SETTINGS_TTL = 30.0


class ScrobbleDisabled(Exception):
    """Raised internally when the user has no usable scrobble config."""


# ── settings ──────────────────────────────────────────────────────────────────

def _get_db():
    try:
        from main.utils import media_index as _mi
        s = _mi._store
        if s is None or not hasattr(s, "_client"):
            return None
        return s._client[s._db_name]
    except Exception:
        return None


async def get_settings(user_id: int) -> Optional[dict]:
    """Cached scrobble settings for a user; None when unset/unavailable.

    The stored token is never included in API responses — callers get
    ``hasToken`` instead (see settings_payload).
    """
    cached = _settings_cache.get(user_id)
    now = time.monotonic()
    if cached and now - cached[0] < _SETTINGS_TTL:
        return cached[1]
    db = _get_db()
    if db is None:
        return None
    try:
        doc = await db["scrobble_settings"].find_one(
            {"user_id": user_id}, projection={"_id": 0},
        )
    except Exception:
        logging.exception("scrobbler: settings load failed uid=%d", user_id)
        return None
    _settings_cache[user_id] = (now, doc)
    return doc


def settings_payload(doc: Optional[dict]) -> dict:
    """Client-safe view of settings — the token never leaves the server."""
    if not doc:
        return {"provider": "", "enabled": False, "hasToken": False, "filter": None}
    return {
        "provider": doc.get("provider") or "",
        "enabled": bool(doc.get("token")),
        "hasToken": bool(doc.get("token")),
        "filter": doc.get("filter"),
    }


async def save_settings(
    user_id: int, *, provider: str, token: str, filter_rules: Any,
) -> tuple[bool, str]:
    """Validate and persist settings. Returns (ok, error-message-or-'')."""
    if provider != "listenbrainz":
        return False, "Unsupported provider"
    token = (token or "").strip()
    filter_rules = filter_rules or None
    if filter_rules:
        try:
            rule_filter.validate_rules(filter_rules, smart_playlists_rule_fields())
        except rule_filter.RuleValidationError as exc:
            return False, str(exc)

    if token:
        valid, message = await validate_listenbrainz_token(token)
        if not valid:
            return False, message

    db = _get_db()
    if db is None:
        return False, "Storage unavailable"
    try:
        await db["scrobble_settings"].update_one(
            {"user_id": user_id},
            {"$set": {
                "provider": provider,
                "token": token,
                "filter": filter_rules,
            }},
            upsert=True,
        )
    except Exception:
        logging.exception("scrobbler: settings save failed uid=%d", user_id)
        return False, "Storage unavailable"
    _settings_cache.pop(user_id, None)
    logging.info("scrobbler: settings saved uid=%d provider=%s filter=%s",
                 user_id, provider, bool(filter_rules))
    return True, ""


async def clear_settings(user_id: int) -> bool:
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["scrobble_settings"].delete_one({"user_id": user_id})
    except Exception:
        logging.exception("scrobbler: settings clear failed uid=%d", user_id)
        return False
    _settings_cache.pop(user_id, None)
    return result.deleted_count > 0


def smart_playlists_rule_fields() -> set[str]:
    """Rule fields allowed in a scrobble filter — the smart-playlist set."""
    # Imported lazily to avoid a circular import at module load.
    from main.utils.smart_playlists import RULE_FIELDS
    return RULE_FIELDS


async def validate_listenbrainz_token(token: str) -> tuple[bool, str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_LISTENBRAINZ_API}/1/validate-token",
                params={"token": token},
                timeout=aiohttp.ClientTimeout(total=_VALIDATE_TIMEOUT),
            ) as response:
                if response.status == 200:
                    payload = await response.json(content_type=None)
                    if payload.get("valid"):
                        return True, ""
                    return False, "Token is not valid"
                if response.status == 400:
                    return False, "Token is malformed"
                return False, f"ListenBrainz returned {response.status}"
    except Exception:
        logging.exception("scrobbler: token validation request failed")
        return False, "Could not reach ListenBrainz"


# ── enqueue + worker ──────────────────────────────────────────────────────────

def _queue_instance() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=_MAX_QUEUE)
    return _queue


def _start_worker() -> None:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())


def enqueue(user_id: int, message_id: int, *, played_at: Optional[float] = None) -> None:
    """Fire-and-forget scrobble submission for one completed audio play.

    Never raises and never blocks the caller: when the queue is full (an
    outage backlogging submissions) the newest play is dropped with a log
    line instead of stalling the play-recording path.
    """
    try:
        _queue_instance().put_nowait((user_id, int(message_id), played_at or time.time()))
        _start_worker()
    except asyncio.QueueFull:
        logging.warning("scrobbler: queue full, dropping play uid=%d mid=%d", user_id, message_id)
    except Exception:
        logging.exception("scrobbler: enqueue failed uid=%d mid=%d", user_id, message_id)


def _track_meta(message_id: int) -> Optional[dict]:
    item = media_index.get_item(message_id)
    if item is None or (item.media_kind or "") != "audio":
        return None
    artist = (item.artist or "").strip()
    title = (item.title or item.file_name or "").strip()
    if not artist or not title:
        return None
    meta = {
        "artist": artist,
        "track": title,
        "message_id": message_id,
    }
    if item.album_title:
        meta["album"] = item.album_title
    return meta


def _filter_allows(rules: Any, meta: dict) -> bool:
    if not rules:
        return True

    def extract(field: str) -> Any:
        if field == "genre":
            genres = []
            for t in (media_index.get_item(meta["message_id"]).tags or []):
                g = media_index.normalize_genre(t)
                if g and g.lower() not in [x.lower() for x in genres]:
                    genres.append(g)
            return genres
        if field == "artist":
            return (meta.get("artist") or "").lower()
        if field == "album":
            return (meta.get("album") or "").lower()
        if field == "title":
            return (meta.get("track") or "").lower()
        return None

    # Filter semantics: rules matching the track KEEP it off the scrobble
    # feed — allow = not matches.
    return not rule_filter.matches(rules, extract)


async def _submit_batch(user_id: int, token: str, listens: list[dict]) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_LISTENBRAINZ_API}/1/submit-listens",
                headers={"Authorization": f"Token {token}"},
                json={"listen_type": "import", "payload": [
                    {
                        "listened_at": listen["listened_at"],
                        "track_metadata": {
                            "artist_name": listen["track_metadata"]["artist"],
                            "track_name": listen["track_metadata"]["track"],
                            **({"album_name": listen["track_metadata"]["album"]}
                               if listen["track_metadata"].get("album") else {}),
                        },
                    }
                    for listen in listens
                ]},
                timeout=aiohttp.ClientTimeout(total=_SUBMIT_TIMEOUT),
            ) as response:
                if response.status == 200:
                    return True
                detail = (await response.text())[:200]
                logging.warning(
                    "scrobbler: submit rejected uid=%d status=%d count=%d body=%s",
                    user_id, response.status, len(listens), detail,
                )
                return False
    except Exception:
        logging.exception("scrobbler: submit failed uid=%d count=%d", user_id, len(listens))
        return False


async def _worker() -> None:
    q = _queue_instance()
    while True:
        user_id, message_id, played_at = await q.get()
        batch: list[tuple[int, int, float]] = [(user_id, message_id, played_at)]
        foreign: list[tuple[int, int, float]] = []
        # Drain up to _BATCH_SIZE quickly so bursts cost one request —
        # but only plays from the SAME user, since one submit call carries
        # one auth token.
        while len(batch) < _BATCH_SIZE:
            try:
                uid, mid, ts = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if uid != user_id:
                # Foreign play: hold it aside. Requeueing inside this fill
                # loop would spin forever (the requeued item is immediately
                # visible to get_nowait again); pushing after submission
                # lets the next iteration batch it under its own user.
                foreign.append((uid, mid, ts))
                continue
            batch.append((uid, mid, ts))
        for entry in foreign:
            # Balance this dequeue NOW: the item leaves `batch`, so the
            # finally below never calls task_done for it. put_nowait
            # re-registers the task (+1) and task_done cancels the original
            # dequeue (-1) — net zero until the item's next iteration,
            # where it joins a real batch and is done normally. Without
            # this, join() waits forever on the phantom +1.
            q.task_done()
            q.put_nowait(entry)

        try:
            settings = await get_settings(user_id)
            token = (settings or {}).get("token") if settings else None
            rules = (settings or {}).get("filter") if settings else None
            if not token:
                # Settings cleared mid-flight: drop the plays quietly.
                continue

            listens = []
            for uid, mid, ts in batch:
                meta = _track_meta(mid)
                if meta is None:
                    continue
                if not _filter_allows(rules, meta):
                    continue
                listens.append({
                    "listened_at": int(ts),
                    "track_metadata": meta,
                })
            if not listens:
                continue

            for attempt in range(_MAX_ATTEMPTS):
                if await _submit_batch(user_id, token, listens):
                    logging.info("scrobbler: submitted uid=%d count=%d", user_id, len(listens))
                    break
                if attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_RETRY_BACKOFF * (2 ** attempt))
            else:
                logging.error(
                    "scrobbler: dropping %d listen(s) uid=%d after %d attempts",
                    len(listens), user_id, _MAX_ATTEMPTS,
                )
        except Exception:
            logging.exception("scrobbler: worker batch failed")
        finally:
            for _ in batch:
                q.task_done()
