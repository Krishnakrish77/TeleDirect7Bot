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

# ── Pending channel detection ("add the bot, press Continue") ─────────────
# Telegram tells the bot when its own membership changes, including who made
# the change, so a freshly added vault can be reported back to the web wizard
# without the user ever handling a channel id or message link.
_PENDING_TTL_SECONDS = 900.0
_PENDING_LINKS: dict[int, dict] = {}


def remember_pending_link(user_id: int, channel_id: int, title: str,
                          *, now: float | None = None) -> None:
    """Record "this user just added the bot to this channel"."""
    import time
    _PENDING_LINKS[int(user_id)] = {
        "channel_id": int(channel_id),
        "title": title or "",
        "at": time.time() if now is None else now,
    }


def pending_link_for(user_id: int, *, now: float | None = None) -> dict | None:
    """The user's recent pending channel, or None once it goes stale."""
    import time
    entry = _PENDING_LINKS.get(int(user_id))
    if not entry:
        return None
    if (time.time() if now is None else now) - entry["at"] > _PENDING_TTL_SECONDS:
        _PENDING_LINKS.pop(int(user_id), None)
        return None
    return entry


def clear_pending_link(user_id: int) -> None:
    _PENDING_LINKS.pop(int(user_id), None)


def _chat_type_name(chat_type) -> str:
    """Pyrogram enums compare by identity, so normalise through .value."""
    return str(getattr(chat_type, "value", chat_type) or "").lower()


def _member_status_name(status) -> str:
    return str(getattr(status, "value", status) or "").lower()


def _bot_membership_change(update, bot_id: int) -> tuple[bool, str]:
    """Classify a ChatMemberUpdated as "the bot just became a channel admin".

    Returns ``(True, "")`` for the vault-adding event; otherwise
    ``(False, reason)`` so the caller can log why an update was ignored.
    """
    chat = getattr(update, "chat", None)
    new_member = getattr(update, "new_chat_member", None)
    if chat is None or new_member is None:
        return False, "no chat/member payload"
    member_user = getattr(new_member, "user", None)
    if member_user is None or int(getattr(member_user, "id", 0) or 0) != int(bot_id):
        return False, "not about this bot"
    if _chat_type_name(getattr(chat, "type", "")) != "channel":
        return False, "not a channel"
    if _member_status_name(getattr(new_member, "status", "")) not in (
        "administrator", "owner", "creator",
    ):
        return False, "bot is not an administrator"
    return True, ""


async def _dm(user_id: int, text: str) -> None:
    """Best-effort courtesy DM — the web poll works even when it fails."""
    try:
        await StreamBot.send_message(user_id, text)
    except Exception as exc:
        log.debug("photos: could not DM uid=%s: %r", user_id, exc)


@StreamBot.on_chat_member_updated(group=-3)
async def photo_bot_added_to_channel(client: Client, update):
    """Turn "user added the bot to their channel" into a pending link.

    The web wizard polls for this, so linking needs neither the numeric id
    nor a message link. Updates missed while the bot was down fall back to
    the manual link/id path in the wizard.
    """
    try:
        if not Var.PHOTOS_ENABLED:
            return
        me = getattr(client, "me", None)
        if me is None:
            return
        relevant, reason = _bot_membership_change(update, int(me.id))
        if not relevant:
            log.debug("photos: ignoring membership update (%s)", reason)
            return
        chat = update.chat
        actor = getattr(update, "from_user", None)
        if actor is None:
            log.warning(
                "photos: bot added to channel %s but the update carried no actor",
                chat.id,
            )
            return
        # Lazily imported: photo_routes owns the connect verification rules.
        from main.server.photo_routes import _verify_channel_access

        verified, reject_reason, _definitive = await _verify_channel_access(
            chat.id, int(actor.id)
        )
        if verified is None:
            log.warning(
                "photos: channel %s added by uid=%s rejected: %s",
                chat.id, actor.id, reject_reason,
            )
            await _dm(int(actor.id), f"Could not link that channel: {reject_reason}")
            return
        title = verified.get("title") or getattr(chat, "title", "") or ""
        remember_pending_link(int(actor.id), int(chat.id), title)
        log.info(
            "photos: pending channel %s (%s) for uid=%s", chat.id, title, actor.id
        )
        await _dm(
            int(actor.id),
            f"✅ {title or 'Channel'} detected.\n"
            "Return to the TeleDirect Photos page and press Continue to finish linking.",
        )
    except Exception:
        log.exception("photo_bot_added_to_channel failed")


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
            # Global cap on simultaneously-buffered originals — ingest holds a
            # whole photo/video in RAM and there is one worker per channel.
            # FloodWait must escape the worker for requeue, so it is caught
            # outside this block.
            slot = photo_pipeline.download_slot()
            if slot.locked():
                # Explains "ingest is slow" without guessing: another channel
                # is holding the memory budget.
                log.debug(
                    "ingest waiting for a download slot cid=%d mid=%d",
                    channel_id, getattr(message, "id", -1),
                )
            async with slot:
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


def _photos_channel_filter():
    """Match everything channel-shaped; binding is checked in the handler."""
    from pyrogram import filters
    return filters.channel & (filters.document | filters.video | filters.photo)


# ── Rescan (catch-up for downtime / dropped posts) ───────────────────────

_RESCAN_TASKS: dict[int, asyncio.Task] = {}
# History page size; get_chat_history yields newest-first and handles
# pagination internally — we just bound total items scanned per pass.
_RESCAN_MAX_ITEMS = 20000


def schedule_rescan(owner_user_id: int, channel_id: int) -> None:
    """Kick off a background history scan; coalesces repeat calls."""
    if not Var.PHOTOS_ENABLED:
        return
    task = _RESCAN_TASKS.get(channel_id)
    if task is not None and not task.done():
        return  # one already running for this channel
    _RESCAN_TASKS[channel_id] = asyncio.create_task(rescan_channel(owner_user_id, channel_id))


async def rescan_channel(owner_user_id: int, channel_id: int) -> int:
    """Walk the vault's history and enqueue posts that were never indexed.

    channel_post only fires while the bot is live, and a full ingest queue
    drops posts — without this, photos sent during downtime would sit in
    the channel but never appear in the gallery. Idempotent: the pipeline
    dedups on (channel_id, message_id) and (owner, sha256). Returns the
    number of messages enqueued.
    """
    from main.bot import multi_clients
    bot = multi_clients.get(0) or StreamBot
    try:
        indexed = await photo_store.list_indexed_message_ids(owner_user_id, channel_id)
        enqueued = 0
        scanned = 0
        async for message in bot.get_chat_history(channel_id):
            scanned += 1
            if scanned > _RESCAN_MAX_ITEMS:
                log.warning("rescan truncated at %d items cid=%d", _RESCAN_MAX_ITEMS, channel_id)
                break
            if message.empty or not (message.document or message.video or message.photo):
                continue
            if message.id in indexed:
                continue
            q = _queue_for(channel_id)
            try:
                q.put_nowait((owner_user_id, message))
            except asyncio.QueueFull:
                # Drain is FIFO and rescan is repeatable — stop here, the
                # next pass picks up the rest.
                log.warning("rescan queue full at %d enqueued cid=%d", enqueued, channel_id)
                break
            enqueued += 1
        if enqueued:
            _ensure_worker(channel_id)
        log.info("rescan cid=%d: scanned=%d enqueued=%d", channel_id, scanned, enqueued)
        return enqueued
    except FloodWait as e:
        wait = float(getattr(e, "value", getattr(e, "x", 1)))
        log.warning("rescan FloodWait %ss cid=%d; will retry on next trigger", wait, channel_id)
        return 0
    except Exception:
        log.exception("rescan failed cid=%d", channel_id)
        return 0


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
