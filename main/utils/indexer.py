"""
Auto-indexer for BIN_CHANNEL.

After a file is forwarded into BIN_CHANNEL by the existing stream handlers,
the bot rewrites that message's caption into a structured "index entry"
format. The same message now plays two roles: the byte source for streaming
and the hub's catalogue entry. No separate INDEX_CHANNEL needed — the hub's
browse/search routes read BIN_CHANNEL captions directly.

The /edit_index admin command can refine title and tags afterward.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional


# Bound concurrency of the per-message indexer + downstream
# enrichment so a burst of N parallel uploads (10 episodes of a
# series sent at once) doesn't fan out into N caption edits +
# N TMDB lookups + N ffprobe subprocesses simultaneously. Telegram
# rate-limits caption edits (FloodWait), TMDB rate-limits search
# (HTTP 429), and ffprobe holds a range stream + subprocess each
# — all three behave badly when fired in parallel without bound.
#
# Tunable via env so heavy operators can lift the cap. Default
# matches Pyrogram's WORKERS default and the existing
# codec_probe bulk concurrency, so behavior is consistent.
def _conc_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return max(1, min(n, 32))


_INDEX_CONCURRENCY = _conc_from_env("INDEX_CONCURRENCY", 3)
_index_sem: Optional[asyncio.Semaphore] = None


def _semaphore() -> asyncio.Semaphore:
    """Lazy-init the semaphore on first use — the indexer is imported
    at module load before the asyncio loop exists, so we can't build
    it at top level."""
    global _index_sem
    if _index_sem is None:
        _index_sem = asyncio.Semaphore(_INDEX_CONCURRENCY)
    return _index_sem

from pyrogram import Client
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import Message

from main.utils.file_properties import get_hash, get_media_file_unique_id
from main.utils.hub_query import ExternalSubtitle
from main.utils.human_readable import humanbytes
from main.utils.index_entry import (
    IndexEntry,
    render,
    title_from_filename,
    year_from_filename,
)
from main.utils.subtitles import (
    derive_label,
    is_subtitle_filename,
    is_subtitle_mime,
    language_from_filename,
    stem_for_pairing,
)


# Media types worth cataloguing for the hub. Photos, stickers, voice notes
# pass through the bot but aren't library content.
_INDEXABLE_ATTRS = ("video", "document", "animation", "audio")


def _indexable_media(message: Message):
    for attr in _INDEXABLE_ATTRS:
        media = getattr(message, attr, None)
        if media is None:
            continue
        mime = (getattr(media, "mime_type", "") or "").lower()
        # Generic documents (zips, pdfs, etc.) shouldn't end up in the hub —
        # accept documents only when their MIME type is video or audio.
        if attr == "document" and not (
            mime.startswith("video/") or mime.startswith("audio/")
        ):
            continue
        return media
    return None


def _build_caption(message: Message) -> Optional[str]:
    media = _indexable_media(message)
    if media is None:
        return None
    file_name = getattr(media, "file_name", None) or ""
    file_size = getattr(media, "file_size", 0) or 0

    entry = IndexEntry(
        title=title_from_filename(file_name),
        year=year_from_filename(file_name),
        description=humanbytes(file_size) if file_size else "",
    )
    # No FileVariant entries: in the single-channel layout, the message IS
    # the file. Hub URLs are built from message.id directly. The parser's
    # files list stays empty.
    return render(entry)


async def index_bin_message(bot: Client, bin_msg: Message) -> None:
    """Edit a BIN_CHANNEL message's caption into the structured index format,
    then register the entry in the in-process catalogue used by the hub.

    Bounded by ``_INDEX_CONCURRENCY`` (env ``INDEX_CONCURRENCY``, default 3)
    so a burst of parallel uploads doesn't FloodWait Telegram or stack
    ffprobe subprocesses.
    """
    async with _semaphore():
        await _index_bin_message_impl(bot, bin_msg)


async def _index_bin_message_impl(bot: Client, bin_msg: Message) -> None:
    from main.utils import media_index

    caption = _build_caption(bin_msg)
    if caption is None:
        return
    # Mongo holds the canonical catalogue now — the BIN caption is
    # redundant cosmetic round-tripping. Skip the Telegram edit so we
    # don't FloodWait or burn quota on it. The add_from_message call
    # below uses the file's original caption (or filename) and writes
    # the structured fields to Mongo, which is all the hub needs.
    if media_index._store_active():
        logging.debug(
            "indexer: bin:%d caption-write skipped (Mongo active)",
            bin_msg.id,
        )
    else:
        try:
            await bot.edit_message_caption(
                chat_id=bin_msg.chat.id,
                message_id=bin_msg.id,
                caption=caption,
            )
        except MessageNotModified:
            pass
        except FloodWait as e:
            wait = getattr(e, "value", None) or getattr(e, "x", 0)
            logging.warning("FloodWait editing bin:%d — sleeping %ss", bin_msg.id, wait)
            await asyncio.sleep(wait)
            # Recurse on the impl (NOT the public function) so we don't
            # re-acquire the semaphore from inside the same task — that
            # would deadlock against the slot we're already holding.
            await _index_bin_message_impl(bot, bin_msg)
            return
        except Exception:
            logging.exception("Failed to index bin:%d", bin_msg.id)

    # Re-fetch the message so the caption we just wrote is the one that
    # gets parsed into the index entry. Even when the caption-edit
    # above failed (forwarded messages can't be edited, channel posts
    # the bot doesn't own, etc.) we still want the entry in the
    # catalogue — the read-side falls back to filename-derived title.
    try:
        fresh = await bot.get_messages(bin_msg.chat.id, bin_msg.id)
        await media_index.add_from_message(fresh)
    except Exception:
        logging.debug("media_index add failed for bin:%d", bin_msg.id, exc_info=True)
        return

    # Snapshot the catalogue so the new entry survives a Koyeb restart
    # even when TMDB never matches (enrich_one is the only other code
    # path that snapshots, and it returns early on no-match). Coalesced
    # via the debouncer so a 70-episode burst is one upload, not 70.
    media_index.schedule_snapshot(bot)

    # AWAIT (not fire-and-forget) the TMDB enrichment so the
    # indexer's semaphore slot also bounds parallel enrichments —
    # otherwise 10 simultaneous uploads schedule 10 simultaneous
    # TMDB lookups + 10 simultaneous caption write-backs, blowing
    # past Telegram's edit-message rate limit. With the semaphore,
    # at most _INDEX_CONCURRENCY enrichments run at once.
    # tmdb._locks already serialises same-(title,year) calls so
    # multiple episodes of one series share the network round-trip.
    try:
        from main.utils import tmdb
        if tmdb.is_configured():
            await media_index.enrich_one(bin_msg.id, bot=bot)
    except Exception:
        logging.debug("enrichment failed for bin:%d", bin_msg.id,
                      exc_info=True)

    # Fire-and-forget ffprobe so the watch page knows whether this
    # file is browser-playable (h264/8-bit ✓, hevc/10-bit/av1 ✗)
    # without waiting for the user to hit play and fail.
    try:
        from main.utils import codec_probe
        codec_probe.schedule_probe(bin_msg.id)
    except Exception:
        logging.debug("codec probe schedule failed for bin:%d", bin_msg.id,
                      exc_info=True)


def schedule_index(bot: Client, bin_msg: Message) -> None:
    """Fire-and-forget. Caller (the forward handlers) gets to reply with the
    stream link immediately while caption editing happens in the background."""
    asyncio.create_task(index_bin_message(bot, bin_msg))


def _subtitle_document(message: Message):
    """Return the document if this is a subtitle sidecar, else None."""
    doc = getattr(message, "document", None)
    if doc is None:
        return None
    file_name = getattr(doc, "file_name", "") or ""
    mime = (getattr(doc, "mime_type", "") or "").lower()
    if is_subtitle_filename(file_name):
        return doc
    if is_subtitle_mime(mime) and file_name:
        # Text/plain alone is too generous; require a recognisable filename.
        return doc
    return None


async def pair_subtitle(bin_msg: Message, source_msg: Optional[Message]) -> Optional[int]:
    """If ``bin_msg`` is a subtitle sidecar, attach it to a previously-indexed
    video. Returns the target video's BIN message id on success, None otherwise.

    Pairing strategy (in order):
      1. ``source_msg.reply_to_message`` (user replied to their own video DM)
         — match by file_unique_id[:6].
      2. Filename stem match against existing HubItems.
    """
    from main.utils import media_index

    doc = _subtitle_document(bin_msg)
    if doc is None:
        return None

    file_name = getattr(doc, "file_name", "") or ""

    target = None
    reply = getattr(source_msg, "reply_to_message", None) if source_msg else None
    if reply is not None:
        reply_uid = (get_media_file_unique_id(reply) or "")[:6]
        if reply_uid:
            target = media_index.find_by_hash(reply_uid)

    if target is None:
        target = media_index.find_by_filename_stem(stem_for_pairing(file_name))

    if target is None:
        logging.info("subtitle bin:%d uploaded but no matching video found",
                     bin_msg.id)
        return None

    language = language_from_filename(file_name)
    sub = ExternalSubtitle(
        bin_message_id=bin_msg.id,
        secure_hash=get_hash(bin_msg),
        language=language,
        label=derive_label(language, file_name),
    )
    ok = await media_index.attach_subtitle(target.message_id, sub)
    if ok:
        logging.info(
            "subtitle bin:%d paired with video bin:%d (lang=%s)",
            bin_msg.id, target.message_id, language or "?",
        )
        return target.message_id
    return None


def schedule_subtitle_pairing(bot: Client, bin_msg: Message,
                              source_msg: Optional[Message]) -> None:
    """Fire-and-forget background pairing for sidecar uploads."""
    asyncio.create_task(pair_subtitle(bin_msg, source_msg))
