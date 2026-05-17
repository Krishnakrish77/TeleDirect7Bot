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
)

multi_clients = {}
work_loads = {}
