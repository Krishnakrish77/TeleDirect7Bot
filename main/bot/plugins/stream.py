import asyncio
import logging
from main.bot import StreamBot
from main.utils import media_index
from main.utils.download_urls import as_download_url
from main.utils.file_properties import gen_link, get_hash, get_media_from_message
from main.utils.indexer import schedule_index, schedule_subtitle_pairing
from main.utils.subtitles import is_subtitle_filename, is_subtitle_mime
from main.vars import Var
from pyrogram import filters, Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton


def _from_admin(m: Message) -> bool:
    user = getattr(m, "from_user", None)
    try:
        return bool(user and int(user.id) == int(Var.OWNER_ID))
    except (TypeError, ValueError):
        return False


def _schedule_index_if_admin(bot: Client, source_msg: Message, bin_msg: Message) -> None:
    """Only admin-added files belong in the public media hub.

    Non-admin uploads still get stream links; they just stay out of the
    catalogue so the hub remains an operator-curated library.
    """
    if _from_admin(source_msg):
        schedule_index(bot, bin_msg)
        return
    logging.info(
        "media_index: skipped non-admin upload bin:%s from chat:%s user:%s",
        getattr(bin_msg, "id", "?"),
        getattr(getattr(source_msg, "chat", None), "id", "?"),
        getattr(getattr(source_msg, "from_user", None), "id", "?"),
    )


_pending_bin_copies: dict[tuple[str, int], asyncio.Future] = {}
_pending_bin_copies_lock = asyncio.Lock()
_COPY_RESERVATION_TTL = 300


def _exact_upload_key(m: Message) -> tuple[str, int] | None:
    media = get_media_from_message(m)
    if media is None:
        return None
    secure_hash = get_hash(m)
    try:
        file_size = int(getattr(media, "file_size", 0) or 0)
    except (TypeError, ValueError):
        file_size = 0
    if not secure_hash or not file_size:
        return None
    return secure_hash, file_size


async def _fetch_existing_bin_message(bot: Client, key: tuple[str, int]) -> Message | None:
    existing = media_index.find_exact_upload(*key)
    if existing is None:
        return None
    try:
        msg = await bot.get_messages(Var.BIN_CHANNEL, existing.message_id)
    except Exception:
        logging.debug(
            "upload dedupe: failed to fetch existing bin:%d",
            existing.message_id,
            exc_info=True,
        )
        return None
    if getattr(msg, "empty", False):
        return None
    return msg


async def _copy_or_reuse_bin_message(bot: Client, source_msg: Message) -> tuple[Message, bool]:
    """Copy media to BIN_CHANNEL unless the exact file already exists.

    Multi-file Telegram forwards arrive as parallel updates. Without the
    pending-copy reservation below, two identical forwarded files can both pass
    the catalogue lookup before either background index task has updated
    ``media_index``. The reservation lets later updates reuse the first BIN
    message and keeps the channel/catalogue from growing duplicate uploads.
    """
    key = _exact_upload_key(source_msg)
    if key is None:
        return await source_msg.copy(chat_id=Var.BIN_CHANNEL), False

    while True:
        existing = await _fetch_existing_bin_message(bot, key)
        if existing is not None:
            logging.info(
                "upload dedupe: reusing existing bin:%d for hash=%s size=%d",
                existing.id,
                key[0],
                key[1],
            )
            return existing, True

        loop = asyncio.get_running_loop()
        async with _pending_bin_copies_lock:
            pending = _pending_bin_copies.get(key)
            if pending is None:
                pending = loop.create_future()
                _pending_bin_copies[key] = pending
                creator = True
            else:
                creator = False

        if not creator:
            try:
                msg = await pending
                logging.info(
                    "upload dedupe: reused pending bin:%d for hash=%s size=%d",
                    getattr(msg, "id", 0),
                    key[0],
                    key[1],
                )
                return msg, True
            except Exception:
                # The leading copy failed; loop around and either find a now
                # indexed item or become the new copy owner.
                continue

        copied = False
        try:
            log_msg = await source_msg.copy(chat_id=Var.BIN_CHANNEL)
            copied = True
            if not pending.done():
                pending.set_result(log_msg)
            asyncio.create_task(_clear_copy_reservation_later(key, pending))
            return log_msg, False
        except Exception as exc:
            if not pending.done():
                pending.set_exception(exc)
                pending.add_done_callback(lambda fut: fut.exception())
            raise
        finally:
            if not copied:
                async with _pending_bin_copies_lock:
                    if _pending_bin_copies.get(key) is pending:
                        _pending_bin_copies.pop(key, None)


async def _clear_copy_reservation_later(
    key: tuple[str, int],
    pending: asyncio.Future,
) -> None:
    await asyncio.sleep(_COPY_RESERVATION_TTL)
    async with _pending_bin_copies_lock:
        if _pending_bin_copies.get(key) is pending:
            _pending_bin_copies.pop(key, None)


@StreamBot.on_deleted_messages(filters.channel)
async def bin_message_deleted(client: Client, messages):
    """Prune the catalogue when messages are deleted from BIN_CHANNEL.

    Uses filters.channel (checks chat.type == CHANNEL only) rather than
    filters.chat(BIN_CHANNEL): the minimal Chat stub built by kurigram
    for deleted-message updates only has id+type, and filters.chat() does
    a set-membership check that silently fails on type mismatches between
    the string env-var and the integer chat.id.

    Pattern from Telegram-Stremio (weebzone/Telegram-Stremio): use
    filters.channel to gate on channel updates, then guard the specific
    channel with an explicit int comparison inside the handler.
    """
    bin_id = int(Var.BIN_CHANNEL)
    removed = 0
    for msg in messages:
        chat = getattr(msg, "chat", None)
        if getattr(chat, "id", None) != bin_id:
            continue
        try:
            mid = int(getattr(msg, "id", 0) or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        if media_index.get_item(mid) is None:
            continue
        await media_index.remove(mid, bot=client)
        removed += 1
    if removed:
        logging.info("media_index: pruned %d entries on BIN deletion", removed)


def _looks_like_subtitle(m: Message) -> bool:
    doc = getattr(m, "document", None)
    if doc is None:
        return False
    file_name = getattr(doc, "file_name", "") or ""
    if is_subtitle_filename(file_name):
        return True
    mime = (getattr(doc, "mime_type", "") or "").lower()
    return is_subtitle_mime(mime) and bool(file_name)


@StreamBot.on_message(
    filters.private
    & ~filters.user(Var.BANNED_USERS) & (
        filters.document
        | filters.video
        | filters.audio
        | filters.animation
        | filters.voice
        | filters.video_note
        | filters.photo
        | filters.sticker
    ),
    group=4,
)
async def private_receive_handler(c: Client, m: Message):
    try:
        # Use copy() not forward(): forwarded messages carry a visible
        # "Forwarded from" header which makes their captions
        # non-editable even by the bot that did the forwarding. Copy
        # reposts as a fresh bot-authored message, keeping admin
        # re-enrichment / caption rewrites working.
        if _looks_like_subtitle(m):
            log_msg = await m.copy(chat_id=Var.BIN_CHANNEL)
            schedule_subtitle_pairing(c, log_msg, m)
            await m.reply_text(
                text="📝 Subtitle saved. I'll attach it to the matching video.",
                quote=True,
            )
            return

        log_msg, reused_bin = await _copy_or_reuse_bin_message(c, m)
        if not reused_bin:
            _schedule_index_if_admin(c, m, log_msg)
        reply_markup, Stream_Text, stream_link = await gen_link(m=m, log_msg=log_msg, from_channel=reused_bin)
        if not reused_bin:
            await log_msg.reply_text(text=f"**Requested By :** [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n**User ID :** `{m.from_user.id}`\n**Download Link :** {stream_link}", disable_web_page_preview=True, quote=True)

        await m.reply_text(
            text=Stream_Text,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            quote=True
        )
    except FloodWait as e:
        # Log it; don't echo a notice into BIN_CHANNEL. The channel is
        # the catalogue stream — operator notices belong in logs.
        logging.warning(
            "FloodWait %ss handling upload from user %s",
            e.x, getattr(m.from_user, "id", "?"),
        )
        await asyncio.sleep(e.x)

@StreamBot.on_message(filters.channel & ~filters.user(Var.BANNED_USERS) & (filters.document | filters.video), group=-1)
async def channel_receive_handler(bot, broadcast: Message):
    if int(broadcast.chat.id) in Var.BANNED_CHANNELS:
        await bot.leave_chat(broadcast.chat.id)
        return
    try:
        # See private_receive_handler — copy keeps the bin caption
        # editable. The reply-text below still carries the source
        # channel attribution, so we don't lose that context.
        log_msg, reused_bin = await _copy_or_reuse_bin_message(bot, broadcast)
        if not reused_bin:
            _schedule_index_if_admin(bot, broadcast, log_msg)
        file_hash = get_hash(log_msg)
        stream_link = as_download_url(f"{Var.URL}{file_hash}{log_msg.id}")
        if not reused_bin:
            await log_msg.reply_text(
                text=f"**Channel Name:** `{broadcast.chat.title}`\n**Channel ID:** `{broadcast.chat.id}`\n**Request URL:** https://t.me/{(await bot.get_me()).username}?start=msgid_{str(log_msg.id)}",
                quote=True,
            )
        # Best-effort: try to attach an inline "Download Link" button to
        # the source-channel message. This only works when the bot
        # itself authored the post or has explicit edit rights in the
        # source channel; the common case is a user-uploaded post we
        # have no edit permission on, which Telegram surfaces as
        # MESSAGE_ID_INVALID. Streaming + forward to BIN_CHANNEL have
        # already succeeded, so swallow the failure rather than
        # logging it at ERROR or echoing it into BIN.
        try:
            from pyrogram.errors.exceptions.bad_request_400 import (
                MessageIdInvalid,
            )
        except ImportError:
            MessageIdInvalid = None
        try:
            await bot.edit_message_reply_markup(
                chat_id=broadcast.chat.id,
                message_id=broadcast.id,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Download Link 📥", url=stream_link)]]),
            )
        except Exception as edit_exc:
            if MessageIdInvalid is not None and isinstance(edit_exc, MessageIdInvalid):
                logging.debug(
                    "channel_receive: skipping reply-markup on %s/%d (bot doesn't own that message)",
                    broadcast.chat.title, broadcast.id,
                )
            else:
                logging.warning(
                    "channel_receive: edit_message_reply_markup failed for %s/%d: %s",
                    broadcast.chat.title, broadcast.id, edit_exc,
                )
    except FloodWait as w:
        logging.warning(
            "FloodWait %ss handling channel upload from %s/%d",
            w.x, broadcast.chat.title, broadcast.chat.id,
        )
        await asyncio.sleep(w.x)
    except Exception as e:
        # Real failure of the forward / reply flow — log it, but don't
        # spam BIN_CHANNEL with a traceback message. That was leaving a
        # pile of #ᴇʀʀᴏʀ_ᴛʀᴀᴄᴇʙᴀᴄᴋ messages in the catalogue channel.
        logging.exception(
            "channel_receive_handler failed for %s/%d",
            broadcast.chat.title, broadcast.id,
        )

@StreamBot.on_message(filters.group & ~filters.user(Var.BANNED_USERS) & (filters.document | filters.video | filters.audio), group=4)
async def group_receive_handler(c: Client, m: Message):
    try:
        # See private_receive_handler — copy keeps captions editable.
        log_msg, reused_bin = await _copy_or_reuse_bin_message(c, m)
        if not reused_bin:
            _schedule_index_if_admin(c, m, log_msg)
        reply_markup, Stream_Text, stream_link = await gen_link(m=m, log_msg=log_msg, from_channel=True)
        if not reused_bin:
            await log_msg.reply_text(text=f"**Requested By :** [{m.chat.title}](https://t.me/{m.chat.username or ''})\n**Group ID :** `{m.chat.id}`\n**Download Link :** {stream_link}", disable_web_page_preview=True, quote=True)

        await m.reply_text(
            text=Stream_Text,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            quote=True
        )
    except FloodWait as e:
        logging.warning(
            "FloodWait %ss handling group upload from %s/%d",
            e.x, m.chat.title, m.chat.id,
        )
        await asyncio.sleep(e.x)
