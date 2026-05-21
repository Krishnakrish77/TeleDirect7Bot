"""Durable catalogue storage abstraction.

Replaces the JSON-file-plus-pinned-snapshot persistence path with a
pluggable interface so the in-memory ``_items`` dict can be backed by
either the legacy JSON cache or MongoDB Atlas. The dict itself stays
authoritative at runtime — these backends are write-through stores
that get loaded once on boot and updated on every mutation.

Two backends:

  * ``JsonStore``  — current behaviour: /tmp/media_index.json plus a
    pinned snapshot doc in BIN_CHANNEL. Kept as the default so a
    misconfigured env doesn't break existing installs.
  * ``MongoStore`` — Motor (async PyMongo) talking to MongoDB Atlas.
    Single source of truth, no Telegram snapshot needed, atomic
    per-item upserts so a 70-episode bulk upload doesn't republish
    the whole catalogue.

Selecting the backend:

  ``STORE_BACKEND=mongo``        — enables MongoStore
  ``MONGO_URI=<atlas srv uri>``  — required when STORE_BACKEND=mongo
  ``MONGO_DB``                   — defaults to ``teledirect``
  ``MONGO_COLLECTION``           — defaults to ``items``
  ``MONGO_META_COLLECTION``      — defaults to ``meta``

Migration: see ``/admin/migrate-to-mongo`` which reads the in-process
catalogue (loaded from whatever backend was active at boot) and
upserts every item into Mongo. Run it once after pointing the env
at Atlas; then flip STORE_BACKEND and restart.
"""

from __future__ import annotations

import logging
import os
from typing import AsyncIterator, Iterable, List, Optional, Protocol

# Motor pulls in pymongo which pulls in bson. Importing the Binary
# wrapper at module level (rather than inside the hot set_thumb path)
# avoids paying the import lookup on every persisted thumbnail.
try:
    from bson.binary import Binary as _Binary
except ImportError:  # pragma: no cover — only matters for Mongo deployments
    _Binary = bytes  # type: ignore[assignment,misc]


class Store(Protocol):
    """Catalogue persistence contract.

    Implementations must be safe to call concurrently — multiple
    indexer tasks may upsert different items in parallel.
    """

    async def init(self) -> None: ...
    async def load_all(self) -> List[dict]: ...
    async def upsert(self, doc: dict) -> None: ...
    async def upsert_many(self, docs: Iterable[dict]) -> None: ...
    async def remove(self, message_id: int) -> None: ...
    async def get_meta(self, key: str) -> Optional[object]: ...
    async def set_meta(self, key: str, value: object) -> None: ...


class MongoStore:
    """MongoDB-backed store using Motor for async I/O.

    Documents are HubItem-shaped (the same dict we already write to
    the JSON snapshot). ``message_id`` is the primary key — we use
    it as ``_id`` so Mongo's per-key locking does the deduplication
    for us, no application-level race possible.

    The ``meta`` collection holds singletons like ``latest_seen_id``
    and ``snapshot_msg_id`` (the latter becomes irrelevant once
    Mongo is the source of truth, but we keep it for the migration
    window).
    """

    def __init__(self, uri: str, db_name: str, items_coll: str,
                 meta_coll: str) -> None:
        # Lazy import so installs without Motor (only JSON-store users)
        # don't pay the import cost.
        from motor.motor_asyncio import AsyncIOMotorClient
        self._client = AsyncIOMotorClient(
            uri,
            maxPoolSize=20,           # M0 caps at 100 total connections
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
        )
        self._db_name = db_name
        self._items_coll_name = items_coll
        self._meta_coll_name = meta_coll
        self._items = self._client[db_name][items_coll]
        self._meta = self._client[db_name][meta_coll]
        # Persistent thumbnail cache. Keeps generated JPEGs (Telegram
        # native + ffmpeg fallback) across restarts so cold series pages
        # don't re-run ffmpeg on every deploy. Collection name doesn't
        # need env-tuning — fixed sibling of items/meta.
        self._thumbs = self._client[db_name]["thumbs"]
        self._initialised = False

    async def init(self) -> None:
        """Create indexes. Idempotent — create_index is a no-op if
        the index already exists."""
        if self._initialised:
            return
        try:
            await self._items.create_index("message_id", unique=True)
            await self._items.create_index("series_key")
            await self._items.create_index("movie_key")
            await self._items.create_index("tmdb_id", sparse=True)
            await self._items.create_index([("year", -1)])
            # Additional indexes for scale (10K+ items):
            await self._items.create_index("secure_hash", sparse=True)
            # Compound for ordered episode listing within a series.
            await self._items.create_index([
                ("series_key", 1), ("season", 1), ("episode", 1),
            ], sparse=True)
            # Sparse indexes on the "needs work" timestamps so maintenance
            # sweeps that look for missing enrichment/probe data are fast.
            await self._items.create_index("enriched_at", sparse=True)
            await self._items.create_index("probed_at", sparse=True)
            self._initialised = True
            logging.info(
                "store.mongo: connected to %s.%s", self._db_name,
                self._items_coll_name,
            )
        except Exception:
            logging.exception("store.mongo: init failed")
            raise

    # ── Thumbnail persistence ────────────────────────────────────────
    # JPEG bytes are stored as BSON Binary keyed by message_id. Survives
    # restarts so series pages don't re-run ffmpeg from cold.

    async def get_thumb(self, message_id: int) -> Optional[bytes]:
        try:
            doc = await self._thumbs.find_one(
                {"_id": int(message_id)}, projection={"data": 1},
            )
            if not doc:
                return None
            data = doc.get("data")
            return bytes(data) if data else None
        except Exception:
            logging.exception("store.mongo: get_thumb failed for bin:%d", message_id)
            return None

    # Defensive cap. MongoDB BSON docs are limited to 16 MB; real JPEG
    # thumbs are ≤30 KB. A future fetcher producing huge bytes would
    # otherwise crash the write — quietly skip persisting instead.
    _THUMB_MAX_BYTES = 15 * 1024 * 1024

    async def set_thumb(self, message_id: int, data: bytes) -> None:
        """Persist a thumbnail. Exceptions PROPAGATE so the caller can
        decide whether to retry (the thumb_cache write-through retries
        once after a 2 s pause)."""
        if not data or len(data) > self._THUMB_MAX_BYTES:
            return
        await self._thumbs.replace_one(
            {"_id": int(message_id)},
            {"_id": int(message_id), "data": _Binary(data)},
            upsert=True,
        )

    async def remove_thumb(self, message_id: int) -> None:
        try:
            await self._thumbs.delete_one({"_id": int(message_id)})
        except Exception:
            logging.exception("store.mongo: remove_thumb failed for bin:%d", message_id)

    async def get_thumbs_bulk(self, message_ids: Iterable[int]) -> "dict":
        """Hydrate many thumbs in one round-trip — cuts page-render cost
        on series/hub pages where a single find_one per thumbnail would
        otherwise become N×latency."""
        ids = [int(m) for m in message_ids]
        if not ids:
            return {}
        try:
            cursor = self._thumbs.find(
                {"_id": {"$in": ids}}, projection={"data": 1},
            )
            out: dict = {}
            async for doc in cursor:
                data = doc.get("data")
                if data:
                    out[int(doc["_id"])] = bytes(data)
            return out
        except Exception:
            logging.exception("store.mongo: get_thumbs_bulk failed")
            return {}

    async def thumb_ids(self) -> "list":
        """Return all message_ids that currently have a persisted thumb.
        Used by the orphan-prune maintenance action."""
        try:
            cursor = self._thumbs.find({}, projection={"_id": 1})
            return [int(doc["_id"]) async for doc in cursor]
        except Exception:
            logging.exception("store.mongo: thumb_ids failed")
            return []

    async def load_all(self) -> List[dict]:
        """Return every item as a list of dicts in HubItem-shape.

        We pull the whole collection in one go on boot; for the
        catalogue sizes this project realistically handles (up to
        a few hundred thousand items) the latency cost of a single
        large query is much smaller than the cumulative cost of
        querying per-render.
        """
        try:
            cursor = self._items.find({}, projection={"_id": False})
            out = [doc async for doc in cursor]
            logging.info("store.mongo: loaded %d items", len(out))
            return out
        except Exception:
            logging.exception("store.mongo: load_all failed")
            return []

    async def upsert(self, doc: dict) -> None:
        try:
            mid = int(doc.get("message_id") or 0)
            if mid <= 0:
                return
            await self._items.replace_one(
                {"message_id": mid}, doc, upsert=True,
            )
        except Exception:
            logging.exception(
                "store.mongo: upsert failed for bin:%s",
                doc.get("message_id"),
            )

    async def upsert_many(self, docs: Iterable[dict]) -> None:
        """Bulk-upsert helper used by the migration script. Falls back
        to per-doc upserts on Mongo connection errors so a partial
        failure still writes whatever was reachable.
        """
        from pymongo import ReplaceOne
        ops = []
        for d in docs:
            mid = int(d.get("message_id") or 0)
            if mid <= 0:
                continue
            ops.append(ReplaceOne({"message_id": mid}, d, upsert=True))
        if not ops:
            return
        # Batch in chunks of 500 to stay well under Mongo's per-op
        # size limits and keep failure granularity reasonable.
        BATCH = 500
        for i in range(0, len(ops), BATCH):
            try:
                await self._items.bulk_write(ops[i:i + BATCH], ordered=False)
            except Exception:
                logging.exception(
                    "store.mongo: bulk_write failed at batch starting %d", i,
                )

    async def remove(self, message_id: int) -> None:
        try:
            await self._items.delete_one({"message_id": int(message_id)})
        except Exception:
            logging.exception(
                "store.mongo: remove failed for bin:%d", message_id,
            )

    async def get_meta(self, key: str) -> Optional[object]:
        try:
            doc = await self._meta.find_one({"_id": key})
            return doc.get("value") if doc else None
        except Exception:
            logging.exception("store.mongo: get_meta(%s) failed", key)
            return None

    async def set_meta(self, key: str, value: object) -> None:
        try:
            await self._meta.replace_one(
                {"_id": key},
                {"_id": key, "value": value},
                upsert=True,
            )
        except Exception:
            logging.exception("store.mongo: set_meta(%s) failed", key)


def from_env() -> Optional["Store"]:
    """Return a configured store based on environment variables, or
    None if the env says "use the legacy JSON path".

    Currently only returns a non-None value when ``STORE_BACKEND=mongo``;
    we deliberately don't expose a "JsonStore" wrapper because the
    legacy code path already handles JSON natively and isolating it
    behind this abstraction is unnecessary churn for the migration.
    """
    backend = (os.environ.get("STORE_BACKEND") or "").strip().lower()
    if backend != "mongo":
        return None
    uri = os.environ.get("MONGO_URI") or ""
    if not uri:
        logging.error(
            "store: STORE_BACKEND=mongo but MONGO_URI is empty — "
            "falling back to legacy JSON path",
        )
        return None
    db_name = os.environ.get("MONGO_DB") or "teledirect"
    items_coll = os.environ.get("MONGO_COLLECTION") or "items"
    meta_coll = os.environ.get("MONGO_META_COLLECTION") or "meta"
    try:
        return MongoStore(uri, db_name, items_coll, meta_coll)
    except Exception:
        logging.exception("store: MongoStore init failed; using JSON fallback")
        return None
