import asyncio
import logging
import re
from typing import Optional, Tuple, Union

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from main.bot import StreamBot
from main.utils.file_properties import gen_link, get_media_from_message
from main.utils.human_readable import humanbytes
from main.utils.indexer import schedule_index
from main.vars import Var

logger = logging.getLogger(__name__)

PAGE_SIZE = 8          # media items shown per page
SCAN_LIMIT = 200       # max messages to scan per page to find PAGE_SIZE media items

_user_client: Optional[Client] = None
_user_client_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# User client helpers
# ---------------------------------------------------------------------------

async def _get_user_client() -> Client:
    global _user_client
    async with _user_client_lock:
        if _user_client is None:
            if not Var.USER_SESSION:
                raise RuntimeError("USER_SESSION is not configured in .env")
            _user_client = await Client(
                name=":memory:",
                api_id=Var.API_ID,
                api_hash=Var.API_HASH,
                session_string=Var.USER_SESSION,
                no_updates=True,
            ).start()
            logger.info("grab: user client started")
    return _user_client


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_grab_args(text: str) -> Tuple[Union[int, str], int]:
    """Return (chat, message_id). chat is int peer ID or str username."""
    text = text.strip()

    m = re.match(r"https?://t\.me/c/(\d+)/(\d+)", text)
    if m:
        return int(f"-100{m.group(1)}"), int(m.group(2))

    m = re.match(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)", text)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"@?([A-Za-z][A-Za-z0-9_]{3,})\s+(\d+)$", text)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"(-?\d+)\s+(\d+)$", text)
    if m:
        return int(m.group(1)), int(m.group(2))

    raise ValueError(
        "Cannot parse. Use one of:\n"
        "  `/grab https://t.me/channel/123`\n"
        "  `/grab @channel 123`\n"
        "  `/grab -100xxx 123`"
    )


def _parse_channel_arg(text: str) -> Union[int, str]:
    """Parse just a channel from /grablist args."""
    text = text.strip()

    m = re.match(r"https?://t\.me/c/(\d+)(?:/\d+)?", text)
    if m:
        return int(f"-100{m.group(1)}")

    m = re.match(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})", text)
    if m:
        return m.group(1)

    m = re.match(r"@?([A-Za-z][A-Za-z0-9_]{3,})$", text)
    if m:
        return m.group(1)

    m = re.match(r"(-?\d+)$", text)
    if m:
        return int(m.group(1))

    raise ValueError(
        "Cannot parse channel. Use:\n"
        "  `/grablist @channel`\n"
        "  `/grablist https://t.me/channel`\n"
        "  `/grablist -100xxx`"
    )


# ---------------------------------------------------------------------------
# Re-upload helper
# ---------------------------------------------------------------------------

async def _reupload(src: Message) -> Message:
    """Download src via user client, re-upload to BIN_CHANNEL via StreamBot."""
    user = await _get_user_client()
    buf = await user.download_media(src, in_memory=True)
    buf.seek(0)

    media = get_media_from_message(src)
    file_name = getattr(media, "file_name", None) or "file"
    caption = src.caption or ""

    if src.video:
        return await StreamBot.send_video(
            Var.BIN_CHANNEL, buf,
            file_name=file_name, caption=caption, supports_streaming=True,
        )
    if src.audio:
        return await StreamBot.send_audio(
            Var.BIN_CHANNEL, buf, file_name=file_name, caption=caption,
        )
    return await StreamBot.send_document(
        Var.BIN_CHANNEL, buf, file_name=file_name, caption=caption,
    )


# ---------------------------------------------------------------------------
# Grablist page builder
# ---------------------------------------------------------------------------

async def _build_page(chat_id: int, offset_id: int):
    """
    Scan up to SCAN_LIMIT messages from chat_id starting before offset_id.
    Returns (media_msgs, next_offset_id, has_more).
    next_offset_id is 0 when there are no more messages.
    """
    user = await _get_user_client()
    media_msgs = []
    last_id = 0

    kwargs = {"limit": SCAN_LIMIT}
    if offset_id:
        kwargs["offset_id"] = offset_id

    async for msg in user.get_chat_history(chat_id, **kwargs):
        last_id = msg.id
        if get_media_from_message(msg):
            media_msgs.append(msg)
        if len(media_msgs) >= PAGE_SIZE:
            break

    # has_more is True only if we hit PAGE_SIZE and there may be older messages
    has_more = len(media_msgs) >= PAGE_SIZE and last_id > 0
    return media_msgs, last_id, has_more


def _file_button_label(msg: Message) -> str:
    media = get_media_from_message(msg)
    name = getattr(media, "file_name", None) or f"[{msg.id}]"
    size = getattr(media, "file_size", 0) or 0
    size_str = humanbytes(size) if size else "?"
    max_name = 44 - len(size_str)
    if len(name) > max_name:
        name = name[: max_name - 1] + "…"
    return f"🔽 {name} · {size_str}"


def _build_markup(chat_id: int, media_msgs, next_offset: int, has_more: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            _file_button_label(msg),
            callback_data=f"grabdo_{chat_id}_{msg.id}",
        )]
        for msg in media_msgs
    ]
    if has_more:
        rows.append([
            InlineKeyboardButton(
                "Load More ▶",
                callback_data=f"grablist_{chat_id}_{next_offset}",
            )
        ])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# /grab — single message
# ---------------------------------------------------------------------------

@StreamBot.on_message(
    filters.private & filters.user(Var.OWNER_ID) & filters.command("grab"),
    group=1,
)
async def grab_handler(client: Client, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        await m.reply_text(
            "Usage:\n"
            "  `/grab https://t.me/channel/123`\n"
            "  `/grab @channel 123`\n"
            "  `/grab -100xxx 123`",
            quote=True,
        )
        return

    if not Var.USER_SESSION:
        await m.reply_text(
            "Set `USER_SESSION` in .env and restart first.",
            quote=True,
        )
        return

    status = await m.reply_text("Fetching…", quote=True)
    try:
        chat, msg_id = _parse_grab_args(parts[1])
        user = await _get_user_client()
        src = await user.get_messages(chat, msg_id)
        if src.empty or not get_media_from_message(src):
            await status.edit_text("Message not found or contains no media.")
            return

        await status.edit_text("Downloading and re-uploading…")
        log_msg = await _reupload(src)
        schedule_index(client, log_msg)

        await log_msg.reply_text(
            text=f"**Grabbed by:** [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n"
                 f"**Source:** `{chat}` / msg `{msg_id}`",
            disable_web_page_preview=True,
            quote=True,
        )
        reply_markup, stream_text, _ = await gen_link(m=log_msg, log_msg=log_msg, from_channel=False)
        await status.edit_text(stream_text, disable_web_page_preview=True, reply_markup=reply_markup)

    except ValueError as e:
        await status.edit_text(str(e))
    except FloodWait as e:
        await status.edit_text(f"FloodWait — retry after {e.x}s")
        await asyncio.sleep(e.x)
    except Exception as e:
        logger.exception("grab_handler failed")
        await status.edit_text(f"Error: {e}")


# ---------------------------------------------------------------------------
# /grablist — browse media in a protected channel
# ---------------------------------------------------------------------------

@StreamBot.on_message(
    filters.private & filters.user(Var.OWNER_ID) & filters.command("grablist"),
    group=1,
)
async def grablist_handler(client: Client, m: Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        await m.reply_text(
            "Usage:\n"
            "  `/grablist @channel`\n"
            "  `/grablist https://t.me/channel`\n"
            "  `/grablist -100xxx`",
            quote=True,
        )
        return

    if not Var.USER_SESSION:
        await m.reply_text("Set `USER_SESSION` in .env and restart first.", quote=True)
        return

    status = await m.reply_text("Scanning channel…", quote=True)
    try:
        chat = _parse_channel_arg(parts[1])
        user = await _get_user_client()

        # Resolve to numeric ID so callback data stays compact
        chat_obj = await user.get_chat(chat)
        chat_id = chat_obj.id
        chat_title = getattr(chat_obj, "title", str(chat_id))

        media_msgs, next_offset, has_more = await _build_page(chat_id, offset_id=0)
        if not media_msgs:
            await status.edit_text("No media found in this channel.")
            return

        markup = _build_markup(chat_id, media_msgs, next_offset, has_more)
        await status.edit_text(
            f"**{chat_title}** — tap a file to grab it:",
            reply_markup=markup,
        )

    except ValueError as e:
        await status.edit_text(str(e))
    except Exception as e:
        logger.exception("grablist_handler failed")
        await status.edit_text(f"Error: {e}")


# ---------------------------------------------------------------------------
# Callbacks: grablist pagination + grabdo execution
# ---------------------------------------------------------------------------

_owner_filter = filters.user(Var.OWNER_ID)


@StreamBot.on_callback_query(filters.regex(r"^grablist_") & _owner_filter)
async def grablist_cb(client: Client, cb: CallbackQuery):
    _, chat_id_str, offset_str = cb.data.split("_", 2)
    chat_id = int(chat_id_str)
    offset_id = int(offset_str)

    await cb.answer("Loading…")
    try:
        media_msgs, next_offset, has_more = await _build_page(chat_id, offset_id)
        if not media_msgs:
            await cb.message.edit_text("No more media found.")
            return

        markup = _build_markup(chat_id, media_msgs, next_offset, has_more)
        # Keep same header text, just swap the keyboard
        await cb.message.edit_reply_markup(reply_markup=markup)

    except Exception as e:
        logger.exception("grablist_cb failed")
        await cb.answer(f"Error: {e}", show_alert=True)


@StreamBot.on_callback_query(filters.regex(r"^grabdo_") & _owner_filter)
async def grabdo_cb(client: Client, cb: CallbackQuery):
    _, chat_id_str, msg_id_str = cb.data.split("_", 2)
    chat_id = int(chat_id_str)
    msg_id = int(msg_id_str)

    await cb.answer("Starting grab…")
    progress = await cb.message.reply_text(
        f"⬇️ Downloading msg `{msg_id}`…",
        quote=True,
    )
    try:
        user = await _get_user_client()
        src = await user.get_messages(chat_id, msg_id)
        if src.empty or not get_media_from_message(src):
            await progress.edit_text("Message not found or has no media.")
            return

        media = get_media_from_message(src)
        file_name = getattr(media, "file_name", None) or f"[{msg_id}]"
        await progress.edit_text(f"⬆️ Re-uploading **{file_name}**…")

        log_msg = await _reupload(src)
        schedule_index(client, log_msg)

        await log_msg.reply_text(
            text=f"**Grabbed via list** | source msg `{chat_id}/{msg_id}`",
            disable_web_page_preview=True,
            quote=True,
        )
        reply_markup, stream_text, _ = await gen_link(m=log_msg, log_msg=log_msg, from_channel=False)
        await progress.edit_text(stream_text, disable_web_page_preview=True, reply_markup=reply_markup)

    except FloodWait as e:
        await progress.edit_text(f"FloodWait — retry after {e.x}s")
        await asyncio.sleep(e.x)
    except Exception as e:
        logger.exception("grabdo_cb failed")
        await progress.edit_text(f"Error: {e}")
