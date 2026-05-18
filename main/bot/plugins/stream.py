import asyncio
import logging
from main.bot import StreamBot
from main.utils import media_index
from main.utils.file_properties import gen_link
from main.utils.indexer import schedule_index, schedule_subtitle_pairing
from main.utils.subtitles import is_subtitle_filename, is_subtitle_mime
from main.vars import Var
from pyrogram import filters, Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton


@StreamBot.on_deleted_messages(filters.chat(Var.BIN_CHANNEL))
async def bin_message_deleted(_client: Client, messages):
    """Prune the catalogue when messages are deleted from BIN_CHANNEL.

    Telegram pushes a deletion event whenever a message in BIN_CHANNEL
    is removed — by an admin via a Telegram client, by our own bulk
    delete, or by anything else. Reacting here keeps the hub honest
    without any periodic-sweep cost: the row disappears on the next
    page refresh.
    """
    removed = 0
    for msg in messages:
        try:
            mid = int(getattr(msg, "id", 0) or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        if media_index.get_item(mid) is None:
            continue
        await media_index.remove(mid)
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
        log_msg = await m.forward(chat_id=Var.BIN_CHANNEL)

        if _looks_like_subtitle(m):
            schedule_subtitle_pairing(c, log_msg, m)
            await m.reply_text(
                text="📝 Subtitle saved. I'll attach it to the matching video.",
                quote=True,
            )
            return

        schedule_index(c, log_msg)
        reply_markup, Stream_Text, stream_link = await gen_link(m=m, log_msg=log_msg, from_channel=False)
        await log_msg.reply_text(text=f"**Requested By :** [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n**User ID :** `{m.from_user.id}`\n**Download Link :** {stream_link}", disable_web_page_preview=True, quote=True)

        await m.reply_text(
            text=Stream_Text,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            quote=True
        )
    except FloodWait as e:
        logging.warning(f"Sleeping for {e.x}s")
        await asyncio.sleep(e.x)
        await c.send_message(chat_id=Var.BIN_CHANNEL, text=f"Got Floodwait Of {str(e.x)}s from [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n\n**User ID :** `{str(m.from_user.id)}`", disable_web_page_preview=True,)

@StreamBot.on_message(filters.channel & ~filters.user(Var.BANNED_USERS) & (filters.document | filters.video), group=-1)
async def channel_receive_handler(bot, broadcast: Message):
    if int(broadcast.chat.id) in Var.BANNED_CHANNELS:
        await bot.leave_chat(broadcast.chat.id)
        return
    try:
        log_msg = await broadcast.forward(chat_id=Var.BIN_CHANNEL)
        schedule_index(bot, log_msg)
        stream_link = "https://{}/{}".format(Var.FQDN, log_msg.id) if Var.ON_HEROKU or Var.NO_PORT else \
            "http://{}:{}/{}".format(Var.FQDN,
                                    Var.PORT,
                                    log_msg.id)
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
        logging.warning(f"Sleeping for {w.x}s")
        await asyncio.sleep(w.x)
        await bot.send_message(chat_id=Var.BIN_CHANNEL,
                             text=f"Got Floodwait Of {str(w.x)}s from {broadcast.chat.title}\n\n**Channel ID:** `{str(broadcast.chat.id)}`",
                             disable_web_page_preview=True,)
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
        log_msg = await m.forward(chat_id=Var.BIN_CHANNEL)
        schedule_index(c, log_msg)
        reply_markup, Stream_Text, stream_link = await gen_link(m=m, log_msg=log_msg, from_channel=True)
        await log_msg.reply_text(text=f"**Requested By :** [{m.chat.title}](https://t.me/{m.chat.username or ''})\n**Group ID :** `{m.chat.id}`\n**Download Link :** {stream_link}", disable_web_page_preview=True, quote=True)

        await m.reply_text(
            text=Stream_Text,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
            quote=True
        )
    except FloodWait as e:
        logging.warning(f"Sleeping for {e.x}s")
        await asyncio.sleep(e.x)
        await c.send_message(chat_id=Var.BIN_CHANNEL, text=f"Got Floodwait Of {str(e.x)}s in group `{m.chat.title}` ({m.chat.id})", disable_web_page_preview=True)

