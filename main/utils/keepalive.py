import asyncio
import logging
import aiohttp
from main import Var


async def ping_server():
    sleep_time = Var.PING_INTERVAL
    while True:
        await asyncio.sleep(sleep_time)
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(Var.URL) as resp:
                    logging.info("Pinged server with response: %s", resp.status)
        except asyncio.TimeoutError:
            logging.warning("Couldn't connect to the site URL..!")
        except Exception:
            logging.exception("Keepalive ping failed")


async def warm_hub_shelves():
    """Periodically pre-warm the hub's optional-shelf caches (top plays,
    trending). Both shelves live behind 1-2.5 s request budgets; a cold
    Mongo aggregation on a slow link blew those budgets on nearly every
    hub load, so the shelves silently vanished. Warm them on a loop well
    outside any request so hub requests hit warm caches."""
    from main.utils import trending, wh_store
    while True:
        try:
            await wh_store.get_top_plays(40)
        except Exception:
            logging.exception("hub warmer: top plays refresh failed")
        try:
            await trending.get_trending()
        except Exception:
            logging.exception("hub warmer: trending refresh failed")
        await asyncio.sleep(900)
