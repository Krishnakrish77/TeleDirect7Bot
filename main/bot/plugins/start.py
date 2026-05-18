import logging
from urllib.parse import urljoin

from main.bot import StreamBot
from main.utils import admin_auth
from main.vars import Var
from pyrogram import filters
from main.utils.Translation import Language, BUTTON


# Sentinel used by vars.py when OWNER_ID isn't set in the env. It's a
# real Telegram id (the "Telegram" system account) so no real user ever
# matches it accidentally.
_OWNER_UNSET = 777000

@StreamBot.on_message(~filters.user(Var.BANNED_USERS) & filters.command('start'))
async def start(b, m):
    lang = getattr(Language, "en")
    await m.reply_text(
        text=lang.START_TEXT.format(m.from_user.mention),
        disable_web_page_preview=True,
        reply_markup=BUTTON.START_BUTTONS
        )


@StreamBot.on_message(~filters.user(Var.BANNED_USERS) & filters.command(["about"]))
async def about(bot, update):
    lang = getattr(Language, "en")
    await update.reply_text(
        text=lang.ABOUT_TEXT,
        disable_web_page_preview=True,
        reply_markup=BUTTON.ABOUT_BUTTONS
    )


@StreamBot.on_message(filters.command("admin") & filters.private)
async def admin_link(bot, message):
    """DM-only: owner sends /admin, gets a one-time admin URL.

    Non-owners get nothing in production so the admin surface stays
    hidden. But if OWNER_ID is unset (defaulting to Telegram's 777000
    system sentinel) we treat the first invocation as setup help and DM
    the requester instructions plus their own Telegram id — otherwise
    the failure mode is silent and undebuggable.
    """
    user = message.from_user
    if user is None:
        return

    owner_id = int(Var.OWNER_ID)
    requester_id = int(user.id)
    logging.info(
        "/admin invoked: from_user=%s owner_id=%s match=%s",
        requester_id, owner_id, requester_id == owner_id,
    )

    if owner_id == _OWNER_UNSET:
        await message.reply_text(
            text=(
                "⚠️ **Admin not configured**\n\n"
                f"Set the `OWNER_ID` environment variable to `{requester_id}` "
                "and redeploy — that's your Telegram user id. Once set, "
                "`/admin` will return a one-time login link."
            ),
            disable_web_page_preview=True,
            quote=True,
        )
        return

    if requester_id != owner_id:
        # Silently ignore so the admin surface isn't advertised to randos.
        return

    token = admin_auth.issue_one_time_token(requester_id)
    url = urljoin(Var.URL, f"admin/login?t={token}")
    await message.reply_text(
        text=(
            "🔐 **Admin access**\n\n"
            f"One-time link (valid 15 min):\n{url}\n\n"
            "Visiting it sets a session cookie good for the next hour."
        ),
        disable_web_page_preview=True,
        quote=True,
    )


@StreamBot.on_message(filters.command(["myid", "id"]) & filters.private)
async def my_id(bot, message):
    """DM-only helper: returns the sender's Telegram user id.

    Useful for OWNER_ID setup — the user can find their numeric id without
    needing a third-party bot like @userinfobot.
    """
    if message.from_user is None:
        return
    await message.reply_text(
        text=f"Your Telegram user id: `{message.from_user.id}`",
        quote=True,
    )


@StreamBot.on_message((filters.command('help')) & ~filters.user(Var.BANNED_USERS))
async def help_handler(bot, message):
    lang = getattr(Language, "en")
    await message.reply_text(
        text=lang.HELP_TEXT.format(Var.UPDATES_CHANNEL),
        disable_web_page_preview=True,
        reply_markup=BUTTON.HELP_BUTTONS
        )
