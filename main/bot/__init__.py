from ..vars import Var
from pyrogram import Client
from os import getcwd

# Monkey patch for Pyrogram bug
from pyrogram import utils

def get_peer_type_new(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"

utils.get_peer_type = get_peer_type_new

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
