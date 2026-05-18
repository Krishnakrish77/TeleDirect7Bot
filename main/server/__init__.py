# Taken from megadlbot_oss <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/webserver/__init__.py>
# Thanks to Eyaadh <https://github.com/eyaadh>

import gzip
import os

from aiohttp import web

from .stream_routes import routes as stream_routes
from .hls_routes import routes as hls_routes
from .hub_routes import routes as hub_routes
from .admin_routes import routes as admin_routes


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Don't waste CPU compressing payloads smaller than this — gzip headers
# themselves cost ~25 bytes.
GZIP_MIN_BYTES = 1024

# Content-Types that gzip well. Skip already-compressed binaries.
_GZIPPABLE = (
    "text/",
    "application/javascript",
    "application/json",
    "application/xml",
    "application/vnd.apple.mpegurl",
    "image/svg+xml",
)


@web.middleware
async def gzip_middleware(request: web.Request, handler):
    response: web.StreamResponse = await handler(request)

    # Only compress regular Response bodies (not StreamResponse, which we
    # use for HLS segments — those are already binary TS bytes).
    if not isinstance(response, web.Response):
        return response
    if response.body is None:
        return response
    if "gzip" not in request.headers.get("Accept-Encoding", ""):
        return response

    ctype = (response.content_type or "").lower()
    if not any(ctype.startswith(p) for p in _GZIPPABLE):
        return response

    body = response.body
    if isinstance(body, web.Response):  # paranoia; shouldn't happen
        return response
    raw = bytes(body) if not isinstance(body, (bytes, bytearray)) else bytes(body)
    if len(raw) < GZIP_MIN_BYTES:
        return response

    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    response.body = compressed
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(compressed))
    vary = response.headers.get("Vary")
    response.headers["Vary"] = f"{vary}, Accept-Encoding" if vary else "Accept-Encoding"
    return response


def web_server():
    web_app = web.Application(client_max_size=30000000, middlewares=[gzip_middleware])
    # Order matters: specific prefixes (hub, hls) first so they don't get
    # swallowed by the catch-all /{path:\S+} byte-stream route at the end.
    web_app.add_routes(admin_routes)
    web_app.add_routes(hub_routes)
    web_app.add_routes(hls_routes)
    if os.path.isdir(STATIC_DIR):
        web_app.add_routes([web.static("/static", STATIC_DIR)])
    web_app.add_routes(stream_routes)
    return web_app
