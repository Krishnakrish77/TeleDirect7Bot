"""TeleDirect Photos channel_post handler.

Any post landing in a bound ``photo_channels`` channel is enqueued into the
ingest pipeline (EXIF, sha256 dedup, thumbnails). Bytes stay in the channel;
metadata + thumbs land in Mongo.

The catalogue's stream.py channel handler must ignore bound photo channels —
photo posts never enter the public media hub and never emit public hash links.
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import Client

from main import Var
from main.bot import StreamBot
from main.utils import photo_store, photo_pipeline

try:
    from pyrogram.errors import FloodWait
except ImportError:  # pragma: no cover
    FloodWait = Exception  # type: ignore[assignment,misc]

log = logging.getLogger("photos.plugin")

# Ingest concurrency: one worker per channel keeps Telegram pacing sane and
# caps CPU spent on thumbnail generation. Queue is drained FIFO so timeline
# order roughly matches upload order.
_CHANNEL_QUEUES: dict[int, "asyncio.Queue"] = {}
_QUEUE_WORKERS: dict[int, asyncio.Task] = {}
_QUEUE_MAX = 1000


def _queue_for(channel_id: int) -> asyncio.Queue:
    q = _CHANNEL_QUEUES.get(channel_id)
    if q is None:
        q = asyncio.Queue(maxsize=_QUEUE_MAX)
        _CHANNEL_QUEUES[channel_id] = q
    return q


def _ensure_worker(channel_id: int) -> None:
    if channel_id in _QUEUE_WORKERS and not _QUEUE_WORKERS[channel_id].done():
        return
    _QUEUE_WORKERS[channel_id] = asyncio.create_task(_worker(channel_id))


async def _worker(channel_id: int) -> None:
    q = _queue_for(channel_id)
    while True:
        item = await q.get()
        if item is None:
            return
        owner_user_id, message = item
        try:
            await photo_pipeline.ingest_message(owner_user_id, channel_id, message)
        except FloodWait as e:
            log.warning("ingest FloodWait %ss cid=%d", getattr(e, "value", getattr(e, "x", 1)), channel_id)
            await asyncio.sleep(float(getattr(e, "value", getattr(e, "x", 1))))
            # Requeue once.
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                log.warning("ingest queue full, dropping mid=%d", getattr(message, "id", -1))
        except Exception:
            log.exception("ingest worker error cid=%d", channel_id)
        finally:
            q.task_done()


def _resolve_owner_sync(channel_id: int):
    """Best-effort synchronous cache check — the async lookup happens in
    the worker; here we just need to reject obviously unbound channels."""
    return channel_id in _CHANNEL_QUEUES


def _photos_channel_filter():
    """Match everything channel-shaped; binding is checked in the handler."""
    from pyrogram import filters
    return filters.channel & (filters.document | filters.video | filters.photo)


@StreamBot.on_message(_photos_channel_filter(), group=-3)
async def photo_channel_post(client: Client, message):
    """Ingest any post in a bound photo channel.

    ``_photos_channel_filter`` is a dynamic filter that can't know the bound
    channel ids at import time, so this handler checks the binding here.
    DB errors on the binding lookup are contained (logged, post dropped) —
    the fail-closed privacy concern lives in stream.py, which skips bound
    channels before this handler sees the post.
    """
    try:
        if not Var.PHOTOS_ENABLED:
            return  # feature off: no ingestion (stream.py's fail-closed skip already protected the vault from the catalogue path)
        channel_id = int(message.chat.id)
        owner_doc = await photo_store.get_channel(channel_id)
        if not owner_doc:
            return  # not a photo vault — let other handlers deal with it
        if owner_doc.get("status") != "active":
            return
        owner_user_id = owner_doc["owner_user_id"]
        q = _queue_for(channel_id)
        try:
            q.put_nowait((owner_user_id, message))
        except asyncio.QueueFull:
            log.warning("photos ingest queue full for cid=%d; dropping mid=%d", channel_id, message.id)
            return
        _ensure_worker(channel_id)
    except Exception:
        log.exception("photo_channel_post failed")


def _photos_channel_filter():
    """Match everything channel-shaped; binding is checked in the handler."""
    from pyrogram import filters
    return filters.channel & (filters.document | filters.video | filters.photo)
