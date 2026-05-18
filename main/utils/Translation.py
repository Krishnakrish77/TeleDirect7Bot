from main.vars import Var
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


class Language(object):
    class en(object):
        START_TEXT = """
**👋 Hᴇʏ, {}**\n
<i>I'm a Telegram file streamer + browsable media library.</i>\n
<i>Send me any video to get an instant streaming link, or open the library to browse everything you've added so far.</i>\n
<i>Tap Help for full instructions.</i>"""

        HELP_TEXT = """🔰 **How to Use Me ?**

<b>Streaming &amp; downloads</b>
<i>- Forward me any file or media — I'll reply with a streaming link and a direct download URL.</i>

<b>Browsable library</b>
<i>- Everything you upload is auto-indexed and appears on the library hub with search, filters, year/quality facets, and tag chips.</i>
<i>- Episodes (S01E03, 1x03, "Season 1 Episode 3") collapse into one series card; click through to see all episodes by season.</i>

<b>Subtitles</b>
<i>- Embedded tracks inside MKV/MP4 are detected automatically.</i>
<i>- For external .srt / .vtt sidecars: either reply to your video with the subtitle file, or upload it with a matching filename stem (e.g. <code>Movie.2024.1080p.en.srt</code> pairs with <code>Movie.2024.1080p.mkv</code>). Language is read from the trailing <code>.{{lang}}</code> suffix.</i>

**Download Link With Fastest Speed ⚡️**"""

        ABOUT_TEXT = """
<b>⚜ My Name : TeleDirectBot</b>\n
<b>⚜ Username : @TeleDirect7Bot</b>\n
<b>🔸Version : 1.0</b>\n
<b>🔹Last Updated : [ 14-Jan-25 ]</b>
"""

        stream_msg_text ="""
<u>**Successfully Generated Your Link !**</u>\n
<b>📂 File Name :</b> {}\n
<b>📦 File Size :</b> {}\n
<b>📥 Download :</b> {}\n
<b>🖥 Watch :</b> {}"""

        ban_text="__Sᴏʀʀʏ Sɪʀ, Yᴏᴜ ᴀʀᴇ Bᴀɴɴᴇᴅ ᴛᴏ ᴜsᴇ ᴍᴇ.__\n\n**[Cᴏɴᴛᴀᴄᴛ Dᴇᴠᴇʟᴏᴘᴇʀ](https://t.me/TechZBots_Support) Tʜᴇʏ Wɪʟʟ Hᴇʟᴘ Yᴏᴜ**"

# ------------------------------------------------------------------------------

class BUTTON(object):
    START_BUTTONS = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📚 Browse Library", url=Var.URL),
            ],
            [
                InlineKeyboardButton("Help", callback_data="help"),
                InlineKeyboardButton("About", callback_data="about"),
            ],
            [
                InlineKeyboardButton("Media Search Bot", url="https://t.me/Movier7Bot"),
                InlineKeyboardButton("Repo", url="https://github.com/Krishnakrish77/TeleDirect7Bot"),
            ],
        ]
    )
    HELP_BUTTONS = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📚 Browse Library", url=Var.URL),
            ],
            [
                InlineKeyboardButton("Home", callback_data="home"),
                InlineKeyboardButton("About", callback_data="about"),
            ],
            [
                InlineKeyboardButton("Close", callback_data="close"),
            ],
        ]
    )
    ABOUT_BUTTONS = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📚 Browse Library", url=Var.URL),
            ],
            [
                InlineKeyboardButton("Home", callback_data="home"),
                InlineKeyboardButton("Help", callback_data="help"),
            ],
            [
                InlineKeyboardButton("Close", callback_data="close"),
            ],
        ]
    )