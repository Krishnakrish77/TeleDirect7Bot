"""
HLS routes — playlist + per-segment ffmpeg streamer.

URL shape mirrors the existing /watch/{hash}{id} convention so the hash check
is identical and unchanged callers can build URLs predictably.

    GET /hls/{hash}{id}/playlist.m3u8
    GET /hls/{hash}{id}/seg-{n}.ts
"""

import asyncio
import json
import logging
import re
import time

from aiohttp import web

from main import StreamBot
from main.bot import multi_clients, work_loads
from main.server.exceptions import FIleNotFound, InvalidHash
from main.utils import hls, skeleton_cache, ByteStreamer


routes = web.RouteTableDef()
_PATH_RE = re.compile(r"^([a-zA-Z0-9_-]{6})(\d+)$")

_class_cache: dict = {}


def _parse_path(raw: str):
    m = _PATH_RE.match(raw)
    if not m:
        raise web.HTTPBadRequest(text="Malformed path")
    return m.group(1), int(m.group(2))


async def _resolve(message_id: int, secure_hash: str):
    """Returns (file_id, byte_streamer, client_index) and validates the hash."""
    index = min(work_loads, key=work_loads.get) if work_loads else 0
    client = multi_clients.get(index, StreamBot)
    streamer = _class_cache.get(client)
    if streamer is None:
        streamer = ByteStreamer(client)
        _class_cache[client] = streamer
    file_id = await streamer.get_file_properties(message_id)
    if file_id.unique_id[:6] != secure_hash:
        raise InvalidHash
    return file_id, streamer, index


@routes.get(r"/hls/{path:[^/]+}/playlist.m3u8")
async def hls_playlist(request: web.Request) -> web.Response:
    try:
        secure_hash, message_id = _parse_path(request.match_info["path"])
        file_id, streamer, index = await _resolve(message_id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)

    source_url = hls.internal_stream_url(secure_hash, message_id)
    probe = await hls.probe(message_id, source_url)

    if not probe.hls_compatible:
        logging.info(
            "HLS not compatible for msg %d (video=%s audio=%s duration=%s)",
            message_id, probe.video_codec, probe.audio_codec, probe.duration,
        )
        raise web.HTTPUnsupportedMediaType(text="HLS not supported for this codec")

    # Warm the skeleton cache in the background. By the time the browser asks
    # for segments, ffmpeg's header + Cues reads will hit our in-memory cache
    # instead of round-tripping to Telegram for every seek.
    asyncio.create_task(skeleton_cache.prefetch_skeleton(
        message_id, file_id.file_size, streamer, file_id, index
    ))

    seg_template = f"seg-{{n}}.ts"
    body = hls.build_playlist(probe, seg_template)
    return web.Response(
        text=body,
        content_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "public, max-age=300",
            "Access-Control-Allow-Origin": "*",
        },
    )


@routes.get(r"/hls/{path:[^/]+}/seg-{n:\d+}.ts")
async def hls_segment(request: web.Request) -> web.StreamResponse:
    try:
        secure_hash, message_id = _parse_path(request.match_info["path"])
        await _resolve(message_id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)

    n = int(request.match_info["n"])
    source_url = hls.internal_stream_url(secure_hash, message_id)
    probe = await hls.probe(message_id, source_url)
    if not probe.hls_compatible:
        raise web.HTTPUnsupportedMediaType(text="HLS not supported for this codec")
    if n >= probe.segment_count:
        raise web.HTTPNotFound(text="Segment out of range")

    start_sec = n * hls.SEGMENT_SECONDS
    # Final segment may be shorter than SEGMENT_SECONDS.
    duration_sec = min(hls.SEGMENT_SECONDS, probe.duration - start_sec)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "video/mp2t",
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)

    try:
        async for chunk in hls.stream_segment(
            source_url, start_sec, duration_sec, audio_codec=probe.audio_codec
        ):
            await response.write(chunk)
        await response.write_eof()
    except (ConnectionError, asyncio.CancelledError):
        # hls.js routinely cancels in-flight segment requests when seeking or
        # when its buffer is satisfied. The ffmpeg subprocess is cleaned up by
        # stream_segment's finally block.
        logging.debug("Client disconnected during segment %d for msg %d", n, message_id)
    except Exception:
        logging.exception("Error streaming segment %d for msg %d", n, message_id)

    return response


# --- Subtitles ---------------------------------------------------------

# In-memory cache for extracted WebVTT bytes. Keyed by (message_id, track).
_VTT_CACHE_TTL = 60 * 60  # 1h — source is immutable
_vtt_cache: dict = {}
_vtt_locks: dict = {}


@routes.get(r"/sub/{path:[^/]+}/list.json")
async def hls_sub_list(request: web.Request) -> web.Response:
    """Returns the available text-based subtitle tracks for a media file."""
    try:
        secure_hash, message_id = _parse_path(request.match_info["path"])
        await _resolve(message_id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)

    probe = await hls.probe(message_id, hls.internal_stream_url(secure_hash, message_id))
    tracks = [
        {
            "index": s.index,
            "language": s.language,
            "label": s.label,
            "codec": s.codec,
        }
        for s in probe.subtitles
    ]
    return web.json_response(
        tracks,
        headers={
            "Cache-Control": "public, max-age=300",
            "Access-Control-Allow-Origin": "*",
        },
    )


@routes.get(r"/sub/{path:[^/]+}/{track:\d+}.vtt")
async def hls_sub_vtt(request: web.Request) -> web.Response:
    try:
        secure_hash, message_id = _parse_path(request.match_info["path"])
        await _resolve(message_id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)

    track = int(request.match_info["track"])
    cache_key = (message_id, track)

    # Serve from cache if fresh.
    entry = _vtt_cache.get(cache_key)
    now = time.monotonic()
    if entry and (now - entry[0]) < _VTT_CACHE_TTL:
        data = entry[1]
    else:
        lock = _vtt_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            entry = _vtt_cache.get(cache_key)
            if entry and (time.monotonic() - entry[0]) < _VTT_CACHE_TTL:
                data = entry[1]
            else:
                # Verify track exists before spending an ffmpeg cycle on it.
                probe = await hls.probe(message_id, hls.internal_stream_url(secure_hash, message_id))
                if not any(s.index == track for s in probe.subtitles):
                    raise web.HTTPNotFound(text="subtitle track not found")
                data = await hls.extract_subtitle_vtt(
                    hls.internal_stream_url(secure_hash, message_id), track
                )
                if not data:
                    raise web.HTTPInternalServerError(text="subtitle extraction failed")
                _vtt_cache[cache_key] = (time.monotonic(), data)

    return web.Response(
        body=data,
        content_type="text/vtt",
        charset="utf-8",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
            "Content-Length": str(len(data)),
        },
    )
