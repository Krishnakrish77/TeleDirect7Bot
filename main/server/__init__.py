# Taken from megadlbot_oss <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/webserver/__init__.py>
# Thanks to Eyaadh <https://github.com/eyaadh>

import os

from aiohttp import web

from .stream_routes import routes as stream_routes
from .hls_routes import routes as hls_routes
from .hub_routes import routes as hub_routes


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def web_server():
    web_app = web.Application(client_max_size=30000000)
    # Order matters: specific prefixes (hub, hls) first so they don't get
    # swallowed by the catch-all /{path:\S+} byte-stream route at the end.
    web_app.add_routes(hub_routes)
    web_app.add_routes(hls_routes)
    if os.path.isdir(STATIC_DIR):
        web_app.add_routes([web.static("/static", STATIC_DIR)])
    web_app.add_routes(stream_routes)
    return web_app
