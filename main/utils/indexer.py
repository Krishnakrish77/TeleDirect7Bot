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

from main.utils.human_readable import humanbytes
from main.utils.index_entry import IndexEntry, render, title_from_filename


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
        description=humanbytes(file_size) if file_size else "",
    )
    # No FileVariant entries: in the single-channel layout, the message IS
    # the file. Hub URLs are built from message.id directly. The parser's
    # files list stays empty.
    return render(entry)


async def index_bin_message(bot: Client, bin_msg: Message) -> None:
    """Edit a BIN_CHANNEL message's caption into the structured index format.
    No-op for non-indexable messages or if the caption is already correct."""
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
    except Exception:
        logging.exception("Failed to index bin:%d", bin_msg.id)


def schedule_index(bot: Client, bin_msg: Message) -> None:
    """Fire-and-forget. Caller (the forward handlers) gets to reply with the
    stream link immediately while caption editing happens in the background."""
    asyncio.create_task(index_bin_message(bot, bin_msg))
