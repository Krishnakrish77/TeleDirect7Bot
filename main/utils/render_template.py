import logging
import re
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
_env.filters["humansize"] = lambda b: humanbytes(b) if b else ""
_REQ_TEMPLATE = _env.get_template("req.html")
_DL_TEMPLATE = _env.get_template("dl.html")


# Containers the browser can decode straight from a byte-range stream.
# Anything else (MKV, AVI, WMV...) gets routed through HLS because the
# container itself isn't supported in MSE, not just the codecs inside.
_KURIGRAM_TS_RE = re.compile(r"^video_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.mp4$")


def _best_file_name(api_name: str, meta) -> str:
    """Return the most useful filename: catalogue > API (if not a kurigram
    timestamp) > empty string."""
    if meta and meta.file_name:
        return meta.file_name
    if api_name and not _KURIGRAM_TS_RE.match(api_name):
        return api_name
    return ""


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
        next_ep = media_index.next_episode(meta) if meta else None
        file_name = _best_file_name(file_data.file_name, meta)
        from main.utils import codec_probe
        known_unplayable = (
            mime_type == "video"
            and meta is not None
            and codec_probe.known_unplayable(meta)
        )
        # Quality variants — other uploads of the same film or episode at
        # different resolutions / file sizes. Shown as chips on the watch
        # page so users can switch quality without going back.
        quality_variants: list = []
        if meta and mime_type == "video":
            if meta.movie_key:
                quality_variants = [
                    v for v in media_index.variants_for_movie(meta.movie_key)
                    if v.message_id != meta.message_id
                ]
            elif meta.series_key and meta.episode is not None:
                quality_variants = [
                    e for e in media_index.episodes_for_series(meta.series_key)
                    if (e.season == meta.season
                        and e.episode == meta.episode
                        and e.episode_end == meta.episode_end
                        and e.message_id != meta.message_id)
                ]
        return _REQ_TEMPLATE.render(
            tag=mime_type,
            is_audio=(mime_type == "audio"),
            heading=("Watch " if mime_type == "video" else "Listen ") + file_name,
            src=src,
            hls_src=hls_src,
            sub_path=sub_path,
            file_name=file_name,
            meta=meta,
            known_unplayable=known_unplayable,
            video_codec=(meta.video_codec if meta else "") or "",
            pix_fmt=(meta.pix_fmt if meta else "") or "",
            next_ep=next_ep,
            quality_variants=quality_variants,
        )
    file_name = _best_file_name(file_data.file_name, None)
    heading = f"Download {file_name}"
    return _DL_TEMPLATE.render(
        heading=heading,
        file_name=file_name,
        file_size=humanbytes(file_data.file_size),
        src=src,
    )
