# Taken from megadlbot_oss <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/webserver/__init__.py>
# Thanks to Eyaadh <https://github.com/eyaadh>

from aiohttp import web
from .stream_routes import routes
import os

# Define the path to your static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

def web_server():
    web_app = web.Application(client_max_size=30000000)
    web_app.add_routes([web.static('/static', STATIC_DIR)])
    routes.static('/static', STATIC_DIR)
    web_app.add_routes(routes)
    return web_app
