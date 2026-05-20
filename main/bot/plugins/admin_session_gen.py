"""
/gensession — interactive session string generator for the bot owner.

Uses asyncio.Queue (running-loop-safe) to pipe user replies into the
single _run() coroutine that owns the entire auth flow.
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

# user_id → Queue[str] receiving the next reply
_queues: Dict[int, asyncio.Queue] = {}

_owner = filters.private & filters.user(Var.OWNER_ID)


async def _run(bot: Client, chat_id: int):
    """Full session-generation flow in one coroutine."""

    async def ask(prompt: str) -> str:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        _queues[chat_id] = q
        await bot.send_message(chat_id, prompt)
        try:
            return await asyncio.wait_for(q.get(), timeout=300)
        except asyncio.TimeoutError:
            raise TimeoutError("No reply in 5 minutes — run /gensession to restart.")
        finally:
            _queues.pop(chat_id, None)

    # Prompt for separate API credentials — reusing the bot's api_id from the
    # same server IP causes Telegram to flag the login as suspicious.
    try:
        api_id_str = (await ask(
            "Send your **API ID** (integer).\n"
            "Get one at my.telegram.org → App configuration.\n\n"
            "/cancel to abort."
        )).strip()
        api_id = int(api_id_str)
    except ValueError:
        await bot.send_message(chat_id, "Invalid API ID — must be a number. Run /gensession to retry.")
        return

    api_hash = (await ask("Now send your **API HASH**:")).strip()

    client = Client(
        name="gen_session",
        api_id=api_id,
        api_hash=api_hash,
        in_memory=True,
    )
    try:
        await client.connect()

        phone = (await ask(
            "Now send your phone number in international format.\n"
            "Example: `+919876543210`"
        )).strip()

        try:
            sent = await client.send_code(phone)
        except FloodWait as e:
            await bot.send_message(chat_id, f"FloodWait — retry after {e.x}s.")
            return
        except Exception as e:
            await bot.send_message(chat_id, f"send\\_code failed: `{type(e).__name__}: {e}`")
            return

        code_raw = await ask(
            "OTP sent!\n"
            "Enter the digits only — no spaces or dashes."
        )
        code = code_raw.strip().replace(" ", "").replace("-", "")

        try:
            await client.sign_in(phone, sent.phone_code_hash, code)
        except PhoneCodeInvalid:
            await bot.send_message(chat_id, "Wrong code — run /gensession to try again.")
            return
        except PhoneCodeExpired:
            await bot.send_message(chat_id, "Code expired — run /gensession to request a new one.")
            return
        except SessionPasswordNeeded:
            password = await ask("2FA enabled — enter your cloud password:")
            try:
                await client.check_password(password)
            except Exception as e:
                await bot.send_message(chat_id, f"Password failed: `{type(e).__name__}: {e}`")
                return
        except Exception as e:
            await bot.send_message(chat_id, f"sign\\_in failed: `{type(e).__name__}: {e}`")
            return

        session = await client.export_session_string()
        await bot.send_message(
            chat_id,
            "✅ Done! Set this as `USER_SESSION` in your environment, "
            "then **delete this message**.\n\n"
            f"`{session}`",
        )

    except TimeoutError as e:
        await bot.send_message(chat_id, str(e))
    except Exception as e:
        logger.exception("gensession _run failed")
        await bot.send_message(chat_id, f"Unexpected error: `{type(e).__name__}: {e}`")
    finally:
        _queues.pop(chat_id, None)
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            pass


# ── /gensession ───────────────────────────────────────────────────────────────

@StreamBot.on_message(_owner & filters.command("gensession"), group=1)
async def gensession_start(bot: Client, m: Message):
    # Cancel any in-progress session for this user
    _queues.pop(m.from_user.id, None)
    asyncio.ensure_future(_run(bot, m.from_user.id))


# ── text replies (phone / OTP / password) ─────────────────────────────────────

async def _has_queue(_, __, m: Message) -> bool:
    return m.from_user is not None and m.from_user.id in _queues

_waiting_filter = filters.create(_has_queue)


@StreamBot.on_message(_owner & filters.text & _waiting_filter, group=0)
async def gensession_reply(bot: Client, m: Message):
    q = _queues.get(m.from_user.id)
    if q and q.empty():
        await q.put(m.text)
