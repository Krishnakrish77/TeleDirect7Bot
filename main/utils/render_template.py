import logging
import urllib.parse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from main.vars import Var
from main.bot import StreamBot
from main.utils.human_readable import humanbytes
from main.utils.file_properties import get_file_ids
from main.server.exceptions import InvalidHash


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=False,
)
_REQ_TEMPLATE = _env.get_template("req.html")
_DL_TEMPLATE = _env.get_template("dl.html")

# MIME types browsers can decode natively in a <video>/<audio> tag.
# Anything else (mkv, avi, flv, wmv, mov-variants…) falls back to the download
# page — otherwise the browser thrashes on small range probes trying to parse
# a container it can't decode, hammering Telegram for no user benefit.
BROWSER_PLAYABLE = {
    "video/mp4", "video/webm", "video/ogg",
    "audio/mpeg", "audio/mp3", "audio/mp4", "audio/ogg",
    "audio/wav", "audio/x-wav", "audio/webm", "audio/aac", "audio/flac",
}


async def render_page(message_id, secure_hash):
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), int(message_id))
    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f'link hash: {secure_hash} - {file_data.unique_id[:6]}')
        logging.debug(f"Invalid hash for message with - ID {message_id}")
        raise InvalidHash
    src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{message_id}')
    full_mime = (file_data.mime_type or "").lower()
    major = full_mime.split('/')[0].strip()
    if major in ("video", "audio") and full_mime in BROWSER_PLAYABLE:
        heading = ("Watch " if major == "video" else "Listen ") + (file_data.file_name or "")
        return _REQ_TEMPLATE.render(
            tag=major,
            heading=heading,
            src=src,
            file_name=file_data.file_name,
        )
    heading = f"Download {file_data.file_name}"
    return _DL_TEMPLATE.render(
        heading=heading,
        file_name=file_data.file_name,
        file_size=humanbytes(file_data.file_size),
        src=src,
    )
