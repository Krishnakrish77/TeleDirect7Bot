"""Durable per-user reading locations for the Books reader."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

_INDEXED = False
_TTL_DAYS = 365


def _db():
    try:
        from main.utils import media_index
        store = media_index._store
        return store._client[store._db_name] if store is not None and hasattr(store, "_client") else None
    except Exception:
        return None


async def _indexes() -> None:
    global _INDEXED
    if _INDEXED:
        return
    db = _db()
    if db is None:
        return
    try:
        collection = db["book_progress"]
        await collection.create_index([("user_id", 1), ("book_id", 1)], unique=True)
        await collection.create_index("updated_at", expireAfterSeconds=_TTL_DAYS * 86400)
        reader_data = db["book_reader_data"]
        await reader_data.create_index([("user_id", 1), ("book_id", 1)], unique=True)
        await reader_data.create_index("updated_at", expireAfterSeconds=_TTL_DAYS * 86400)
        _INDEXED = True
    except Exception:
        logging.exception("book_progress: index creation failed")


async def get_all(user_id: int) -> dict[str, dict]:
    await _indexes()
    db = _db()
    if db is None:
        return {}
    try:
        docs = await db["book_progress"].find(
            {"user_id": user_id},
            projection={"book_id": 1, "locator": 1, "progress": 1, "t": 1, "_id": 0},
            sort=[("t", -1)],
        ).to_list(length=250)
        return {str(doc["book_id"]): {"locator": doc.get("locator", ""), "progress": doc.get("progress", 0), "t": doc.get("t", 0)} for doc in docs}
    except Exception:
        logging.exception("book_progress: fetch failed uid=%d", user_id)
        return {}


async def upsert(user_id: int, book_id: int, locator: str, progress: float, stamp: int) -> bool:
    await _indexes()
    db = _db()
    if db is None:
        return False
    try:
        await db["book_progress"].update_one(
            {"user_id": user_id, "book_id": book_id},
            {"$set": {"locator": locator, "progress": progress, "t": stamp, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return True
    except Exception:
        logging.exception("book_progress: save failed uid=%d book=%d", user_id, book_id)
        return False


async def get_reader_data(user_id: int, book_id: int) -> dict:
    await _indexes()
    db = _db()
    if db is None:
        return {"bookmarks": [], "notes": []}
    try:
        doc = await db["book_reader_data"].find_one({"user_id": user_id, "book_id": book_id}, projection={"bookmarks": 1, "notes": 1, "t": 1, "_id": 0}) or {}
        return {"bookmarks": doc.get("bookmarks") or [], "notes": doc.get("notes") or [], "t": doc.get("t", 0)}
    except Exception:
        logging.exception("book_progress: reader data fetch failed uid=%d", user_id)
        return {"bookmarks": [], "notes": []}


async def upsert_reader_data(user_id: int, book_id: int, bookmarks: list[dict], notes: list[dict], stamp: int) -> bool:
    await _indexes()
    db = _db()
    if db is None:
        return False
    try:
        await db["book_reader_data"].update_one(
            {"user_id": user_id, "book_id": book_id},
            {"$set": {"bookmarks": bookmarks[:30], "notes": notes[:50], "t": stamp, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return True
    except Exception:
        logging.exception("book_progress: reader data save failed uid=%d", user_id)
        return False
