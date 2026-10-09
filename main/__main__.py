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


async def _connect_catalogue_store() -> None:
    """Retry Mongo while the web server presents its 503 maintenance page."""
    delay = 2
    while not await media_index.init_store():
        logging.warning("Mongo unavailable; retrying in %ss", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
    if not Var.IS_LEADER:
        # Replicas restore the durable catalogue (needed to serve hub
        # routes) but skip the BIN probe/scan and reconciliation — the
        # leader owns those.
        logging.info("ROLE=replica: loading catalogue without BIN scan")
        try:
            await media_index.load_catalogue()
        except Exception:
            logging.exception("catalogue load failed on replica")

        async def replica_catalogue_refresh_loop() -> None:
            # Safety net for the leader's post-upload hook (which is
            # fire-and-forget): sweep Mongo for rows this replica has never
            # seen on a fixed interval. load_since makes a no-op sweep one
            # cheap indexed query.
            while True:
                await asyncio.sleep(300)
                try:
                    await media_index.refresh_from_store()
                except Exception:
                    logging.exception("replica catalogue refresh failed")

        asyncio.create_task(replica_catalogue_refresh_loop())
        return

    async def seed_then_reconcile() -> None:
        try:
            await media_index.seed(StreamBot, Var.BIN_CHANNEL)
        except Exception:
            logging.exception("Catalogue seed failed")
        finally:
            # The scheduler is deliberately started only after recovery. It
            # checks one bounded batch every interval; it never full-scans at boot.
            media_index.ensure_reconciliation_running(StreamBot, Var.BIN_CHANNEL)

    asyncio.create_task(seed_then_reconcile())


async def _photos_channel_reverify_loop() -> None:
    """Periodically re-verify bound photo channels (plan §4).

    Detects bot-kicked / ownership-transferred channels and flips their
    status so originals stop streaming and the UI shows the banner. Runs
    only while the catalogue store is connected; failures just delay the
    next pass.
    """
    from main.utils import photo_store
    from main.server.photo_routes import reverify_channel
    while True:
        await asyncio.sleep(3600)  # hourly
        try:
            db = photo_store._get_db()
            if db is None:
                continue
            # Page through ALL active bindings — no cap that silently
            # skips later channels.
            cursor = db["photo_channels"].find(
                {"status": "active"}, projection={"owner_user_id": 1}
            )
            stale = False
            async for row in cursor:
                try:
                    status = await reverify_channel(row["owner_user_id"])
                    if status and status != "active":
                        logging.warning(
                            "photos: channel for owner %s re-verified as %s",
                            row["owner_user_id"], status,
                        )
                except Exception:
                    logging.exception(
                        "photos: reverify failed for owner %s", row["owner_user_id"]
                    )
                    stale = True
                    break  # store likely down; resume next pass
            if stale:
                continue
        except Exception:
            logging.exception("photos: reverify loop pass failed")


async def _photos_boot_rescan() -> None:
    """Catch-up pass on startup: enqueue vault posts missed while the bot
    was down (channel_post only fires live). Waits for Mongo first —
    photo_store reports unavailable until the catalogue store connects.
    """
    from main.utils import photo_store
    from main.bot.plugins.photos import schedule_rescan
    for _ in range(30):  # up to ~5 min for Mongo to come up
        if photo_store.is_available():
            break
        await asyncio.sleep(10)
    else:
        return
    try:
        db = photo_store._get_db()
        cursor = db["photo_channels"].find(
            {"status": "active"},
            projection={"owner_user_id": 1, "channel_id": 1},
        )
        async for row in cursor:
            schedule_rescan(row["owner_user_id"], row["channel_id"])
    except Exception:
        logging.exception("photos: boot rescan failed")


async def _photos_geo_backfill() -> None:
    """Build GeoJSON points + place labels for photos ingested before the
    Places feature. Small batches, rescheduled hourly — a large library
    labels gradually rather than stalling boot. Runs after boot rescan so
    the scan's ingest queue isn't competing for the geocode budget."""
    from main.utils import photo_store
    for _ in range(30):  # up to ~5 min for Mongo to come up
        if photo_store.is_available():
            break
        await asyncio.sleep(10)
    else:
        return
    while True:
        try:
            db = photo_store._get_db()
            cursor = db["photo_channels"].find(
                {"status": "active"},
                projection={"owner_user_id": 1, "channel_id": 1},
            )
            total = 0
            async for row in cursor:
                total += await photo_store.backfill_geo(row["owner_user_id"], row["channel_id"])
                total += await photo_store.backfill_places(row["owner_user_id"], row["channel_id"])
            if total:
                logging.info("photos: geo backfill progressed %d docs this pass", total)
        except Exception:
            logging.exception("photos: geo backfill pass failed")
        await asyncio.sleep(3600)


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
    print("--------------------- Initalizing Web Server ---------------------")
    # A hard restart can leave completed/partial HLS segments in /tmp. They
    # cannot be safely resumed and would consume the next process's disk budget.
    hls_session.cleanup_orphaned_workdirs()
    await server.setup()
    bind_address = "0.0.0.0" if Var.ON_KOYEB else Var.BIND_ADDRESS
    await web.TCPSite(server, bind_address, Var.PORT).start()
    # The server is intentionally live before Mongo connects, so visitors get
    # a styled maintenance page rather than a platform-level connection error.
    asyncio.create_task(_connect_catalogue_store())
    # Leader-only jobs: reconciliation, photo reverify/rescan and hub
    # warmup all mutate shared state (Mongo, BIN_CHANNEL pins) — running
    # them on every replica would duplicate work and race writes.
    if Var.IS_LEADER:
        asyncio.create_task(utils.warm_hub_shelves())
        if Var.PHOTOS_ENABLED:
            asyncio.create_task(_photos_channel_reverify_loop())
            asyncio.create_task(_photos_boot_rescan())
            if Var.PHOTOS_PLACES:
                asyncio.create_task(_photos_geo_backfill())
    hls_session.ensure_reaper_running()
    if Var.KEEP_ALIVE:
        print("------------------ Starting Keep Alive Service ------------------")
        print()
        asyncio.create_task(utils.ping_server())
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
    # StreamBot.start() may have failed before the client ever connected
    # (e.g. auth FloodWait at boot). pyrogram's stop()/terminate() then
    # raises ConnectionError, which used to mask the real startup error.
    try:
        await StreamBot.stop()
    except ConnectionError:
        logging.info("StreamBot never connected; nothing to stop")


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
