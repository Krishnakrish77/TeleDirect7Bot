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
    if Var.ON_HEROKU:
        print("------------------ Starting Keep Alive Service ------------------")
        print()
        asyncio.create_task(utils.ping_server())
    print("--------------------- Initalizing Web Server ---------------------")
    await server.setup()
    bind_address = "0.0.0.0" if Var.ON_HEROKU else Var.BIND_ADDRESS
    await web.TCPSite(server, bind_address, Var.PORT).start()
    print("------------------------------ DONE ------------------------------")
    print()
    print("------------------------- Service Started -------------------------")
    print("                        bot =>> {}".format(bot_info.first_name))
    if bot_info.dc_id:
        print("                        DC ID =>> {}".format(str(bot_info.dc_id)))
    print("                        server ip =>> {}:{}".format(bind_address, Var.PORT))
    if Var.ON_HEROKU:
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
