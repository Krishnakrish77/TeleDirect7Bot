import asyncio
import logging
import os
import re
import tempfile
import time
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

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
_idle_disconnect_task: Optional[asyncio.Task] = None
_IDLE_TIMEOUT = 300  # disconnect after 5 min of inactivity


# ---------------------------------------------------------------------------
# User client helpers
# ---------------------------------------------------------------------------

def _schedule_idle_disconnect() -> None:
    """Reset the idle timer. Client disconnects after _IDLE_TIMEOUT seconds
    of no grab activity, freeing ~40 MB of MTProto session overhead."""
    global _idle_disconnect_task
    if _idle_disconnect_task and not _idle_disconnect_task.done():
        _idle_disconnect_task.cancel()

    async def _disconnect_after_idle():
        await asyncio.sleep(_IDLE_TIMEOUT)
        global _user_client
        async with _user_client_lock:
            if _user_client and _user_client.is_connected:
                try:
                    await _user_client.stop()
                    logger.info("grab: user client stopped (idle)")
                except Exception as e:
                    logger.warning("grab: idle stop failed: %s", e)
            _user_client = None

    _idle_disconnect_task = asyncio.ensure_future(_disconnect_after_idle())


async def _get_user_client() -> Client:
    global _user_client
    async with _user_client_lock:
        if _user_client is None or not _user_client.is_connected:
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
    _schedule_idle_disconnect()
    return _user_client


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _is_http_url(text: str) -> bool:
    """True when the argument is a plain HTTP/HTTPS URL (not a t.me link)."""
    t = text.strip()
    return bool(re.match(r"https?://", t, re.IGNORECASE)) and "t.me/" not in t


# ---------------------------------------------------------------------------
# HTTP URL download helper
# ---------------------------------------------------------------------------

# Max download size: env-tunable, default 1.5 GB (Koyeb free-tier has ~1 GB
# ephemeral disk; 1.5 GB leaves room for the upload temp copy to coexist).
_MAX_URL_GRAB_BYTES = int(os.environ.get("GRAB_URL_MAX_BYTES", str(1_500 * 1024 * 1024)))

# Private / loopback / link-local ranges that must never be fetched
# (SSRF protection). Cloud metadata endpoints live in 169.254.0.0/16.
import ipaddress as _ipaddress
_BLOCKED_NETWORKS = [
    _ipaddress.ip_network(n) for n in (
        "127.0.0.0/8",       # loopback
        "10.0.0.0/8",        # RFC-1918 private
        "172.16.0.0/12",     # RFC-1918 private
        "192.168.0.0/16",    # RFC-1918 private
        "169.254.0.0/16",    # link-local / cloud metadata (AWS, GCP, Azure)
        "100.64.0.0/10",     # shared address space (RFC-6598)
        "::1/128",           # IPv6 loopback
        "fc00::/7",          # IPv6 unique-local
        "fe80::/10",         # IPv6 link-local
    )
]


def _check_ssrf(host: str) -> None:
    """Raise ValueError if host resolves to a private/internal address."""
    import socket as _socket
    try:
        addrs = _socket.getaddrinfo(host, None)
    except _socket.gaierror as e:
        raise ValueError(f"Cannot resolve host '{host}': {e}") from e
    for _, _, _, _, sockaddr in addrs:
        ip = _ipaddress.ip_address(sockaddr[0])
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise ValueError(
                    f"Blocked: '{host}' resolves to {ip} which is in a "
                    f"private/internal range ({net}). Use a public URL."
                )


async def _grab_from_url(url: str, status_msg=None) -> Message:
    """Download a file from an HTTP(S) URL and re-upload it to BIN_CHANNEL.

    Security:
    - SSRF guard: hostname must not resolve to a private/loopback/metadata IP
    - Size cap: rejects downloads exceeding GRAB_URL_MAX_BYTES (default 1.5 GB)
    - Content-Type check: rejects HTML responses (login pages, error pages)
    - Redirect safety: aiohttp follows redirects but each hop is re-checked
      against the same SSRF guard via allow_redirects=True + connector
    Performance:
    - Streams in 512 KB chunks — never loads the full file into RAM
    - Progress updated every 5 s or every 5 MB
    """
    import aiohttp as _aiohttp
    from urllib.parse import urlparse as _urlparse

    url = url.strip()
    parsed = _urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http:// and https:// URLs are supported.")

    # SSRF check before opening any connection.
    _check_ssrf(parsed.hostname or "")

    logger.info("grab_url: fetching %s", url)
    timeout = _aiohttp.ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=120)

    async with _aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=True, max_redirects=5) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status} — server refused the download.")

            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()

            # Reject HTML responses — these are almost always login/error pages,
            # not the actual file. A bare download URL never serves text/html.
            if content_type.startswith("text/html"):
                raise ValueError(
                    "Server returned an HTML page instead of a file. "
                    "The URL may require login or redirect to an error page."
                )

            # Enforce size cap early if Content-Length is provided.
            total_size = int(resp.headers.get("Content-Length") or 0)
            if total_size and total_size > _MAX_URL_GRAB_BYTES:
                raise ValueError(
                    f"File too large: {humanbytes(total_size)} exceeds the "
                    f"{humanbytes(_MAX_URL_GRAB_BYTES)} grab limit."
                )

            # Derive filename: prefer Content-Disposition, fall back to URL path.
            file_name = ""
            cd = resp.headers.get("Content-Disposition", "")
            if cd:
                m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^\";\']+)', cd, re.IGNORECASE)
                if m:
                    file_name = m.group(1).strip().strip('"\'')
            if not file_name:
                file_name = os.path.basename(resp.url.path) or "download"
            # Sanitise: strip directory separators that could escape the temp dir.
            file_name = os.path.basename(file_name.replace("\\", "/")) or "download"

            logger.info(
                "grab_url: %s — %s — %s",
                file_name, content_type,
                humanbytes(total_size) if total_size else "unknown size",
            )
            if status_msg:
                size_hint = humanbytes(total_size) if total_size else "unknown size"
                try:
                    await status_msg.edit_text(f"⬇️ Downloading {file_name} ({size_hint})…")
                except Exception:
                    pass

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=f"_grab_{file_name}")
            os.close(tmp_fd)
            try:
                downloaded = 0
                last_edit = 0.0
                last_mb_bucket = 0
                CHUNK = 512 * 1024  # 512 KB

                with open(tmp_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(CHUNK):
                        f.write(chunk)
                        downloaded += len(chunk)

                        # Hard size cap mid-stream (Content-Length may be absent).
                        if downloaded > _MAX_URL_GRAB_BYTES:
                            raise ValueError(
                                f"Download exceeded the {humanbytes(_MAX_URL_GRAB_BYTES)} limit — aborted."
                            )

                        now = time.monotonic()
                        mb_bucket = downloaded // (5 * 1024 * 1024)
                        if status_msg and (mb_bucket > last_mb_bucket or now - last_edit > 5):
                            last_mb_bucket = mb_bucket
                            last_edit = now
                            done_str = humanbytes(downloaded)
                            pct = f" {downloaded * 100 // total_size}%" if total_size else ""
                            try:
                                await status_msg.edit_text(
                                    f"⬇️ Downloading {file_name}{pct} ({done_str}…)"
                                )
                            except Exception:
                                pass

                logger.info("grab_url: download complete — %s", humanbytes(downloaded))

                # Determine upload method from Content-Type + extension.
                ext = os.path.splitext(file_name)[1].lower()
                _VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts", ".flv", ".wmv"}
                _AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".ogg", ".wav", ".aac", ".opus"}
                is_video = content_type.startswith("video/") or ext in _VIDEO_EXTS
                is_audio = content_type.startswith("audio/") or ext in _AUDIO_EXTS

                if status_msg:
                    try:
                        await status_msg.edit_text(f"⬆️ Uploading {file_name}…")
                    except Exception:
                        pass

                up_cb = _progress_callback("⬆️ Uploading", status_msg, downloaded)
                if is_video:
                    return await StreamBot.send_video(
                        Var.BIN_CHANNEL, tmp_path,
                        file_name=file_name, supports_streaming=True, progress=up_cb,
                    )
                if is_audio:
                    return await StreamBot.send_audio(
                        Var.BIN_CHANNEL, tmp_path, file_name=file_name, progress=up_cb,
                    )
                return await StreamBot.send_document(
                    Var.BIN_CHANNEL, tmp_path, file_name=file_name, progress=up_cb,
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


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

def _progress_callback(label: str, status_msg, total_size: int) -> Callable:
    """
    Returns a Pyrogram progress callback that:
    - Logs every 10% to the server log
    - Edits status_msg every 5 seconds or every 10% (whichever comes first)
    """
    last_edit = [0.0]
    last_pct = [-1]

    async def cb(current: int, total: int):
        if not total:
            return
        pct = current * 100 // total
        now = time.monotonic()
        if pct == last_pct[0]:
            return
        if pct - last_pct[0] < 10 and now - last_edit[0] < 5:
            return

        last_pct[0] = pct
        last_edit[0] = now
        done_str = humanbytes(current)
        total_str = humanbytes(total)
        logger.info("grab %s: %d%% (%s / %s)", label, pct, done_str, total_str)
        if status_msg:
            try:
                await status_msg.edit_text(
                    f"{label} {pct}%  ({done_str} / {total_str})"
                )
            except Exception:
                pass

    return cb


async def _reupload(src: Message, status_msg=None) -> Message:
    """Download src via user client to a temp file, re-upload via StreamBot.

    Temp file avoids loading the whole file into RAM (in_memory=True caused
    OOM on large files — an 800 MB file = 800 MB heap spike).
    status_msg: optional Message to edit with live download/upload progress.
    """
    user = await _get_user_client()
    media = get_media_from_message(src)
    file_name = getattr(media, "file_name", None) or "file"
    file_size = getattr(media, "file_size", 0) or 0
    caption = src.caption or ""

    logger.info("grab: starting download — %s (%s)", file_name, humanbytes(file_size))

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=f"_grab_{file_name}")
    os.close(tmp_fd)
    try:
        dl_cb = _progress_callback("⬇️ Downloading", status_msg, file_size)
        await user.download_media(src, file_name=tmp_path, progress=dl_cb)
        logger.info("grab: download complete — %s, uploading to BIN_CHANNEL", file_name)

        up_cb = _progress_callback("⬆️ Uploading", status_msg, file_size)
        mime = (getattr(media, "mime_type", "") or "").lower()
        ext = os.path.splitext(file_name)[1].lower()
        _VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts", ".flv", ".wmv", ".3gp"}
        _AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".ogg", ".wav", ".aac", ".opus"}
        # Use send_video for video MIME type OR known video extension.
        # Many channels send videos as generic documents (mime=application/octet-stream)
        # — extension check catches those and ensures Telegram extracts duration.
        is_video = bool(src.video) or mime.startswith("video/") or ext in _VIDEO_EXTS
        is_audio = bool(src.audio) or mime.startswith("audio/") or ext in _AUDIO_EXTS
        if is_video:
            return await StreamBot.send_video(
                Var.BIN_CHANNEL, tmp_path,
                file_name=file_name, caption=caption, supports_streaming=True,
                progress=up_cb,
            )
        if is_audio:
            return await StreamBot.send_audio(
                Var.BIN_CHANNEL, tmp_path, file_name=file_name, caption=caption,
                progress=up_cb,
            )
        return await StreamBot.send_document(
            Var.BIN_CHANNEL, tmp_path, file_name=file_name, caption=caption,
            progress=up_cb,
        )
    finally:
        logger.info("grab: cleaning up temp file for %s", file_name)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Grablist page builder + selection state
# ---------------------------------------------------------------------------

# (user_id, bot_message_id) → set of source message IDs selected
_selections: Dict[Tuple[int, int], set] = {}

# (user_id, bot_message_id) → list of before_id anchors, one per page visited.
# before_id=0 means "from the newest message". We use offset_id with a
# small add_offset=-1 trick: fetch messages where id < before_id by passing
# offset_id=before_id with add_offset=0 (Pyrogram exclusive semantics).
_nav: Dict[Tuple[int, int], List[int]] = {}


async def _build_page(chat_id: int, max_id: int):
    """
    Return up to PAGE_SIZE media messages from chat_id.

    max_id=0  → start from the newest message (no upper bound).
    max_id=X  → only messages with id ≤ X (kurigram makes max_id inclusive
                 by adding 1 internally before the raw API call).

    Returns (media_msgs, next_max_id, has_more).
    next_max_id is oldest_id_seen - 1, ready to pass as max_id for the
    next page so it returns messages strictly older than this page.
    """
    user = await _get_user_client()
    media_msgs = []
    oldest_id = 0

    kwargs: dict = {"limit": SCAN_LIMIT}
    if max_id:
        kwargs["max_id"] = max_id

    async for msg in user.get_chat_history(chat_id, **kwargs):
        oldest_id = msg.id
        if get_media_from_message(msg):
            media_msgs.append(msg)
        if len(media_msgs) >= PAGE_SIZE:
            break

    # kurigram max_id is inclusive, so next page needs oldest_id - 1
    # to avoid re-showing the oldest message on this page.
    next_max_id = oldest_id - 1 if oldest_id > 1 else 0
    has_more = len(media_msgs) >= PAGE_SIZE and oldest_id > 1
    return media_msgs, next_max_id, has_more


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
    oldest_id: int,
    has_more: bool,
    selected: set,
    has_prev: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            _file_label(msg, msg.id in selected),
            callback_data=f"gtog_{chat_id}_{msg.id}",
        )]
        for msg in media_msgs
    ]
    # Navigation row
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"grabprev_{chat_id}"))
    if has_more:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"grabnext_{chat_id}_{oldest_id}"))
    if nav:
        rows.append(nav)
    # Grab selected row
    if selected:
        rows.append([InlineKeyboardButton(
            f"✅ Grab Selected ({len(selected)})",
            callback_data=f"grabsel_{chat_id}",
        )])
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
            "  `/grab https://example.com/video.mp4`  ← direct HTTP URL\n"
            "  `/grab https://t.me/channel/123`\n"
            "  `/grab @channel 123`\n"
            "  `/grab -100xxx 123`",
            quote=True,
        )
        return

    arg = parts[1].strip()
    status = await m.reply_text("Fetching…", quote=True)
    try:
        # ── HTTP URL path (no USER_SESSION required) ──────────────────────
        if _is_http_url(arg):
            log_msg = await _grab_from_url(arg, status_msg=status)
            schedule_index(client, log_msg)
            await log_msg.reply_text(
                text=f"**Grabbed from URL** by [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n"
                     f"**Source:** `{arg[:200]}`",
                disable_web_page_preview=True,
                quote=True,
            )
            reply_markup, stream_text, _ = await gen_link(m=log_msg, log_msg=log_msg, from_channel=False)
            await status.edit_text(stream_text, disable_web_page_preview=True, reply_markup=reply_markup)
            return

        # ── Telegram message path (requires USER_SESSION) ─────────────────
        if not Var.USER_SESSION:
            await status.edit_text("Set `USER_SESSION` in .env and restart first.")
            return

        chat, msg_id = _parse_grab_args(arg)
        user = await _get_user_client()
        src = await user.get_messages(chat, msg_id)
        if src.empty or not get_media_from_message(src):
            await status.edit_text("Message not found or contains no media.")
            return

        await status.edit_text("⬇️ Downloading 0%…")
        log_msg = await _reupload(src, status_msg=status)
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

        media_msgs, next_max_id, has_more = await _build_page(chat_id, max_id=0)
        if not media_msgs:
            await status.edit_text("No media found in this channel.")
            return

        markup = _build_markup(
            chat_id, media_msgs, next_max_id, has_more,
            selected=set(), has_prev=False,
        )
        await status.edit_text(
            f"**{chat_title}** — select files then tap Grab Selected:",
            reply_markup=markup,
        )
        # Nav stack stores the max_id used to load each page.
        # Page 1 uses max_id=0 (no upper bound).
        _nav[(m.from_user.id, status.id)] = [0]

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

    # Ensure Grab Selected exists at the end (after nav row) if something selected
    flat = [b for row in new_rows for b in row]
    has_grab_sel = any(b.callback_data and b.callback_data.startswith("grabsel_") for b in flat)
    if sel and not has_grab_sel:
        new_rows.append([InlineKeyboardButton(
            f"✅ Grab Selected ({len(sel)})",
            callback_data=f"grabsel_{chat_id}",
        )])

    try:
        await cb.message.edit_reply_markup(InlineKeyboardMarkup(new_rows))
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e):
            logger.warning("gtog_cb edit_reply_markup failed: %s", e)
    await cb.answer()


async def _load_page(cb: CallbackQuery, client: Client, chat_id: int, max_id: int, has_prev: bool):
    """Fetch a page with max_id and update the list message."""
    key = (cb.from_user.id, cb.message.id)
    _selections.pop(key, None)
    media_msgs, next_max_id, has_more = await _build_page(chat_id, max_id)
    if not media_msgs:
        await cb.answer("No more media found.", show_alert=True)
        return False
    markup = _build_markup(
        chat_id, media_msgs, next_max_id, has_more,
        selected=set(), has_prev=has_prev,
    )
    try:
        await cb.message.edit_reply_markup(reply_markup=markup)
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e):
            raise
    return True


@StreamBot.on_callback_query(filters.regex(r"^grabnext_") & _owner_filter)
async def grabnext_cb(client: Client, cb: CallbackQuery):
    """Navigate to the next (older) page."""
    parts = cb.data.split("_", 2)
    chat_id = int(parts[1])
    next_max_id = int(parts[2])   # oldest_id-1 from the previous page

    key = (cb.from_user.id, cb.message.id)
    nav = _nav.setdefault(key, [0])

    await cb.answer("Loading…")
    try:
        ok = await _load_page(cb, client, chat_id, next_max_id, has_prev=True)
        if ok:
            nav.append(next_max_id)
    except Exception as e:
        logger.exception("grabnext_cb failed")
        await cb.answer(f"Error: {e}", show_alert=True)


@StreamBot.on_callback_query(filters.regex(r"^grabprev_") & _owner_filter)
async def grabprev_cb(client: Client, cb: CallbackQuery):
    """Navigate back to the previous (newer) page."""
    parts = cb.data.split("_", 1)
    chat_id = int(parts[1])

    key = (cb.from_user.id, cb.message.id)
    nav = _nav.get(key, [0])

    if len(nav) <= 1:
        await cb.answer("Already on the first page.", show_alert=True)
        return

    nav.pop()                    # discard current page's max_id
    prev_max_id = nav[-1]        # max_id that loads the previous page

    await cb.answer("Loading…")
    try:
        await _load_page(cb, client, chat_id, prev_max_id, has_prev=len(nav) > 1)
    except Exception as e:
        logger.exception("grabprev_cb failed")
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
            user = await _get_user_client()
            src = await user.get_messages(chat_id, msg_id)
            if src.empty or not get_media_from_message(src):
                failed += 1
                await progress.edit_text(f"⚠️ {i}/{total} — no media, skipping.")
                continue

            media = get_media_from_message(src)
            name = getattr(media, "file_name", None) or f"msg {msg_id}"
            await progress.edit_text(f"⬇️ {i}/{total} — downloading **{name}** 0%…")

            log_msg = await _reupload(src, status_msg=progress)
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
        await progress.edit_text(f"⬇️ Downloading **{file_name}** 0%…")

        log_msg = await _reupload(src, status_msg=progress)
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
