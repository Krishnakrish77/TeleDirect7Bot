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
from typing import Optional

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
_INDEXABLE_ATTRS = ("video", "document", "animation")


def _indexable_media(message: Message):
    for attr in _INDEXABLE_ATTRS:
        media = getattr(message, attr, None)
        if media is None:
            continue
        # Generic documents (zips, pdfs, etc.) shouldn't end up in the hub —
        # accept documents only when their MIME type is a video.
        mime = (getattr(media, "mime_type", "") or "").lower()
        if attr == "document" and not mime.startswith("video/"):
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
    then register the entry in the in-process catalogue used by the hub."""
    from main.utils import media_index

    caption = _build_caption(bin_msg)
    if caption is None:
        return
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
        await index_bin_message(bot, bin_msg)
        return
    except Exception:
        logging.exception("Failed to index bin:%d", bin_msg.id)

    # Re-fetch the message so the caption we just wrote is the one that
    # gets parsed into the index entry.
    try:
        fresh = await bot.get_messages(bin_msg.chat.id, bin_msg.id)
        await media_index.add_from_message(fresh)
    except Exception:
        logging.debug("media_index add failed for bin:%d", bin_msg.id, exc_info=True)
        return

    # Fire-and-forget TMDB enrichment. The lookup is cached per
    # (title, year) so multiple episodes of the same series share one
    # network request. Pass `bot` so the canonical metadata gets written
    # back to the BIN caption when a match lands — that makes the
    # Telegram channel the durable source of truth.
    try:
        from main.utils import tmdb
        if tmdb.is_configured():
            asyncio.create_task(media_index.enrich_one(bin_msg.id, bot=bot))
    except Exception:
        logging.debug("enrichment schedule failed for bin:%d", bin_msg.id,
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
