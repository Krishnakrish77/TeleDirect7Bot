from ..vars import Var
from pyrogram import Client


StreamBot = Client(
    name="Tele7DirectBot",
    api_id=Var.API_ID,
    api_hash=Var.API_HASH,
    workdir="main",
    plugins={"root": "main/bot/plugins"},
    bot_token=Var.BOT_TOKEN,
    sleep_threshold=Var.SLEEP_THRESHOLD,
    workers=Var.WORKERS,
    # in_memory avoids a stale .session file causing AuthKeyNotFound on
    # Koyeb rolling deploys where two containers briefly overlap and
    # Telegram invalidates the old auth key. Bots re-authenticate with
    # BOT_TOKEN on every start so no session persistence is needed.
    in_memory=True,
)

multi_clients = {}
work_loads = {}
