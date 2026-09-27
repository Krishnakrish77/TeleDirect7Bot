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
import time

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


def _prune_pending_links(now: float) -> None:
    """Drop stale handshakes so the map cannot grow one entry per attempt."""
    for user_id, entry in list(_PENDING_LINKS.items()):
        if now - entry["at"] > _PENDING_TTL_SECONDS:
            _PENDING_LINKS.pop(user_id, None)


def remember_pending_link(user_id: int, channel_id: int, title: str,
                          *, now: float | None = None) -> None:
    """Record "this user just added the bot to this channel"."""
    stamp = time.time() if now is None else now
    _prune_pending_links(stamp)
    _PENDING_LINKS[int(user_id)] = {
        "channel_id": int(channel_id),
        "title": title or "",
        "at": stamp,
    }


def pending_link_for(user_id: int, *, now: float | None = None) -> dict | None:
    """The user's recent pending channel, or None once it goes stale."""
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
# Bots may NOT page a chat's history — messages.GetHistory answers
# BOT_METHOD_INVALID (this used to crash every scan). Instead the backfill
# probes message ids with channels.GetMessages, the same call the live ingest
# already makes: up to 100 ids per request, empty slots (deleted or unused
# ids) arrive as MessageEmpty and are skipped. A persisted cursor makes
# repeated passes continue instead of restarting.
_RESCAN_BATCH = 100
_RESCAN_MAX_BATCHES = 200          # per pass: 20k ids, then resume next trigger
_RESCAN_EMPTY_BATCHES_STOP = 5     # 500 consecutive empty ids == end of space
_RESCAN_OVERLAP = 200              # re-check the tail for posts missed while live
_RESCAN_BINDING_CHECK_BATCHES = 25 # how often to confirm the vault is still bound


def schedule_rescan(owner_user_id: int, channel_id: int) -> None:
    """Kick off a background history scan; coalesces repeat calls."""
    if not Var.PHOTOS_ENABLED:
        return
    task = _RESCAN_TASKS.get(channel_id)
    if task is not None and not task.done():
        return  # one already running for this channel
    _RESCAN_TASKS[channel_id] = asyncio.create_task(rescan_channel(owner_user_id, channel_id))


def _enqueue_pending(owner_user_id: int, channel_id: int, message) -> bool:
    """Queue one message for ingest; False when the queue is saturated."""
    q = _queue_for(channel_id)
    try:
        q.put_nowait((owner_user_id, message))
    except asyncio.QueueFull:
        return False
    return True


async def rescan_channel(owner_user_id: int, channel_id: int) -> int:
    """Backfill vault posts that were never indexed.

    ``channel_post`` only fires while the bot is live and a full ingest queue
    drops posts, so photos posted during downtime would never appear. The bot
    cannot page history, hence the id-probing walk; steps are idempotent
    (ingest dedups on channel/message and owner/sha256) and the cursor means a
    pass interrupted by FloodWait, a full queue or shutdown resumes rather than
    restarting. Returns the number of messages enqueued.
    """
    from main.bot import multi_clients
    bot = multi_clients.get(0) or StreamBot
    await photo_store.set_scan_status(channel_id, state="running")
    try:
        indexed = await photo_store.list_indexed_message_ids(owner_user_id, channel_id)
        cursor = await photo_store.get_scan_cursor(channel_id)
        start = max(1, cursor - _RESCAN_OVERLAP) if cursor else 1
        enqueued = 0
        batches = 0
        empty_streak = 0
        while batches < _RESCAN_MAX_BATCHES:
            if batches and batches % _RESCAN_BINDING_CHECK_BATCHES == 0:
                # A disconnect mid-scan must not keep indexing a vault the user
                # just detached (and must not index a channel re-bound to
                # somebody else).
                binding = await photo_store.get_channel(channel_id)
                if not binding or binding.get("owner_user_id") != owner_user_id:
                    log.info(
                        "rescan cid=%d: no longer bound to uid=%d, stopping at id=%d",
                        channel_id, owner_user_id, start,
                    )
                    return enqueued
            ids = list(range(start, start + _RESCAN_BATCH))
            messages = await bot.get_messages(channel_id, ids)
            if not isinstance(messages, list):
                messages = [messages]
            found = 0
            drained = True
            for message in messages:
                if message is None or getattr(message, "empty", False):
                    continue
                if not (message.document or message.video or message.photo):
                    continue
                found += 1
                if message.id in indexed:
                    continue
                if not _enqueue_pending(owner_user_id, channel_id, message):
                    # Resume this batch on the next pass rather than skipping
                    # the ids we could not queue.
                    log.warning(
                        "rescan queue full cid=%d at id=%d (%d enqueued)",
                        channel_id, message.id, enqueued,
                    )
                    drained = False
                    break
                enqueued += 1
            empty_streak = 0 if found else empty_streak + 1
            batches += 1
            if not drained:
                break
            start += _RESCAN_BATCH
            await photo_store.set_scan_cursor(channel_id, start)
            if empty_streak >= _RESCAN_EMPTY_BATCHES_STOP:
                break
        if enqueued:
            _ensure_worker(channel_id)
        log.info(
            "rescan cid=%d: batches=%d enqueued=%d cursor=%d",
            channel_id, batches, enqueued, start,
        )
        await photo_store.set_scan_status(
            channel_id, state="done", enqueued=enqueued, scanned_to=start
        )
        return enqueued
    except FloodWait as e:
        wait = float(getattr(e, "value", getattr(e, "x", 1)))
        log.warning(
            "rescan FloodWait %ss cid=%d; resuming from the saved cursor", wait, channel_id
        )
        await photo_store.set_scan_status(
            channel_id, state="paused", error=f"Telegram rate limit ({wait:.0f}s)"
        )
        return 0
    except Exception as exc:
        log.exception("rescan failed cid=%d", channel_id)
        await photo_store.set_scan_status(
            channel_id, state="error", error=f"{type(exc).__name__}: {exc}"
        )
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
