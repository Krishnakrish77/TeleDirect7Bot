import logging
import re
import urllib.parse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from main.vars import Var
from main.bot import StreamBot
from main.utils import media_index
from main.utils.download_urls import as_download_url
from main.utils.human_readable import humanbytes
from main.utils.file_properties import get_file_ids
from main.utils.playback import should_offer_hls_for_video
from main.utils import share_meta
from main.exceptions import InvalidHash


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=False,
)
_env.filters["humansize"] = lambda b: humanbytes(b) if b else ""

# Strip distribution-site watermarks from music display fields
from main.utils.codec_probe import _clean_music_tag as _cmt
_env.filters["clean_music_tag"] = lambda s: _cmt(s) if s else s
import re as _re
_env.filters["artist_slug"] = lambda s: _re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
from main.utils.media_index import (
    _artist_slug as _mslug, _primary_artist as _mprimary, _artist_credits as _mcredits,
    _person_slug as _mpslug, _director_credits as _mdcredits,
)
_env.filters["primary_artist_slug"] = lambda s: _mslug(_mprimary(s or ""))
_env.filters["artist_credits"] = lambda s: [(_mslug(a), a) for a in _mcredits(s or "")]
_env.filters["person_slug"] = lambda s: _mpslug(s or "")
_env.filters["director_credits"] = lambda s: [(_mpslug(d), d) for d in _mdcredits(s or "")]
from main.vars import Var as _Var
_env.globals["bot_username"] = _Var.BOT_USERNAME
_env.globals["Var"] = _Var


def _fmt_duration(seconds: int) -> str:
    if not seconds:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_env.filters["duration"] = _fmt_duration
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


async def render_page(message_id, secure_hash,
                      vlc_user_id=None, vlc_token=None):
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), int(message_id))
    if not secure_hash or file_data.unique_id[:len(secure_hash)] != secure_hash:
        logging.debug(f'link hash: {secure_hash} - {file_data.unique_id[:len(secure_hash) if secure_hash else 6]}')
        logging.debug(f"Invalid hash for message with - ID {message_id}")
        raise InvalidHash
    src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{message_id}')
    download_src = as_download_url(src)
    full_mime = (file_data.mime_type or "").lower().strip()
    mime_type = full_mime.split('/')[0]
    if mime_type in ("video", "audio"):
        # Route through HLS only when the container actually needs it.
        # MP4/MOV/M4V/WebM byte-stream directly to <video src=...>;
        # MKV/AVI/etc. need ffmpeg to transmux into MPEG-TS. Skipping the
        # HLS path for browser-native containers avoids spinning ffmpeg
        # and any DTS-monotonicity demuxer errors that can surface when
        # segmenting B-frame-heavy MP4s.
        hls_src = None
        if mime_type == "video" and should_offer_hls_for_video(full_mime=full_mime):
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
        # Derive human-readable format label from the MIME type so the badge
        # works even before a codec probe runs on the file.
        _audio_format_map = {
            "flac": "FLAC", "x-flac": "FLAC",
            "wav": "WAV", "x-wav": "WAV", "wave": "WAV",
            "aiff": "AIFF", "x-aiff": "AIFF",
            "mpeg": "MP3", "mp3": "MP3",
            "aac": "AAC", "mp4": "AAC", "x-m4a": "AAC",
            "opus": "Opus",
            "ogg": "OGG", "vorbis": "OGG",
            "webm": "WebM",
        }
        audio_format = ""
        if mime_type == "audio":
            _sub = full_mime.split("/")[-1].lower()
            audio_format = _audio_format_map.get(_sub, _sub.upper()[:6])
        # Music-specific: next track + "More from album" — compute album track
        # list once (O(N) scan) and derive both from it to avoid double scan.
        next_track = None
        prev_track = None
        album_tracks: list = []
        if meta and mime_type == "audio" and getattr(meta, "album_key", ""):
            _all_tracks = media_index.tracks_for_album(meta.album_key)
            album_tracks = [t for t in _all_tracks if t.message_id != meta.message_id]
            for _i, _t in enumerate(_all_tracks):
                if _t.message_id == meta.message_id:
                    if _i + 1 < len(_all_tracks):
                        _nxt = _all_tracks[_i + 1]
                        next_track = {
                            "url": f"/watch/{_nxt.secure_hash}{_nxt.message_id}",
                            "stream_url": urllib.parse.urljoin(Var.URL, f"{_nxt.secure_hash}{_nxt.message_id}"),
                            "title": _nxt.title or _nxt.file_name or f"Track {_nxt.track_number or _i + 2}",
                            "artist": _nxt.artist or "",
                            "art": f"/thumb/{_nxt.secure_hash}{_nxt.message_id}.jpg?v=audio3",
                            "track_number": _nxt.track_number,
                            "secure_hash": _nxt.secure_hash,
                            "message_id": _nxt.message_id,
                            "duration": _nxt.duration,
                        }
                    if _i > 0:
                        _prv = _all_tracks[_i - 1]
                        prev_track = {
                            "url": f"/watch/{_prv.secure_hash}{_prv.message_id}",
                            "stream_url": urllib.parse.urljoin(Var.URL, f"{_prv.secure_hash}{_prv.message_id}"),
                            "title": _prv.title or _prv.file_name or f"Track {_prv.track_number or _i}",
                            "artist": _prv.artist or "",
                            "art": f"/thumb/{_prv.secure_hash}{_prv.message_id}.jpg?v=audio3",
                            "track_number": _prv.track_number,
                        }
                    break
        file_name = _best_file_name(file_data.file_name, meta)
        # Build a human-readable page heading from catalogue metadata.
        # Falls back to the cleaned filename when metadata is unavailable.
        def _build_heading(meta, mime_type, file_name):
            if not meta:
                return file_name
            if mime_type == "audio":
                parts = []
                if meta.album_title:
                    parts.append(meta.album_title)
                if meta.title:
                    parts.append(meta.title)
                return " · ".join(parts) if parts else file_name
            # Video
            if meta.series_title and meta.season is not None and meta.episode is not None:
                ep = f"S{meta.season:02d}E{meta.episode:02d}"
                title = meta.episode_title or meta.title or ""
                base = f"{meta.series_title} {ep}"
                return f"{base} · {title}" if title else base
            return meta.title or file_name
        heading = _build_heading(meta, mime_type, file_name)
        share_title = ""
        share_description = ""
        share_image = ""
        share_url = ""
        share_type = "website"
        if not (meta and getattr(meta, "hidden", False)):
            share_title = heading
            share_description = share_meta.compact_description(
                getattr(meta, "episode_overview", "") if meta else "",
                getattr(meta, "overview", "") if meta else "",
                getattr(meta, "description", "") if meta else "",
                file_name,
                fallback=f"Watch {heading} on TeleDirect" if heading else "Watch on TeleDirect",
            )
            share_image = share_meta.item_image_url(meta)
            if not share_image:
                share_image = share_meta.fallback_thumb_url(
                    secure_hash,
                    message_id,
                    is_audio=(mime_type == "audio"),
                )
            share_url = share_meta.absolute_url(f"watch/{secure_hash}{message_id}")
            if mime_type == "audio":
                share_type = "music.song"
            elif meta and getattr(meta, "series_key", ""):
                share_type = "video.episode"
            elif meta and (getattr(meta, "movie_key", "") or getattr(meta, "tmdb_kind", "") == "movie"):
                share_type = "video.movie"
            elif mime_type == "video":
                share_type = "video.other"

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
            audio_format=audio_format,
            audio_bit_depth=getattr(meta, "audio_bit_depth", 0) if meta else 0,
            audio_sample_rate=getattr(meta, "audio_sample_rate", 0) if meta else 0,
            heading=heading,
            share_title=share_title,
            share_description=share_description,
            share_image=share_image,
            share_url=share_url,
            share_type=share_type,
            src=src,
            download_src=download_src,
            hls_src=hls_src,
            sub_path=sub_path,
            file_name=file_name,
            meta=meta,
            known_unplayable=known_unplayable,
            video_codec=(meta.video_codec if meta else "") or "",
            pix_fmt=(meta.pix_fmt if meta else "") or "",
            next_ep=next_ep,
            next_track=next_track,
            prev_track=prev_track,
            album_tracks=album_tracks,
            quality_variants=quality_variants,
            vlc_user_id=vlc_user_id,
            vlc_token=vlc_token,
            message_id=message_id,
        )
    file_name = _best_file_name(file_data.file_name, None)
    heading = f"Download {file_name}"
    return _DL_TEMPLATE.render(
        heading=heading,
        file_name=file_name,
        file_size=humanbytes(file_data.file_size),
        download_src=download_src,
    )
