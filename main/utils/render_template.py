import logging
import urllib.parse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from main.vars import Var
from main.bot import StreamBot
from main.utils import media_index
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


# Containers the browser can decode straight from a byte-range stream.
# Anything else (MKV, AVI, WMV...) gets routed through HLS because the
# container itself isn't supported in MSE, not just the codecs inside.
_BROWSER_NATIVE_CONTAINERS = {
    "video/mp4",
    "video/quicktime",   # .mov
    "video/x-m4v",
    "video/webm",
}


async def render_page(message_id, secure_hash):
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), int(message_id))
    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f'link hash: {secure_hash} - {file_data.unique_id[:6]}')
        logging.debug(f"Invalid hash for message with - ID {message_id}")
        raise InvalidHash
    src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{message_id}')
    full_mime = (file_data.mime_type or "").lower().strip()
    mime_type = full_mime.split('/')[0]
    if mime_type in ("video", "audio"):
        heading = ("Watch " if mime_type == "video" else "Listen ") + (file_data.file_name or "")
        # Route through HLS only when the container actually needs it.
        # MP4/MOV/M4V/WebM byte-stream directly to <video src=...>;
        # MKV/AVI/etc. need ffmpeg to transmux into MPEG-TS. Skipping the
        # HLS path for browser-native containers avoids spinning ffmpeg
        # and any DTS-monotonicity demuxer errors that can surface when
        # segmenting B-frame-heavy MP4s.
        hls_src = None
        if mime_type == "video" and full_mime and full_mime not in _BROWSER_NATIVE_CONTAINERS:
            hls_src = urllib.parse.urljoin(
                Var.URL, f'hls/{secure_hash}{message_id}/playlist.m3u8',
            )
        # Base path for subtitle endpoints — the client appends /list.json
        # and /{n}.vtt itself.
        sub_path = (
            urllib.parse.urljoin(Var.URL, f'sub/{secure_hash}{message_id}').rstrip("/")
            if mime_type == "video" else None
        )
        # TMDB metadata, if the catalogue has it for this entry. Optional —
        # template guards on `meta and meta.tmdb_id`.
        meta = media_index.get_item(int(message_id))
        return _REQ_TEMPLATE.render(
            tag=mime_type,
            heading=heading,
            src=src,
            hls_src=hls_src,
            sub_path=sub_path,
            file_name=file_data.file_name,
            meta=meta,
        )
    heading = f"Download {file_data.file_name}"
    return _DL_TEMPLATE.render(
        heading=heading,
        file_name=file_data.file_name,
        file_size=humanbytes(file_data.file_size),
        src=src,
    )
