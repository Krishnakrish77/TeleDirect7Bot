from pyrogram import Client
from typing import Any, Optional
from pyrogram.types import Message
from pyrogram.file_id import FileId
from pyrogram.raw.types.messages import Messages
from main.exceptions import FIleNotFound
from main.utils.Translation import Language
from main.utils.download_urls import as_download_url
from main.utils.human_readable import humanbytes
from main.vars import Var
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

async def parse_file_id(message: "Message") -> Optional[FileId]:
    media = get_media_from_message(message)
    if media:
        return FileId.decode(media.file_id)

async def parse_file_unique_id(message: "Messages") -> Optional[str]:
    media = get_media_from_message(message)
    if media:
        return media.file_unique_id

async def get_file_ids(client: Client, chat_id: int, message_id: int) -> Optional[FileId]:
    message = await client.get_messages(chat_id, message_id)
    if message.empty:
        raise FIleNotFound
    media = get_media_from_message(message)
    if not media:
        raise FIleNotFound
    file_unique_id = await parse_file_unique_id(message)
    file_id = await parse_file_id(message)
    if not file_id:
        raise FIleNotFound
    setattr(file_id, "file_size", getattr(media, "file_size", 0))
    setattr(file_id, "mime_type", getattr(media, "mime_type", ""))
    setattr(file_id, "file_name", getattr(media, "file_name", ""))
    setattr(file_id, "unique_id", file_unique_id)
    return file_id

def get_media_from_message(message: "Message") -> Any:
    media_types = (
        "audio",
        "document",
        "photo",
        "sticker",
        "animation",
        "video",
        "voice",
        "video_note",
    )
    for attr in media_types:
        media = getattr(message, attr, None)
        if media:
            return media


def get_hash(media_msg: Message) -> str:
    media = get_media_from_message(media_msg)
    uid = getattr(media, "file_unique_id", "")[:16]
    # Trim trailing digits so the URL hash never ends in a digit.
    # The path parser splits hash from message_id at the digit boundary;
    # a hash ending in a digit would cause it to over-consume the message_id.
    while uid and uid[-1].isdigit():
        uid = uid[:-1]
    return uid

def get_media_file_size(m):
    media = get_media_from_message(m)
    return getattr(media, "file_size", "None")

def get_name(media_msg: Message) -> str:
    media = get_media_from_message(media_msg)
    return getattr(media, "file_name", None) or ""

def get_media_mime_type(m):
    media = get_media_from_message(m)
    return getattr(media, "mime_type", "None/unknown")

def get_media_file_unique_id(m):
    media = get_media_from_message(m)
    return getattr(media, "file_unique_id", "")

# Generate Text, Stream Link, reply_markup
async def gen_link(m: Message, log_msg: Messages, from_channel: bool):
    """Generate Text for Stream Link, Reply Text and reply_markup"""
    lang = getattr(Language, "en")
    file_name = get_name(log_msg)
    file_size = humanbytes(get_media_file_size(log_msg))
    file_hash = get_hash(log_msg)

    page_link = f"{Var.URL}watch/{file_hash}{log_msg.id}"
    stream_link = f"{Var.URL}{file_hash}{log_msg.id}"
    download_link = as_download_url(stream_link)
    Stream_Text = lang.stream_msg_text.format(file_name, file_size, download_link, page_link)

    buttons = [[InlineKeyboardButton("🖥STREAM", url=page_link),
                InlineKeyboardButton("Dᴏᴡɴʟᴏᴀᴅ 📥", url=download_link)]]
    if not from_channel:
        buttons.append([InlineKeyboardButton(
            "❌ Delete Link",
            callback_data=f"msgdelconf2_{log_msg.id}_{get_media_file_unique_id(log_msg)}"
        )])
    reply_markup = InlineKeyboardMarkup(buttons)

    return reply_markup, Stream_Text, download_link
