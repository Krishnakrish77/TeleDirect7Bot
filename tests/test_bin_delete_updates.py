import os
import unittest
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from pyrogram import raw, utils as pyrogram_utils
from main.bot.plugins import stream


class BinDeleteUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_bin_delete_update_is_forwarded_to_catalogue(self):
        raw_channel_id = 12345
        update = raw.types.UpdateDeleteChannelMessages(
            channel_id=raw_channel_id, messages=[7, 8], pts=1, pts_count=2,
        )
        client = object()
        with (
            patch.object(stream.Var, "BIN_CHANNEL", pyrogram_utils.get_channel_id(raw_channel_id)),
            patch.object(stream.media_index, "record_bin_deletions", new=AsyncMock()) as record,
        ):
            await stream.bin_message_deleted(client, update, {}, {})

        record.assert_awaited_once_with([7, 8], bot=client)

    async def test_raw_delete_for_another_channel_is_ignored(self):
        update = raw.types.UpdateDeleteChannelMessages(
            channel_id=99999, messages=[7], pts=1, pts_count=1,
        )
        with patch.object(stream.media_index, "record_bin_deletions", new=AsyncMock()) as record:
            await stream.bin_message_deleted(object(), update, {}, {})

        record.assert_not_awaited()
