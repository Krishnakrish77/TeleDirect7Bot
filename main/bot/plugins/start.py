from urllib.parse import urljoin

from main.bot import StreamBot
from main.utils import admin_auth
from main.vars import Var
from pyrogram import filters
from main.utils.Translation import Language, BUTTON

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

    Anyone else gets nothing — we don't even reply to non-owner pings to
    avoid signalling that an admin surface exists.
    """
    if message.from_user is None or int(message.from_user.id) != int(Var.OWNER_ID):
        return
    token = admin_auth.issue_one_time_token(message.from_user.id)
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


@StreamBot.on_message((filters.command('help')) & ~filters.user(Var.BANNED_USERS))
async def help_handler(bot, message):
    lang = getattr(Language, "en")
    await message.reply_text(
        text=lang.HELP_TEXT.format(Var.UPDATES_CHANNEL),
        disable_web_page_preview=True,
        reply_markup=BUTTON.HELP_BUTTONS
        )
