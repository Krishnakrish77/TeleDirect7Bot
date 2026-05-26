# Taken from megadlbot_oss <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/webserver/routes.py>
# Thanks to Eyaadh <https://github.com/eyaadh>

import os
import re
import time
import math
import logging
import secrets
import mimetypes
import weakref
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from main.bot import multi_clients, work_loads
from main.server.exceptions import FIleNotFound, InvalidHash
from main import Var, utils, StartTime, __version__, StreamBot
from main.utils import skeleton_cache
from main.utils.render_template import render_page


routes = web.RouteTableDef()

# ── Stream rate limiting ──────────────────────────────────────────────────
# Only actual Telegram GetFile calls are counted; skeleton cache hits
# (served from memory) are free and not subject to these limits.
_MAX_STREAMS_TOTAL = int(os.environ.get("MAX_STREAMS_TOTAL", "25"))
_MAX_STREAMS_PER_IP = int(os.environ.get("MAX_STREAMS_PER_IP", "8"))
_total_active: int = 0
_ip_active: dict = {}   # ip → concurrent stream count


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _real_ip(request: web.Request) -> str:
    """Return the real client IP, reading X-Forwarded-For when behind a proxy.

    Koyeb (and most LBs) set X-Forwarded-For to the original client IP.
    Without this, request.remote is always the LB node IP, making the
    per-IP limit a global cap shared by all users.

    Loopback addresses (127.0.0.1, ::1) are returned as-is so HLS ffmpeg
    loopback fetches can be handled separately at the call site.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip() or request.remote or "unknown"
    return request.remote or "unknown"


async def _rate_limited_body(gen, ip: str):
    """Wrap a yield_file generator — ONLY decrements; caller already incremented."""
    try:
        async for chunk in gen:
            yield chunk
    finally:
        global _total_active
        _total_active = max(0, _total_active - 1)
        _ip_active[ip] = max(0, _ip_active.get(ip, 1) - 1)
        if not _ip_active.get(ip):
            _ip_active.pop(ip, None)

@routes.get("/healthz", allow_head=True)
async def healthz(_):
    """Liveness + readiness check for orchestrators.

    Returns 200 once the seed has finished AND the catalogue has
    items; 503 while still seeding (so Koyeb's health probe holds
    traffic out until we're ready). Always returns JSON so it's
    inspectable in a browser.
    """
    from main.utils import media_index
    seed = media_index.seed_state()
    cat_size = media_index.size()
    ready = (not seed.get("running")) and (seed.get("finished_at", 0) > 0) and cat_size > 0
    body = {
        "status": "ok" if ready else "starting",
        "catalogue_size": cat_size,
        "seed_running": bool(seed.get("running")),
        "seed_finished": seed.get("finished_at", 0) > 0,
        "uptime_s": int(time.time() - StartTime),
    }
    return web.json_response(body, status=200 if ready else 503,
                             headers={"Cache-Control": "no-store"})


@routes.get("/status", allow_head=True)
async def status_route_handler(_):
    return web.json_response(
        {
            "server_status": "running",
            "uptime": utils.get_readable_time(time.time() - StartTime),
            "telegram_bot": "@" + StreamBot.username,
            "connected_bots": len(multi_clients),
            "loads": dict(
                ("bot" + str(c + 1), l)
                for c, (_, l) in enumerate(
                    sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                )
            ),
            "version": __version__,
        }
    )

@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        # Hash is everything before the trailing digit run (message_id).
        # File unique_ids can be 6, 15, 16+ chars — accept any hash
        # ending with a non-digit char followed by the numeric message_id.
        match = re.search(r"^([A-Za-z0-9_-]*[A-Za-z_-])(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            message_id = int(match.group(2))
        else:
            message_id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
            secure_hash = request.rel_url.query.get("hash")
        return web.Response(text=await render_page(message_id, secure_hash), content_type='text/html')
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except web.HTTPException:
        # We raised this ourselves with a deliberate status code (e.g. 503
        # for a truncated skeleton fetch). Let aiohttp propagate it instead
        # of re-wrapping as 500.
        raise
    except (AttributeError, BadStatusLine, ConnectionResetError):
        return web.HTTPInternalServerError(text="A server error occurred.")
    except Exception as e:
        logging.exception("Unhandled server error")
        raise web.HTTPInternalServerError(text="A server error occurred.")

@routes.get(r"/{path:\S+}", allow_head=True)
async def stream_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        # Stream URLs are always a single path segment: {6chars}{digits}.
        # Any path with a slash is a named route that reached the catch-all
        # accidentally — return 404 rather than trying to parse it as a stream.
        if '/' in path:
            raise web.HTTPNotFound()
        # Hash is everything before the trailing digit run (message_id).
        # File unique_ids can be 6, 15, 16+ chars — accept any hash
        # ending with a non-digit char followed by the numeric message_id.
        match = re.search(r"^([A-Za-z0-9_-]*[A-Za-z_-])(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            message_id = int(match.group(2))
        else:
            message_id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
            secure_hash = request.rel_url.query.get("hash")
        return await media_streamer(request, message_id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except web.HTTPException:
        # We raised this ourselves with a deliberate status code (e.g. 503
        # for a truncated skeleton fetch). Let aiohttp propagate it instead
        # of re-wrapping as 500.
        raise
    except (AttributeError, BadStatusLine, ConnectionResetError):
        return web.HTTPInternalServerError(text="A server error occurred.")
    except Exception as e:
        logging.exception("Unhandled server error")
        raise web.HTTPInternalServerError(text="A server error occurred.")

class_cache = weakref.WeakKeyDictionary()

async def media_streamer(request: web.Request, message_id: int, secure_hash: str):
    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]

    if Var.MULTI_CLIENT:
        logging.info(f"Client {index} is now serving {request.remote}")

    if faster_client in class_cache:
        tg_connect = class_cache[faster_client]
        logging.debug(f"Using cached ByteStreamer object for client {index}")
    else:
        logging.debug(f"Creating new ByteStreamer object for client {index}")
        tg_connect = utils.ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect
    file_id = await tg_connect.get_file_properties(message_id)

    if not secure_hash or file_id.unique_id[:len(secure_hash)] != secure_hash:
        logging.debug(f"Invalid hash for message with ID {message_id}")
        raise InvalidHash

    file_size = file_id.file_size

    from_bytes = request.http_range.start or 0
    until_bytes = (request.http_range.stop or file_size) - 1
    range_header = "Range" in request.headers

    mime_type = file_id.mime_type
    file_name = file_id.file_name
    disposition = "attachment"
    if mime_type:
        if not file_name:
            try:
                file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"
            except (IndexError, AttributeError):
                file_name = f"{secrets.token_hex(2)}.unknown"
    else:
        if file_name:
            mime_type = mimetypes.guess_type(file_id.file_name)[0] or "application/octet-stream"
        else:
            mime_type = "application/octet-stream"
            file_name = f"{secrets.token_hex(2)}.unknown"
    if "video/" in mime_type or "audio/" in mime_type:
        disposition = "inline"

    common_headers = {
        "Content-Type": f"{mime_type}",
        "Range": f"bytes={from_bytes}-{until_bytes}",
        "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
        "Content-Disposition": f'{disposition}; filename="{file_name}"',
        "Accept-Ranges": "bytes",
    }

    # Fast path: if the requested range fits entirely inside the cached file
    # header or tail, serve from memory and skip the Telegram round-trip.
    # ffmpeg hits these regions on every seek (MKV header + Cues block) so
    # this turns N round-trips into 1 per file.
    #
    # SkeletonFetchError means yield_file timed out mid-stream and we'd be
    # caching truncated bytes — surface a 503 so the ffmpeg side reconnects
    # rather than serving a Content-Range that overpromises the body length.
    cached_body = None
    try:
        if until_bytes <= skeleton_cache.head_limit(file_size):
            cached_body = skeleton_cache.serve_head(message_id, from_bytes, until_bytes)
            if cached_body is None:
                head = await skeleton_cache.get_or_fetch_head(
                    message_id, file_size, tg_connect, file_id, index
                )
                cached_body = head[from_bytes : until_bytes + 1]
        elif from_bytes >= skeleton_cache.tail_floor(file_size):
            cached_body = skeleton_cache.serve_tail(message_id, from_bytes, until_bytes)
            if cached_body is None:
                tail = await skeleton_cache.get_or_fetch_tail(
                    message_id, file_size, tg_connect, file_id, index
                )
                t_start = file_size - len(tail)
                cached_body = tail[from_bytes - t_start : until_bytes - t_start + 1]
    except skeleton_cache.SkeletonFetchError as exc:
        logging.warning("skeleton fetch failed for msg %d: %s", message_id, exc)
        raise web.HTTPServiceUnavailable(text="skeleton fetch incomplete; retry")

    if cached_body is not None:
        return web.Response(
            status=206 if range_header else 200,
            body=cached_body,
            headers={**common_headers, "Content-Length": str(len(cached_body))},
        )

    # For a full-file GET (no Range header, from_bytes=0): serve only the
    # skeleton HEAD (first 2 MB) as a 206 partial response showing the real
    # Content-Range. This prevents streaming 200-300 sequential GetFile calls
    # to Telegram (which Telegram drops), while giving ffprobe/ffmpeg enough
    # bytes to identify the container format. They will then make targeted
    # Range requests for the MOOV/Cues block (served from the tail cache) and
    # the actual seek position — no full-file stream required.
    if not range_header and from_bytes == 0 and file_size > skeleton_cache.HEAD_SIZE:
        try:
            head = await skeleton_cache.get_or_fetch_head(
                message_id, file_size, tg_connect, file_id, index
            )
        except skeleton_cache.SkeletonFetchError as exc:
            logging.warning("skeleton head fetch failed for msg %d: %s", message_id, exc)
            raise web.HTTPServiceUnavailable(text="skeleton fetch incomplete; retry")
        head_end = len(head) - 1
        return web.Response(
            status=206,
            body=head,
            headers={
                **common_headers,
                "Content-Range": f"bytes 0-{head_end}/{file_size}",
                "Content-Length": str(len(head)),
            },
        )

    # Rate limit: only actual Telegram GetFile calls count.
    # Skeleton cache hits (served above) are memory-only and exempt.
    # Increment immediately after check — no await between check and increment
    # to close the asyncio race window (Finding 1).
    client_ip = _real_ip(request)
    is_loopback = client_ip in _LOOPBACK   # HLS ffmpeg self-fetches: skip per-IP limit
    if _total_active >= _MAX_STREAMS_TOTAL:
        raise web.HTTPServiceUnavailable(
            text="Server is at stream capacity. Try again shortly.",
            headers={"Retry-After": "10"},
        )
    if not is_loopback and _ip_active.get(client_ip, 0) >= _MAX_STREAMS_PER_IP:
        raise web.HTTPTooManyRequests(
            text="Too many concurrent streams from this IP.",
            headers={"Retry-After": "5", "X-RateLimit-Limit": str(_MAX_STREAMS_PER_IP)},
        )
    # Increment BEFORE any further await so no other coroutine can slip through
    # the same check window.
    global _total_active
    _total_active += 1
    if not is_loopback:
        _ip_active[client_ip] = _ip_active.get(client_ip, 0) + 1

    req_length = until_bytes - from_bytes
    new_chunk_size = utils.chunk_size(req_length)
    offset = utils.offset_fix(from_bytes, new_chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = (until_bytes % new_chunk_size) + 1
    # Count chunks by start/end chunk index, not by ceil(req_length/chunk).
    # The latter form ignores first_part_cut, so a Range starting mid-chunk
    # (e.g. ffmpeg seeking to moov-at-end at byte 213268712 inside a 1 MB
    # chunk grid) is short by one chunk — yield_file then closes the stream
    # before delivering the trailing bytes the Content-Range header claimed,
    # and ffmpeg hits AVERROR_EOF mid-moov-parse. Counting chunks by index
    # always covers [from_bytes, until_bytes] exactly.
    part_count = (until_bytes // new_chunk_size) - (from_bytes // new_chunk_size) + 1
    body = _rate_limited_body(
        tg_connect.yield_file(
            file_id, index, offset, first_part_cut, last_part_cut, part_count, new_chunk_size
        ),
        client_ip,
    )

    return_resp = web.Response(
        status=206 if range_header else 200,
        body=body,
        headers=common_headers,
    )

    if return_resp.status == 200:
        return_resp.headers.add("Content-Length", str(file_size))

    return return_resp
