"""
/gensession — interactive session string generator for the bot owner.

Flow:  /gensession  →  phone number  →  OTP code  →  (2FA password if set)
       →  bot replies with the USER_SESSION string.

All state is kept in _state (in-process memory). Restart clears it, which
is fine — the user just runs /gensession again.
"""
import logging
from typing import Dict, Any

from pyrogram import Client, filters
from pyrogram.errors import (
    PhoneCodeExpired,
    PhoneCodeInvalid,
    SessionPasswordNeeded,
    FloodWait,
)
from pyrogram.types import Message

from main.bot import StreamBot
from main.vars import Var

logger = logging.getLogger(__name__)

# state keyed by user_id
# { user_id: { "step": "phone"|"code"|"password", "client": Client, "phone": str, "phone_code_hash": str } }
_state: Dict[int, Dict[str, Any]] = {}

_owner = filters.private & filters.user(Var.OWNER_ID)


def _make_client() -> Client:
    return Client(
        name=":memory:",
        api_id=Var.API_ID,
        api_hash=Var.API_HASH,
        in_memory=True,
    )


async def _cleanup(user_id: int):
    entry = _state.pop(user_id, None)
    if entry:
        client: Client = entry.get("client")
        if client and client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass


@StreamBot.on_message(_owner & filters.command("gensession"), group=1)
async def gensession_start(bot: Client, m: Message):
    await _cleanup(m.from_user.id)
    _state[m.from_user.id] = {"step": "phone"}
    await m.reply_text(
        "Send your phone number in international format.\n"
        "Example: `+919876543210`\n\n"
        "Send /cancel to abort.",
        quote=True,
    )


@StreamBot.on_message(_owner & filters.command("cancel"), group=1)
async def gensession_cancel(bot: Client, m: Message):
    if m.from_user.id in _state:
        await _cleanup(m.from_user.id)
        await m.reply_text("Session generation cancelled.", quote=True)


@StreamBot.on_message(_owner & filters.text & ~filters.command([""]), group=2)
async def gensession_input(bot: Client, m: Message):
    uid = m.from_user.id
    entry = _state.get(uid)
    if not entry:
        return

    step = entry["step"]

    # ── Phone number ────────────────────────────────────────────────────────
    if step == "phone":
        phone = m.text.strip()
        status = await m.reply_text("Sending OTP…", quote=True)
        client = _make_client()
        try:
            await client.connect()
            sent = await client.send_code(phone)
            entry.update({
                "step": "code",
                "client": client,
                "phone": phone,
                "phone_code_hash": sent.phone_code_hash,
            })
            await status.edit_text(
                "OTP sent! Enter the code you received.\n"
                "Tip: if Telegram sent `1 2345`, enter `12345` (no spaces)."
            )
        except FloodWait as e:
            await _cleanup(uid)
            await status.edit_text(f"FloodWait — try again after {e.x}s.")
        except Exception as e:
            await _cleanup(uid)
            await status.edit_text(f"Failed to send OTP: {e}")

    # ── OTP code ─────────────────────────────────────────────────────────────
    elif step == "code":
        code = m.text.strip().replace(" ", "")
        client: Client = entry["client"]
        status = await m.reply_text("Verifying…", quote=True)
        try:
            await client.sign_in(
                entry["phone"],
                entry["phone_code_hash"],
                code,
            )
            session = await client.export_session_string()
            await _cleanup(uid)
            await status.edit_text(
                "✅ Done! Here is your `USER_SESSION` string:\n\n"
                f"`{session}`\n\n"
                "Add it to your environment variables, then **delete this message**."
            )
        except PhoneCodeInvalid:
            await status.edit_text("Invalid code — try again.")
        except PhoneCodeExpired:
            await _cleanup(uid)
            await status.edit_text("Code expired. Run /gensession to start over.")
        except SessionPasswordNeeded:
            entry["step"] = "password"
            await status.edit_text("2FA is enabled. Enter your cloud password:")
        except Exception as e:
            await _cleanup(uid)
            await status.edit_text(f"Sign-in failed: {e}")

    # ── 2FA password ─────────────────────────────────────────────────────────
    elif step == "password":
        password = m.text.strip()
        client: Client = entry["client"]
        status = await m.reply_text("Checking password…", quote=True)
        try:
            await client.check_password(password)
            session = await client.export_session_string()
            await _cleanup(uid)
            await status.edit_text(
                "✅ Done! Here is your `USER_SESSION` string:\n\n"
                f"`{session}`\n\n"
                "Add it to your environment variables, then **delete this message**."
            )
        except Exception as e:
            await _cleanup(uid)
            await status.edit_text(f"Password check failed: {e}")
