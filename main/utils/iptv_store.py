"""Admin-managed IPTV channel catalogue.

Phase 1 intentionally stores only channel metadata and stream URLs.  EPG,
catch-up, and provider-token refresh can layer on this later without changing
the public channel shape.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from .iptv_parser import parse_m3u_text


_STORE_FILE = Path(os.environ.get("IPTV_STORE_PATH", "/tmp/iptv_channels.json"))
_lock = asyncio.Lock()
_loaded = False
_channels: dict[str, dict] = {}
_indexed = False


def _now() -> float:
    return time.time()


def _get_db():
    try:
        from main.utils import media_index as _mi

        store = _mi._store
        if store is None or not hasattr(store, "_client"):
            return None
        return store._client[store._db_name]
    except Exception:
        return None


async def _ensure_indexes() -> None:
    global _indexed
    if _indexed:
        return
    db = _get_db()
    if db is None:
        return
    try:
        coll = db["iptv_channels"]
        await coll.create_index("channel_id", unique=True)
        await coll.create_index([("enabled", 1), ("sort_order", 1), ("name", 1)])
        await coll.create_index([("stream_url", 1)], unique=True)
        await coll.create_index("tvg_id")
        _indexed = True
    except Exception:
        logging.exception("iptv_store: ensure_indexes failed")


def is_mongo_available() -> bool:
    return _get_db() is not None


def _clean(value: object, max_len: int = 300) -> str:
    return str(value or "").strip()[:max_len]


def _normalise_url(value: object) -> str:
    url = _clean(value, 2000)
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return ""
    return url


def _normalise_channel(raw: dict, existing: dict | None = None) -> dict:
    now = _now()
    channel_id = _clean(raw.get("channel_id") or raw.get("id") or (existing or {}).get("channel_id"), 64)
    if not channel_id:
        channel_id = uuid.uuid4().hex
    name = _clean(raw.get("name"), 160)
    stream_url = _normalise_url(raw.get("stream_url") or raw.get("streamUrl"))
    logo_url = _normalise_url(raw.get("logo_url") or raw.get("logoUrl"))
    category = _clean(raw.get("category"), 80) or "Uncategorized"
    tvg_id = _clean(raw.get("tvg_id") or raw.get("tvgId"), 180)
    tvg_name = _clean(raw.get("tvg_name") or raw.get("tvgName"), 180)
    duration = _clean(raw.get("duration"), 40) or "-1"
    attrs = raw.get("attrs") if isinstance(raw.get("attrs"), dict) else {}
    extras = raw.get("extras") if isinstance(raw.get("extras"), list) else []
    stream_headers = raw.get("stream_headers") or raw.get("streamHeaders")
    if not isinstance(stream_headers, dict):
        stream_headers = {}
    try:
        sort_order = int(raw.get("sort_order") if raw.get("sort_order") is not None else raw.get("sortOrder") or 0)
    except (TypeError, ValueError):
        sort_order = 0
    enabled = raw.get("enabled")
    if enabled is None:
        enabled = (existing or {}).get("enabled", True)
    return {
        "channel_id": channel_id,
        "name": name,
        "stream_url": stream_url,
        "logo_url": logo_url,
        "category": category,
        "tvg_id": tvg_id,
        "tvg_name": tvg_name,
        "duration": duration,
        "attrs": {str(k): _clean(v, 1000) for k, v in attrs.items()},
        "extras": [_clean(value, 1000) for value in extras if _clean(value)],
        "stream_headers": {str(k): _clean(v, 1000) for k, v in stream_headers.items()},
        "enabled": bool(enabled),
        "sort_order": sort_order,
        "created_at": float((existing or {}).get("created_at") or now),
        "updated_at": now,
    }


def _public_channel(channel: dict) -> dict:
    return {
        "id": channel["channel_id"],
        "name": channel["name"],
        "streamUrl": channel["stream_url"],
        "logoUrl": channel.get("logo_url", ""),
        "category": channel.get("category", "Uncategorized"),
        "tvgId": channel.get("tvg_id", ""),
        "tvgName": channel.get("tvg_name", ""),
        "duration": channel.get("duration", "-1"),
        "attrs": channel.get("attrs", {}),
        "extras": channel.get("extras", []),
        "streamHeaders": channel.get("stream_headers", {}),
        "enabled": bool(channel.get("enabled", True)),
        "sortOrder": int(channel.get("sort_order", 0) or 0),
        "createdAt": float(channel.get("created_at", 0) or 0),
        "updatedAt": float(channel.get("updated_at", 0) or 0),
    }


def _sort_key(channel: dict) -> tuple:
    return (
        int(channel.get("sort_order", 0) or 0),
        str(channel.get("category") or "").lower(),
        str(channel.get("name") or "").lower(),
    )


async def _load_json_unlocked() -> None:
    global _loaded
    if _loaded:
        return
    try:
        if not _STORE_FILE.exists():
            _loaded = True
            return
        data = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
        for raw in data.get("channels", []):
            channel = _normalise_channel(raw)
            if channel["name"] and channel["stream_url"]:
                _channels[channel["channel_id"]] = channel
        _loaded = True  # only mark loaded after successful read
    except Exception:
        logging.exception("iptv_store: failed to load JSON store")
        # Do NOT set _loaded — next call will retry the load


def _persist_json_unlocked() -> None:
    try:
        _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "saved_at": _now(),
            "channels": sorted(_channels.values(), key=_sort_key),
        }
        tmp = _STORE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_STORE_FILE)
    except Exception:
        logging.exception("iptv_store: failed to persist JSON store")


async def list_channels(include_disabled: bool = False) -> list[dict]:
    await _ensure_indexes()
    db = _get_db()
    if db is not None:
        query = {} if include_disabled else {"enabled": True}
        try:
            cursor = db["iptv_channels"].find(query, projection={"_id": 0})
            docs = await cursor.to_list(length=2000)
            return [_public_channel(ch) for ch in sorted(docs, key=_sort_key)]
        except Exception:
            logging.exception("iptv_store: list_channels failed")
            return []

    async with _lock:
        await _load_json_unlocked()
        items = [
            ch for ch in _channels.values()
            if include_disabled or ch.get("enabled", True)
        ]
        return [_public_channel(ch) for ch in sorted(items, key=_sort_key)]


async def get_channel(channel_id: str, include_disabled: bool = False) -> Optional[dict]:
    channel_id = _clean(channel_id, 64)
    if not channel_id:
        return None
    await _ensure_indexes()
    db = _get_db()
    if db is not None:
        query = {"channel_id": channel_id}
        if not include_disabled:
            query["enabled"] = True
        try:
            doc = await db["iptv_channels"].find_one(query, projection={"_id": 0})
            return _public_channel(doc) if doc else None
        except Exception:
            logging.exception("iptv_store: get_channel failed id=%s", channel_id)
            return None

    async with _lock:
        await _load_json_unlocked()
        channel = _channels.get(channel_id)
        if not channel or (not include_disabled and not channel.get("enabled", True)):
            return None
        return _public_channel(channel)


async def save_channel(raw: dict) -> tuple[bool, dict | None, str]:
    await _ensure_indexes()
    channel = _normalise_channel(raw)
    if not channel["name"]:
        return False, None, "Name is required"
    if not channel["stream_url"]:
        return False, None, "A valid http(s) stream URL is required"

    db = _get_db()
    if db is not None:
        try:
            existing = await db["iptv_channels"].find_one({"channel_id": channel["channel_id"]}, projection={"_id": 0})
            if existing is None:
                existing = await db["iptv_channels"].find_one({"stream_url": channel["stream_url"]}, projection={"_id": 0})
            if channel.get("tvg_id"):
                tvgid_match = await db["iptv_channels"].find_one({"tvg_id": channel["tvg_id"]}, projection={"_id": 0})
                if tvgid_match is None:
                    existing = existing  # no tvg_id match, keep stream_url result
                elif existing is None:
                    existing = tvgid_match  # only tvg_id match
                elif existing["channel_id"] != tvgid_match["channel_id"]:
                    # stream_url and tvg_id match different records — stream_url wins, remove the tvg_id duplicate
                    await db["iptv_channels"].delete_one({"channel_id": tvgid_match["channel_id"]})
            if existing:
                raw = {**raw, "channel_id": existing["channel_id"]}
            channel = _normalise_channel(raw, existing)
            await db["iptv_channels"].replace_one(
                {"channel_id": channel["channel_id"]},
                channel,
                upsert=True,
            )
            return True, _public_channel(channel), ""
        except Exception:
            logging.exception("iptv_store: save_channel failed")
            return False, None, "Unable to save channel"

    async with _lock:
        await _load_json_unlocked()
        original_id = channel["channel_id"]
        existing = _channels.get(original_id)
        channel = _normalise_channel(raw, existing)
        duplicate = next(
            (
                item for item in _channels.values()
                if (
                    item["stream_url"] == channel["stream_url"]
                    or (channel.get("tvg_id") and item.get("tvg_id") == channel.get("tvg_id"))
                )
                and item["channel_id"] != channel["channel_id"]
            ),
            None,
        )
        if duplicate:
            # Remove the original id entry before writing under the deduped id
            _channels.pop(original_id, None)
            channel["channel_id"] = duplicate["channel_id"]
            channel["created_at"] = duplicate.get("created_at", channel["created_at"])
        _channels[channel["channel_id"]] = channel
        _persist_json_unlocked()
        return True, _public_channel(channel), ""


async def delete_channel(channel_id: str) -> bool:
    channel_id = _clean(channel_id, 64)
    if not channel_id:
        return False
    await _ensure_indexes()
    db = _get_db()
    if db is not None:
        try:
            result = await db["iptv_channels"].delete_one({"channel_id": channel_id})
            return result.deleted_count > 0
        except Exception:
            logging.exception("iptv_store: delete_channel failed id=%s", channel_id)
            return False

    async with _lock:
        await _load_json_unlocked()
        existed = _channels.pop(channel_id, None) is not None
        if existed:
            _persist_json_unlocked()
        return existed


def parse_m3u(text: str) -> list[dict]:
    playlist = parse_m3u_text(text)
    return [
        {
            "name": channel.name,
            "stream_url": channel.stream_url,
            "logo_url": channel.logo_url,
            "category": channel.category,
            "tvg_id": channel.tvg_id,
            "tvg_name": channel.tvg_name,
            "duration": channel.duration,
            "attrs": channel.attrs,
            "extras": channel.extras,
            "stream_headers": channel.stream_headers,
            "playlist_attrs": playlist.attrs,
            "enabled": True,
        }
        for channel in playlist.channels
    ]


async def import_m3u(text: str) -> dict:
    parsed = parse_m3u(text)
    imported = 0
    skipped = 0
    channels: list[dict] = []

    db = _get_db()
    if db is not None:
        # MongoDB path: sequential save (each handles its own dedup)
        for raw in parsed[:1000]:
            ok, channel, _ = await save_channel(raw)
            if ok and channel:
                imported += 1
                channels.append(channel)
            else:
                skipped += 1
        return {"parsed": len(parsed), "imported": imported, "skipped": skipped, "channels": channels}

    # JSON path: hold lock once, write file once at the end instead of per-channel
    async with _lock:
        await _load_json_unlocked()
        for raw in parsed[:1000]:
            # Pre-validate before normalising so we don't generate a throwaway
            # UUID on the first call only to discard it when calling again with
            # the resolved `existing` record.
            _name = _clean(raw.get("name"), 160)
            _url = _normalise_url(raw.get("stream_url") or raw.get("streamUrl") or "")
            if not _name or not _url:
                skipped += 1
                continue
            _raw_id = _clean(raw.get("channel_id") or raw.get("id") or "", 64)
            existing = _channels.get(_raw_id) or None
            channel = _normalise_channel(raw, existing)
            duplicate = next(
                (
                    item for item in _channels.values()
                    if (
                        item["stream_url"] == channel["stream_url"]
                        or (channel.get("tvg_id") and item.get("tvg_id") == channel.get("tvg_id"))
                    )
                    and item["channel_id"] != channel["channel_id"]
                ),
                None,
            )
            if duplicate:
                channel["channel_id"] = duplicate["channel_id"]
                channel["created_at"] = duplicate.get("created_at", channel["created_at"])
            _channels[channel["channel_id"]] = channel
            imported += 1
            channels.append(_public_channel(channel))
        if imported:
            _persist_json_unlocked()

    return {"parsed": len(parsed), "imported": imported, "skipped": skipped, "channels": channels}
