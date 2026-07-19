# Taken from megadlbot_oss <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/webserver/__init__.py>
# Thanks to Eyaadh <https://github.com/eyaadh>

import gzip
import logging
import os
from pathlib import Path

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .stream_routes import routes as stream_routes
from .hls_routes import routes as hls_routes
from .hub_routes import routes as hub_routes
from .admin_routes import routes as admin_routes
from .auth_routes import routes as auth_routes
from .watchlist_routes import routes as watchlist_routes
from .cw_routes import routes as cw_routes
from .wh_routes import routes as wh_routes
from .ratings_routes import routes as ratings_routes
from .dismiss_routes import routes as dismiss_routes
from .stats_routes import routes as stats_routes
from .iptv_routes import routes as iptv_routes
# NB: alias the route table to a distinct name — binding it to `spa_routes`
# would shadow the `main.server.spa_routes` submodule, so `from main.server
# import spa_routes` elsewhere would return this RouteTableDef instead of the
# module (breaking spa_routes._card / .invalidate_api_cache lookups).
from .spa_routes import routes as spa_route_table
from .ai_rec_routes import routes as ai_rec_routes


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_error_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=True,
)


# Default copy for each HTTP status we surface. Handlers can override
# by setting exc.text / exc.reason; the middleware falls back to these
# when nothing more specific is provided.
_DEFAULTS = {
    400: ("Bad request", "The request didn't make sense to the server."),
    403: ("Access denied", "You don't have permission to view this page."),
    404: ("Page not found", "We couldn't find what you were looking for."),
    405: ("Method not allowed", "That action isn't supported here."),
    415: ("Unsupported format", "This media format isn't streamable in the browser."),
    500: ("Something went wrong", "An error occurred on our side. Try again in a moment."),
    502: ("Upstream error", "A downstream service didn't respond. Try again shortly."),
    503: ("Service unavailable", "The server is starting up or overloaded. Try again shortly."),
    504: ("Timeout", "The server took too long to respond. Try again shortly."),
}


async def render_error(status: int, message: str = "",
                       title: str = "",
                       action_href: str = "",
                       action_label: str = "") -> web.Response:
    """Render the shared error template at the given status code."""
    default_title, default_msg = _DEFAULTS.get(status, ("Error", ""))
    tpl = _error_env.get_template("error.html")
    body = await tpl.render_async(
        status=status,
        title=title or default_title,
        message=message or default_msg,
        action_href=action_href,
        action_label=action_label,
    )
    return web.Response(
        text=body,
        status=status,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _wants_html(request: web.Request) -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    # Browser navigation requests always carry text/html. API clients
    # (curl, fetch with explicit JSON accept) get the raw aiohttp text
    # so they don't have to parse our chrome.
    if "text/html" in accept:
        return True
    return accept == ""  # default — likely a browser anyway


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Wrap HTTPException responses with the styled error template.

    Catches anything in the 4xx/5xx range raised from a handler and, if
    the client looks like a browser, re-renders the response body
    through ``error.html`` while preserving the status code. API-shaped
    clients get the original plain-text body. Unhandled exceptions
    surface as 500 with the same treatment.
    """
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        if exc.status < 400 or not _wants_html(request):
            raise
        # Use the HTTPException's text/reason as the message when it's
        # something other than the boilerplate Python sets by default.
        message = ""
        if exc.text and exc.text != exc.reason:
            message = exc.text
        return await render_error(exc.status, message=message)
    except Exception:
        logging.exception("Unhandled error in %s %s", request.method, request.path)
        if _wants_html(request):
            return await render_error(500)
        raise

    if response.status >= 400 and _wants_html(request):
        # Plain-text responses produced via web.Response(status=...)
        # rather than raised exceptions — still wrap them.
        ctype = (response.content_type or "").lower()
        if ctype.startswith("text/plain") or not response.body:
            return await render_error(response.status, message=response.text or "")
    return response


@web.middleware
async def mongo_readiness_middleware(request: web.Request, handler):
    """Serve a deliberate maintenance response while Mongo reconnects."""
    if request.path == "/healthz" or request.path.startswith("/static/"):
        return await handler(request)
    from main.utils import media_index
    if not media_index.store_ready():
        message = "The media catalogue is reconnecting. Please try again shortly."
        if _wants_html(request):
            return await render_error(
                503, message=message, title="Temporarily unavailable",
                action_href="/app", action_label="Try again",
            )
        return web.json_response({"error": "catalogue unavailable", "retry": True}, status=503)
    return await handler(request)


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


_SECURITY_HEADERS = {
    # Prevent MIME-type sniffing — browsers must respect Content-Type.
    "X-Content-Type-Options": "nosniff",
    # Block legacy browsers from rendering the page inside a frame
    # (clickjacking protection for the admin panel).
    "X-Frame-Options": "SAMEORIGIN",
    # Don't send the full Referrer URL to third-party origins (leaks
    # file IDs and user tokens embedded in paths).
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Basic XSS filter for old IE/Edge (belt-and-suspenders alongside CSP).
    "X-XSS-Protection": "1; mode=block",
}


@web.middleware
async def security_middleware(request: web.Request, handler):
    """Attach security headers to every HTML/JSON response.

    Stream responses (byte-range audio/video) are left untouched so
    the browser media pipeline is not disrupted.  HLS variant and
    segment responses skip HSTS to avoid issues with local/dev proxies.
    """
    from main.vars import Var as _Var
    response = await handler(request)
    # Cache-Control for static assets — web.static() serves these as
    # FileResponse (a StreamResponse), so they'd otherwise fall through the
    # isinstance guard below with no caching hint and revalidate (304) on
    # every navigation.  Vite content-hashes files under /static/app/assets/,
    # so those URLs never change → cache them for a year, immutable.  Other
    # static files (icons, manifest) get a short revalidation window.
    if request.path.startswith("/static/"):
        if "/assets/" in request.path:
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        else:
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
    # Don't modify streaming or binary responses.
    if not isinstance(response, web.Response):
        return response
    ctype = (response.content_type or "").lower()
    if not (ctype.startswith("text/") or ctype.startswith("application/")):
        return response
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if ctype.startswith("text/html"):
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    # HSTS — only on HTTPS deployments.  Tell browsers to always use
    # HTTPS for this origin for the next year.
    if _Var.HAS_SSL or getattr(_Var, "ON_KOYEB", False):
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


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
    # error_middleware runs OUTSIDE gzip so the rendered HTML still
    # benefits from compression on the way out.
    web_app = web.Application(
        client_max_size=30000000,
        middlewares=[error_middleware, security_middleware, gzip_middleware, mongo_readiness_middleware],
    )
    # Order matters: specific prefixes (hub, hls) first so they don't get
    # swallowed by the catch-all /{path:\S+} byte-stream route at the end.
    web_app.add_routes(auth_routes)
    web_app.add_routes(watchlist_routes)
    web_app.add_routes(cw_routes)
    web_app.add_routes(wh_routes)
    web_app.add_routes(ratings_routes)
    web_app.add_routes(dismiss_routes)
    web_app.add_routes(stats_routes)
    web_app.add_routes(iptv_routes)
    web_app.add_routes(spa_route_table)
    web_app.add_routes(ai_rec_routes)
    web_app.add_routes(admin_routes)
    web_app.add_routes(hub_routes)
    web_app.add_routes(hls_routes)
    if os.path.isdir(STATIC_DIR):
        web_app.add_routes([web.static("/static", STATIC_DIR)])
    web_app.add_routes(stream_routes)
    return web_app
