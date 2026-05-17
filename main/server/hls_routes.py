"""
HLS routes — playlist + per-segment ffmpeg streamer.

URL shape mirrors the existing /watch/{hash}{id} convention so the hash check
is identical and unchanged callers can build URLs predictably.

    GET /hls/{hash}{id}/playlist.m3u8
    GET /hls/{hash}{id}/seg-{n}.ts
"""

import logging
import re

from aiohttp import web

from main import StreamBot
from main.bot import multi_clients, work_loads
from main.server.exceptions import FIleNotFound, InvalidHash
from main.utils import hls, ByteStreamer


routes = web.RouteTableDef()
_PATH_RE = re.compile(r"^([a-zA-Z0-9_-]{6})(\d+)$")

_class_cache: dict = {}


def _parse_path(raw: str):
    m = _PATH_RE.match(raw)
    if not m:
        raise web.HTTPBadRequest(text="Malformed path")
    return m.group(1), int(m.group(2))


async def _file_id_for(message_id: int, secure_hash: str):
    # Reuse the same per-client ByteStreamer cache used by the byte-streaming
    # routes so we don't re-fetch FileId for the same message.
    index = min(work_loads, key=work_loads.get) if work_loads else 0
    client = multi_clients.get(index, StreamBot)
    streamer = _class_cache.get(client)
    if streamer is None:
        streamer = ByteStreamer(client)
        _class_cache[client] = streamer
    file_id = await streamer.get_file_properties(message_id)
    if file_id.unique_id[:6] != secure_hash:
        raise InvalidHash
    return file_id


@routes.get(r"/hls/{path:[^/]+}/playlist.m3u8")
async def hls_playlist(request: web.Request) -> web.Response:
    try:
        secure_hash, message_id = _parse_path(request.match_info["path"])
        await _file_id_for(message_id, secure_hash)
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
        await _file_id_for(message_id, secure_hash)
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
        async for chunk in hls.stream_segment(source_url, start_sec, duration_sec):
            await response.write(chunk)
    except ConnectionResetError:
        # Client disconnected mid-stream — ffmpeg subprocess is cleaned up
        # inside stream_segment's finally block.
        logging.debug("Client disconnected during segment %d for msg %d", n, message_id)
    except Exception:
        logging.exception("Error streaming segment %d for msg %d", n, message_id)

    await response.write_eof()
    return response
