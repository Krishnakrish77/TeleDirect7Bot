import os
import sys
import signal
import asyncio
import logging
from logging.handlers import RotatingFileHandler

# Replace asyncio's default event loop with uvloop — Cython-based, ~2–4×
# faster on event-loop ops. aiohttp + pyrogram pick it up transparently.
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

from .vars import Var
from aiohttp import web
from pyrogram import idle
from main import utils
from main import StreamBot
from main.server import web_server
from main.bot.clients import initialize_clients
from main.utils import hls_session, media_index


_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    datefmt="%d/%m/%Y %H:%M:%S",
    format='[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(stream=sys.stdout),
              RotatingFileHandler("streambot.log", mode="a", maxBytes=10 * 1024 * 1024,
                                  backupCount=3, encoding="utf-8")],)

logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)

server = web.AppRunner(web_server())


async def start_services():
    print()
    print("-------------------- Initializing Telegram Bot --------------------")
    await StreamBot.start()
    bot_info = await StreamBot.get_me()
    StreamBot.username = bot_info.username
    print("------------------------------ DONE ------------------------------")
    print()
    print("---------------------- Initializing Clients ----------------------")
    await initialize_clients()
    print("------------------------------ DONE ------------------------------")
    # Initialise the durable store (Mongo when STORE_BACKEND=mongo) BEFORE
    # seed() runs — seed will preload from the store when available and
    # skip the pinned-snapshot dance.
    await media_index.init_store()
    # Seed the hub's in-process catalogue from BIN_CHANNEL history. Runs in
    # the background so it doesn't block web server startup; the hub starts
    # empty and fills in over the next ~30s as message metadata streams in.
    asyncio.create_task(media_index.seed(StreamBot, Var.BIN_CHANNEL))
    # Start the HLS-session reaper so idle ffmpeg processes + their /tmp
    # segment dirs get freed.
    hls_session.ensure_reaper_running()
    if Var.ON_KOYEB:
        print("------------------ Starting Keep Alive Service ------------------")
        print()
        asyncio.create_task(utils.ping_server())
    print("--------------------- Initalizing Web Server ---------------------")
    await server.setup()
    bind_address = "0.0.0.0" if Var.ON_KOYEB else Var.BIND_ADDRESS
    await web.TCPSite(server, bind_address, Var.PORT).start()
    print("------------------------------ DONE ------------------------------")
    print()
    print("------------------------- Service Started -------------------------")
    print("                        bot =>> {}".format(bot_info.first_name))
    if bot_info.dc_id:
        print("                        DC ID =>> {}".format(str(bot_info.dc_id)))
    print("                        server ip =>> {}:{}".format(bind_address, Var.PORT))
    if Var.ON_KOYEB:
        print("                        app running on =>> {}".format(Var.FQDN))
    print("------------------------------------------------------------------")
    print()
    print("""
 _____________________________________________
|                                             |
|          Deployed Successfully              |
|              Join @TeleDirect7Bot           |
|_____________________________________________|
    """)
    await idle()


async def cleanup():
    # Kill in-flight ffmpeg subprocesses and free their /tmp dirs before
    # stopping the bot so we don't orphan disk or processes.
    try:
        await asyncio.wait_for(hls_session.shutdown_all(), timeout=10)
    except Exception:
        logging.warning("hls_session shutdown_all errored or timed out", exc_info=True)
    await server.cleanup()
    await StreamBot.stop()


def _request_shutdown():
    logging.info("Received shutdown signal, stopping...")
    for task in asyncio.all_tasks():
        task.cancel()


async def main():
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    try:
        await start_services()
    finally:
        await cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as err:
        logging.exception("Fatal error during startup", exc_info=err)
    finally:
        print("------------------------ Stopped Services ------------------------")
