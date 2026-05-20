"""
/gensession — interactive session string generator for the bot owner.

The entire auth flow runs as a single background coroutine.
asyncio.Future objects are used to pipe user replies into it, so
the Pyrogram send_code → sign_in sequence stays in one place with
no state-machine split across messages.
"""
import asyncio
import logging
from typing import Dict

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

# user_id → Future[str] awaiting the next text reply
_pending: Dict[int, "asyncio.Future[str]"] = {}

_owner = filters.private & filters.user(Var.OWNER_ID)


async def _run(bot: Client, chat_id: int):
    """Full session generation flow in one coroutine."""
    loop = asyncio.get_event_loop()

    async def ask(prompt: str) -> str:
        """Send prompt, wait for the next user reply."""
        fut: asyncio.Future[str] = loop.create_future()
        _pending[chat_id] = fut
        await bot.send_message(chat_id, prompt)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=300)
        except asyncio.TimeoutError:
            raise TimeoutError("No reply in 5 minutes — session generation cancelled.")
        finally:
            _pending.pop(chat_id, None)

    client = Client(
        name=":memory:",
        api_id=Var.API_ID,
        api_hash=Var.API_HASH,
    )
    try:
        await client.connect()

        phone = (await ask(
            "Send your phone number in international format.\n"
            "Example: `+919876543210`"
        )).strip()

        try:
            sent = await client.send_code(phone)
        except FloodWait as e:
            await bot.send_message(chat_id, f"FloodWait — try again after {e.x}s.")
            return

        code_raw = await ask(
            "OTP sent! Enter the code you received.\n"
            "If it looks like `1 2345`, send it as `12345`."
        )
        code = code_raw.strip().replace(" ", "").replace("-", "")

        try:
            await client.sign_in(phone, sent.phone_code_hash, code)
        except PhoneCodeInvalid:
            await bot.send_message(chat_id, "Invalid code. Run /gensession to try again.")
            return
        except PhoneCodeExpired:
            await bot.send_message(chat_id, "Code expired. Run /gensession to try again.")
            return
        except SessionPasswordNeeded:
            password = await ask("2FA is enabled. Enter your cloud password:")
            try:
                await client.check_password(password)
            except Exception as e:
                await bot.send_message(chat_id, f"Password check failed: {e}")
                return

        session = await client.export_session_string()
        await bot.send_message(
            chat_id,
            "✅ Done! Set this as `USER_SESSION` in your environment variables, "
            "then **delete this message**.\n\n"
            f"`{session}`",
        )

    except TimeoutError as e:
        await bot.send_message(chat_id, str(e))
    except Exception as e:
        logger.exception("gensession flow failed")
        await bot.send_message(chat_id, f"Error: {e}")
    finally:
        _pending.pop(chat_id, None)
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            pass


@StreamBot.on_message(_owner & filters.command("gensession"), group=1)
async def gensession_start(bot: Client, m: Message):
    # Cancel any in-progress flow for this user
    fut = _pending.pop(m.from_user.id, None)
    if fut and not fut.done():
        fut.cancel()
    asyncio.ensure_future(_run(bot, m.from_user.id))


@StreamBot.on_message(_owner & filters.text & ~filters.command([""]), group=3)
async def gensession_reply(bot: Client, m: Message):
    fut = _pending.get(m.from_user.id)
    if fut and not fut.done():
        fut.set_result(m.text)
