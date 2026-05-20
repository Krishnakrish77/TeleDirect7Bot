import asyncio
import logging
import os
import re
import tempfile
from typing import Dict, Optional, Set, Tuple, Union

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
                name="grab_user",
                api_id=Var.USER_API_ID or Var.API_ID,
                api_hash=Var.USER_API_HASH or Var.API_HASH,
                session_string=Var.USER_SESSION,  # implies in_memory=True
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
    """Download src via user client to a temp file, re-upload via StreamBot.

    Temp file avoids loading the whole file into RAM (in_memory=True caused
    OOM on large files — an 800 MB file = 800 MB heap spike).
    """
    user = await _get_user_client()
    media = get_media_from_message(src)
    file_name = getattr(media, "file_name", None) or "file"
    caption = src.caption or ""

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=f"_grab_{file_name}")
    os.close(tmp_fd)
    try:
        await user.download_media(src, file_name=tmp_path)

        if src.video:
            return await StreamBot.send_video(
                Var.BIN_CHANNEL, tmp_path,
                file_name=file_name, caption=caption, supports_streaming=True,
            )
        if src.audio:
            return await StreamBot.send_audio(
                Var.BIN_CHANNEL, tmp_path, file_name=file_name, caption=caption,
            )
        return await StreamBot.send_document(
            Var.BIN_CHANNEL, tmp_path, file_name=file_name, caption=caption,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Grablist page builder + selection state
# ---------------------------------------------------------------------------

# (user_id, bot_message_id) → set of source message IDs selected
_selections: Dict[Tuple[int, int], set] = {}


async def _build_page(chat_id: int, skip: int):
    """
    Scan up to SCAN_LIMIT messages from chat_id, skipping the first `skip`
    messages (newest-first). Returns (media_msgs, next_skip, has_more).
    Uses the count-based `offset` parameter to avoid offset_id inclusive/
    exclusive ambiguity across Pyrogram/kurigram versions.
    """
    user = await _get_user_client()
    media_msgs = []
    scanned = 0

    async for msg in user.get_chat_history(chat_id, limit=SCAN_LIMIT, offset=skip):
        scanned += 1
        if get_media_from_message(msg):
            media_msgs.append(msg)
        if len(media_msgs) >= PAGE_SIZE:
            break

    next_skip = skip + scanned
    has_more = len(media_msgs) >= PAGE_SIZE
    return media_msgs, next_skip, has_more


def _file_label(msg: Message, selected: bool) -> str:
    media = get_media_from_message(msg)
    name = getattr(media, "file_name", None) or f"[{msg.id}]"
    size = getattr(media, "file_size", 0) or 0
    size_str = humanbytes(size) if size else "?"
    prefix = "☑" if selected else "☐"
    max_name = 42 - len(size_str)
    if len(name) > max_name:
        name = name[: max_name - 1] + "…"
    return f"{prefix} {name} · {size_str}"


def _build_markup(
    chat_id: int,
    media_msgs,
    next_offset: int,
    has_more: bool,
    selected: set,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            _file_label(msg, msg.id in selected),
            callback_data=f"gtog_{chat_id}_{msg.id}",
        )]
        for msg in media_msgs
    ]
    bottom = []
    if selected:
        bottom.append(InlineKeyboardButton(
            f"✅ Grab Selected ({len(selected)})",
            callback_data=f"grabsel_{chat_id}",
        ))
    if has_more:
        bottom.append(InlineKeyboardButton(
            "Load More ▶",
            callback_data=f"grablist_{chat_id}_{next_offset}",
        ))
    if bottom:
        rows.append(bottom)
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

        media_msgs, next_offset, has_more = await _build_page(chat_id, skip=0)
        if not media_msgs:
            await status.edit_text("No media found in this channel.")
            return

        markup = _build_markup(chat_id, media_msgs, next_offset, has_more, selected=set())
        await status.edit_text(
            f"**{chat_title}** — select files then tap Grab Selected:",
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


@StreamBot.on_callback_query(filters.regex(r"^gtog_") & _owner_filter)
async def gtog_cb(client: Client, cb: CallbackQuery):
    """Toggle selection of a single file."""
    parts = cb.data.split("_", 2)
    chat_id = int(parts[1])
    msg_id = int(parts[2])

    key = (cb.from_user.id, cb.message.id)
    sel = _selections.setdefault(key, set())
    if msg_id in sel:
        sel.discard(msg_id)
    else:
        sel.add(msg_id)

    # Rebuild the current keyboard with updated checkboxes.
    # Re-read buttons from existing markup to avoid re-fetching Telegram.
    old_rows = cb.message.reply_markup.inline_keyboard
    new_rows = []
    for row in old_rows:
        new_row = []
        for btn in row:
            d = btn.callback_data or ""
            if d.startswith("gtog_"):
                p = d.split("_", 2)
                mid = int(p[2])
                checked = mid in sel
                label = btn.text
                # Replace leading checkbox char
                label = ("☑" if checked else "☐") + label[1:]
                new_row.append(InlineKeyboardButton(label, callback_data=d))
            elif d.startswith("grabsel_"):
                # Update count or remove if nothing selected
                if sel:
                    new_row.append(InlineKeyboardButton(
                        f"✅ Grab Selected ({len(sel)})",
                        callback_data=d,
                    ))
                # else: drop the button entirely
            else:
                new_row.append(btn)
        if new_row:
            new_rows.append(new_row)

    # Ensure Grab Selected button exists if something is selected
    bottom = new_rows[-1] if new_rows else []
    has_grab_sel = any(b.callback_data and b.callback_data.startswith("grabsel_") for b in bottom)
    if sel and not has_grab_sel:
        new_rows.append([InlineKeyboardButton(
            f"✅ Grab Selected ({len(sel)})",
            callback_data=f"grabsel_{chat_id}",
        )])

    await cb.message.edit_reply_markup(InlineKeyboardMarkup(new_rows))
    await cb.answer()


@StreamBot.on_callback_query(filters.regex(r"^grablist_") & _owner_filter)
async def grablist_cb(client: Client, cb: CallbackQuery):
    """Load next page — clears selections since the list changes."""
    parts = cb.data.split("_", 2)
    chat_id = int(parts[1])
    skip = int(parts[2])

    await cb.answer("Loading…")
    # Clear selections for this message when the page changes
    _selections.pop((cb.from_user.id, cb.message.id), None)
    try:
        media_msgs, next_offset, has_more = await _build_page(chat_id, skip)
        if not media_msgs:
            await cb.message.edit_text("No more media found.")
            return
        markup = _build_markup(chat_id, media_msgs, next_offset, has_more, selected=set())
        try:
            await cb.message.edit_reply_markup(reply_markup=markup)
        except Exception as edit_err:
            if "MESSAGE_NOT_MODIFIED" not in str(edit_err):
                raise
    except Exception as e:
        logger.exception("grablist_cb failed")
        await cb.answer(f"Error: {e}", show_alert=True)


@StreamBot.on_callback_query(filters.regex(r"^grabsel_") & _owner_filter)
async def grabsel_cb(client: Client, cb: CallbackQuery):
    """Grab all selected files, showing live progress."""
    parts = cb.data.split("_", 1)
    chat_id = int(parts[1])

    key = (cb.from_user.id, cb.message.id)
    sel = list(_selections.pop(key, set()))
    total = len(sel)
    if not total:
        await cb.answer("Nothing selected.", show_alert=True)
        return

    await cb.answer(f"Starting {total} grab(s)…")
    progress = await cb.message.reply_text(
        f"⏳ 0 / {total} grabbed…", quote=True,
    )
    done, failed = 0, 0
    for i, msg_id in enumerate(sel, 1):
        try:
            await progress.edit_text(f"⬇️ {i}/{total} — downloading…")
            user = await _get_user_client()
            src = await user.get_messages(chat_id, msg_id)
            if src.empty or not get_media_from_message(src):
                failed += 1
                await progress.edit_text(f"⚠️ {i}/{total} — no media, skipping.")
                continue

            media = get_media_from_message(src)
            name = getattr(media, "file_name", None) or f"msg {msg_id}"
            await progress.edit_text(f"⬆️ {i}/{total} — uploading **{name}**…")

            log_msg = await _reupload(src)
            schedule_index(client, log_msg)
            await log_msg.reply_text(
                f"**Grabbed** | source `{chat_id}/{msg_id}`",
                disable_web_page_preview=True,
                quote=True,
            )
            reply_markup, stream_text, _ = await gen_link(m=log_msg, log_msg=log_msg, from_channel=False)
            # Send stream link as a direct reply to the list message so it's easy to find
            await cb.message.reply_text(
                stream_text, disable_web_page_preview=True,
                reply_markup=reply_markup, quote=True,
            )
            done += 1
        except Exception as e:
            logger.exception("grabsel_cb item %s failed", msg_id)
            failed += 1
            await progress.edit_text(f"❌ {i}/{total} — error: {e}")

    summary = f"✅ Done — {done}/{total} grabbed."
    if failed:
        summary += f" {failed} failed."
    await progress.edit_text(summary)


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
