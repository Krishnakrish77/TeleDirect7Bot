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
from main.utils import hls, hls_session, media_index, skeleton_cache, ByteStreamer
from main.utils.subtitles import srt_to_vtt
from main.vars import Var


routes = web.RouteTableDef()
_PATH_RE = re.compile(r"^([A-Za-z0-9_-]*[A-Za-z_-])(\d+)$")

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
    if file_id.unique_id[:len(secure_hash)] != secure_hash:
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

    # Clamp to the actual track count so a rogue ?a=99 on a video-only file
    # can't create unbounded (message_id, N) session keys.
    try:
        _a = int(request.query.get("a", "0"))
    except ValueError:
        _a = 0
    audio_index = max(0, min(_a, len(probe.audio_tracks) - 1)) if probe.audio_tracks else 0

    seg_template = f"seg-{{n}}.ts?a={audio_index}"
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

    # Clamp to the actual track count so a rogue ?a=99 on a video-only file
    # can't create unbounded (message_id, N) session keys.
    try:
        _a = int(request.query.get("a", "0"))
    except ValueError:
        _a = 0
    audio_index = max(0, min(_a, len(probe.audio_tracks) - 1)) if probe.audio_tracks else 0

    # One long-lived ffmpeg per (file, audio_track) produces segments to /tmp;
    # we just serve the file when it's on disk. Backward seeks within already-
    # produced segments are free; forward seek beyond the current cursor
    # restarts ffmpeg from the seek point.
    session = await hls_session.get_or_start(
        message_id, source_url, probe.duration, probe.audio_codec,
        audio_index=audio_index,
        transcode_video=probe.needs_video_transcode,
    )
    seg_path = await session.request(n)
    if seg_path is None:
        logging.warning("hls_session msg=%d segment %d timed out", message_id, n)
        raise web.HTTPGatewayTimeout(text="segment generation timed out")

    return web.FileResponse(
        path=seg_path,
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
            "Content-Type": "video/mp2t",
        },
    )


# --- Audio track list --------------------------------------------------

@routes.get(r"/hls/{path:[^/]+}/audio-list.json")
async def hls_audio_list(request: web.Request) -> web.Response:
    """Return available audio tracks for a media file.

    Returns [] when there is only one audio track (no UI needed).
    """
    try:
        secure_hash, message_id = _parse_path(request.match_info["path"])
        await _resolve(message_id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)

    probe = await hls.probe(message_id, hls.internal_stream_url(secure_hash, message_id))

    # Only expose the list when there are multiple tracks — single-track
    # files return [] so the client knows not to render any switcher UI.
    if len(probe.audio_tracks) <= 1:
        tracks = []
    else:
        tracks = [
            {
                "index": t.index,
                "language": t.language,
                "label": t.label,
                "codec": t.codec,
            }
            for t in probe.audio_tracks
        ]

    return web.json_response(
        tracks,
        headers={
            "Cache-Control": "public, max-age=300",
            "Access-Control-Allow-Origin": "*",
        },
    )


# --- Subtitles ---------------------------------------------------------

# In-memory cache for extracted WebVTT bytes. Keyed by (message_id, track).
_VTT_CACHE_TTL = 60 * 60  # 1h — source is immutable
_vtt_cache: dict = {}
_vtt_locks: dict = {}
# In-flight extraction tasks, keyed by (message_id, track). Detached from any
# single request so a client disconnect / gateway 504 doesn't kill ffmpeg —
# extraction finishes and caches, so a reload serves it instantly.
_vtt_tasks: dict = {}
# Wait this long for extraction before returning 503. Kept under Koyeb's ~120s
# edge timeout so the response flushes; the task keeps running past it.
_VTT_WAIT_BUDGET = 100


def _sub_store_key(message_id: int, track: int) -> str:
    return f"{message_id}:{track}"


async def _extract_vtt_cached(cache_key, secure_hash: str, message_id: int, track: int):
    """Return a subtitle track as WebVTT from L2 (Mongo) or by extracting.

    A large MKV subrip track requires ffmpeg to demux the whole (Telegram-
    streamed) file, which can take minutes. Runs as a detached task so it
    survives request cancellation. Persists to Mongo so the demux is paid
    once ever, not once per Koyeb restart.
    """
    store = _sub_store()
    store_key = _sub_store_key(message_id, track)

    # L2 hit — hydrate L1 and return.
    if store is not None:
        try:
            persisted = await store.get_subtitle(store_key)
        except Exception:
            persisted = None
        if persisted:
            _vtt_cache[cache_key] = (time.monotonic(), persisted)
            return persisted

    src = hls.internal_stream_url(secure_hash, message_id)
    probe = await hls.probe(message_id, src)
    if not any(s.index == track for s in probe.subtitles):
        return None
    data = await hls.extract_subtitle_vtt(src, track)
    if not data:
        return None
    _vtt_cache[cache_key] = (time.monotonic(), data)
    if store is not None:
        try:
            await store.set_subtitle(store_key, data)
        except Exception:
            logging.exception("hls: subtitle L2 persist failed for %s", store_key)
    return data


def _sub_store():
    """Lazy MongoDB store accessor; None when Mongo isn't configured."""
    try:
        from main.utils import media_index
        return media_index._store
    except Exception:
        return None


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
            "id": str(s.index),
            "url": f"/sub/{secure_hash}{message_id}/{s.index}.vtt",
            "language": s.language,
            "label": s.label,
            "codec": s.codec,
            "kind": "embedded",
        }
        for s in probe.subtitles
    ]

    item = media_index.get_item(message_id)
    if item is not None:
        for s in item.subtitles:
            tracks.append({
                "id": f"ext-{s.bin_message_id}",
                "url": f"/sub/{secure_hash}{message_id}/ext-{s.bin_message_id}.vtt",
                "language": s.language,
                "label": s.label or (s.language.upper() if s.language else "External"),
                "codec": "",
                "kind": "external",
            })

    return web.json_response(
        tracks,
        headers={
            "Cache-Control": "public, max-age=60",
            "Access-Control-Allow-Origin": "*",
        },
    )


@routes.get(r"/sub/{path:[^/]+}/ext-{bin_id:\d+}.vtt")
async def hls_sub_external_vtt(request: web.Request) -> web.Response:
    """Serve an external sidecar .srt/.vtt as WebVTT.

    The secure_hash in the path authenticates the video; the bin_id must be
    listed in that video's HubItem.subtitles, otherwise the request is
    refused (prevents using one video's hash to read arbitrary BIN files).
    """
    try:
        secure_hash, message_id = _parse_path(request.match_info["path"])
        await _resolve(message_id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)

    bin_id = int(request.match_info["bin_id"])
    item = media_index.get_item(message_id)
    sub = None
    if item is not None:
        sub = next((s for s in item.subtitles if s.bin_message_id == bin_id), None)
    if sub is None:
        raise web.HTTPNotFound(text="subtitle not found for this video")

    cache_key = (message_id, f"ext-{bin_id}")
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
                try:
                    bytesio = await StreamBot.download_media(
                        message=await StreamBot.get_messages(Var.BIN_CHANNEL, bin_id),
                        in_memory=True,
                    )
                except Exception:
                    logging.exception("failed to download sidecar bin:%d", bin_id)
                    raise web.HTTPInternalServerError(text="subtitle fetch failed")
                if bytesio is None:
                    raise web.HTTPNotFound(text="subtitle source missing")
                raw = bytesio.getvalue() if hasattr(bytesio, "getvalue") else bytes(bytesio)
                data = srt_to_vtt(raw)
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
    if entry and (time.monotonic() - entry[0]) < _VTT_CACHE_TTL:
        data = entry[1]
    else:
        # Reuse an in-flight extraction, or start a detached one. ensure_future
        # schedules it on the loop independently of this request so a client
        # disconnect / gateway 504 won't kill ffmpeg mid-demux.
        task = _vtt_tasks.get(cache_key)
        if task is None or (task.done() and (task.cancelled()
                or task.exception() is not None or not task.result())):
            task = asyncio.ensure_future(
                _extract_vtt_cached(cache_key, secure_hash, message_id, track)
            )
            _vtt_tasks[cache_key] = task
        # Wait up to the budget WITHOUT cancelling the task on timeout (shield),
        # so big-file extractions keep running and cache for the next request.
        try:
            data = await asyncio.wait_for(asyncio.shield(task), timeout=_VTT_WAIT_BUDGET)
        except asyncio.TimeoutError:
            raise web.HTTPServiceUnavailable(
                text="subtitle still extracting; reload in a moment",
                headers={"Retry-After": "20", "Access-Control-Allow-Origin": "*"},
            )
        if not data:
            raise web.HTTPNotFound(text="subtitle track not found or extraction failed")

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
