# Taken from megadlbot_oss <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/webserver/__init__.py>
# Thanks to Eyaadh <https://github.com/eyaadh>

import os

from aiohttp import web

from .stream_routes import routes as stream_routes
from .hls_routes import routes as hls_routes


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def web_server():
    web_app = web.Application(client_max_size=30000000)
    web_app.add_routes([web.static("/static", STATIC_DIR)])
    # HLS routes must be added BEFORE the catch-all /{path:\S+} stream route
    # in stream_routes, otherwise /hls/... would match the byte-stream regex.
    web_app.add_routes(hls_routes)
    web_app.add_routes(stream_routes)
    return web_app
