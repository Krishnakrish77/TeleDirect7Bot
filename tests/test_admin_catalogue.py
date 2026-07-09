import os
from types import SimpleNamespace
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server.admin_routes import _admin_duplicate_candidates
from main.utils import media_index
from main.utils.hub_query import HubItem


def _item(
    message_id: int,
    *,
    secure_hash: str = "hash",
    title: str = "Santhosh Subramaniam",
    year: int | None = 2008,
    file_size: int = 1024,
    movie_key: str = "",
    tmdb_id: int | None = None,
    tmdb_kind: str = "movie",
    series_key: str = "",
    season: int | None = None,
    episode: int | None = None,
    quality: str = "720p",
) -> HubItem:
    return HubItem(
        message_id=message_id,
        secure_hash=secure_hash,
        title=title,
        year=year,
        description="",
        tags=[],
        duration=7200,
        file_size=file_size,
        has_thumb=True,
        quality=quality,
        file_name=f"{title}.mkv",
        movie_key=movie_key,
        tmdb_id=tmdb_id,
        tmdb_kind=tmdb_kind,
        series_key=series_key,
        series_title=title if series_key else "",
        season=season,
        episode=episode,
        media_kind="video",
    )


class AdminCatalogueTest(unittest.TestCase):
    def test_duplicate_candidates_flag_same_title_year_movies(self):
        details, groups, extras = _admin_duplicate_candidates([
            _item(101, secure_hash="a", file_size=1000),
            _item(102, secure_hash="b", file_size=2000),
        ])

        self.assertEqual(groups, 1)
        self.assertEqual(extras, 1)
        self.assertEqual(details[101]["reason"], "Same title/year/quality")
        self.assertEqual(details[102]["size"], 2)

    def test_duplicate_candidates_do_not_flag_quality_variants(self):
        details, groups, extras = _admin_duplicate_candidates([
            _item(101, secure_hash="a", file_size=1000, quality="720p", movie_key="santhosh-subramaniam", tmdb_id=123),
            _item(102, secure_hash="b", file_size=2000, quality="1080p", movie_key="santhosh-subramaniam", tmdb_id=123),
        ])

        self.assertEqual(details, {})
        self.assertEqual(groups, 0)
        self.assertEqual(extras, 0)

    def test_duplicate_candidates_prioritise_exact_files(self):
        details, groups, extras = _admin_duplicate_candidates([
            _item(101, secure_hash="same", file_size=1000, quality="720p"),
            _item(102, secure_hash="same", file_size=1000, quality="1080p"),
        ])

        self.assertEqual(groups, 1)
        self.assertEqual(extras, 1)
        self.assertEqual(details[101]["reason"], "Exact file")
        self.assertEqual(details[102]["reason"], "Exact file")

    def test_duplicate_counts_include_overlapping_review_candidates(self):
        details, groups, extras = _admin_duplicate_candidates([
            _item(101, secure_hash="same", file_size=1000),
            _item(102, secure_hash="same", file_size=1000),
            _item(103, secure_hash="other", file_size=2000),
        ])

        self.assertEqual(groups, 2)
        self.assertEqual(extras, 2)
        self.assertEqual(details[101]["reason"], "Exact file")
        self.assertEqual(details[103]["reason"], "Same title/year/quality")

    def test_duplicate_candidates_do_not_flag_different_series_episodes_by_tmdb_id(self):
        details, groups, extras = _admin_duplicate_candidates([
            _item(101, secure_hash="castle1", file_size=1000, title="Castle", tmdb_id=1419, tmdb_kind="tv", series_key="castle", season=1, episode=1),
            _item(102, secure_hash="castle2", file_size=2000, title="Castle", tmdb_id=1419, tmdb_kind="tv", series_key="castle", season=1, episode=2),
        ])

        self.assertEqual(details, {})
        self.assertEqual(groups, 0)
        self.assertEqual(extras, 0)


class FakeBot:
    def __init__(self, messages: dict[int, object], latest_id: int):
        self.messages = messages
        self.latest_id = latest_id
        self.deleted: list[int] = []

    async def send_message(self, channel_id: int, text: str):
        return SimpleNamespace(id=self.latest_id)

    async def delete_messages(self, channel_id: int, message_id: int):
        self.deleted.append(message_id)
        return 1

    async def get_messages(self, channel_id: int, ids):
        if not isinstance(ids, list):
            ids = [ids]
        return [self.messages.get(mid, SimpleNamespace(id=mid, empty=True)) for mid in ids]


class AdminCatalogueAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_prune_non_admin_uploads_removes_catalogue_only(self):
        original_items = dict(media_index._items)
        original_hash_map = dict(media_index._hash_map)
        original_latest = media_index._latest_seen_id
        original_schedule = media_index.schedule_snapshot
        original_persist = media_index._persist_unlocked
        try:
            media_index._items.clear()
            media_index._hash_map.clear()
            media_index._latest_seen_id = 102
            media_index.schedule_snapshot = lambda bot: None
            media_index._persist_unlocked = lambda: None
            admin_item = _item(101, secure_hash="admin")
            non_admin_item = _item(102, secure_hash="user")
            media_index._items[101] = admin_item
            media_index._items[102] = non_admin_item
            media_index._hash_map["admin"] = 101
            media_index._hash_map["user"] = 102
            messages = {
                101: SimpleNamespace(id=101, empty=False),
                102: SimpleNamespace(id=102, empty=False),
                103: SimpleNamespace(
                    id=103,
                    empty=False,
                    reply_to_message=SimpleNamespace(id=102),
                    text="**Requested By :** User\n**User ID :** `42`",
                ),
            }
            bot = FakeBot(messages, latest_id=104)

            removed = await media_index.prune_non_admin_uploads(bot, -1001, batch_size=10)

            self.assertEqual(removed, 1)
            self.assertIn(101, media_index._items)
            self.assertNotIn(102, media_index._items)
            self.assertEqual(bot.deleted, [104])
        finally:
            media_index._items.clear()
            media_index._items.update(original_items)
            media_index._hash_map.clear()
            media_index._hash_map.update(original_hash_map)
            media_index._latest_seen_id = original_latest
            media_index.schedule_snapshot = original_schedule
            media_index._persist_unlocked = original_persist


if __name__ == "__main__":
    unittest.main()
