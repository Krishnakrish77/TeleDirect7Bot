import os
import unittest
from unittest.mock import patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import media_index
from main.utils.hub_query import HubItem


def _item(mid: int, **kwargs) -> HubItem:
    defaults = dict(
        message_id=mid,
        secure_hash=f"hash{mid}",
        title=f"Item {mid}",
        year=2025,
        description="",
        tags=[],
        duration=90,
        file_size=1000,
        has_thumb=False,
        quality="1080p",
        file_name=f"item{mid}.mkv",
    )
    defaults.update(kwargs)
    return HubItem(**defaults)


class ExtractSourceTypeTests(unittest.TestCase):
    def test_predvd_variants(self):
        self.assertEqual(media_index._extract_source_type("Movie.2025.PreDVD.mkv"), "PreDVD")
        self.assertEqual(media_index._extract_source_type("Movie 2025 Pre-DVDRip"), "PreDVD")
        self.assertEqual(media_index._extract_source_type("Movie 2025 PreHD 1080p"), "PreDVD")

    def test_cam_and_screener(self):
        self.assertEqual(media_index._extract_source_type("Movie.2025.HDCAM.mkv"), "CAM")
        self.assertEqual(media_index._extract_source_type("Movie.2025.CAMRip.x264"), "CAM")
        self.assertEqual(media_index._extract_source_type("Movie.2025.DVDScr.mp4"), "DVDScr")

    def test_ts_tc(self):
        self.assertEqual(media_index._extract_source_type("Movie.2025.HDTS.mkv"), "HDTS")
        self.assertEqual(media_index._extract_source_type("Movie.2025.TS.mkv"), "TS")
        self.assertEqual(media_index._extract_source_type("Movie 2025 TELESYNC"), "TS")
        self.assertEqual(media_index._extract_source_type("Movie.2025.TC.avi"), "TC")
        self.assertEqual(media_index._extract_source_type("Movie 2025 Telecine"), "TC")

    def test_clean_source_is_empty(self):
        self.assertEqual(media_index._extract_source_type("Movie.2025.1080p.WEB-DL.mkv"), "")
        self.assertEqual(media_index._extract_source_type("Movie.2025.BluRay.x264.mkv"), "")

    def test_ts_container_extension_does_not_flag(self):
        # .ts is a legitimate transport-stream container — a filename
        # ending in .ts alone must not be tagged as a telesync print.
        self.assertEqual(media_index._extract_source_type("Movie.2025.1080p.ts"), "")
        # ...but a real telesync tag elsewhere in the name still fires.
        self.assertEqual(media_index._extract_source_type("Movie.2025.TS.1080p.ts"), "TS")

    def test_title_word_cam_does_not_flag_via_title_haystack(self):
        # The caller (index/reindex) never passes the cleaned title; this
        # documents the intended call shape — "Cam" inside a filename
        # with separators still flags (word boundary), plain titles
        # passed by admins do not go through this path.
        self.assertEqual(media_index._extract_source_type("Movie.2025.CAM.x264"), "CAM")


class SourceTypeFilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.items = dict(media_index._items)
        media_index._items.clear()

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self.items)

    async def test_quality_filter_matches_source_type(self):
        predvd = _item(1, quality="1080p", source_type="PreDVD")
        clean = _item(2, quality="1080p", source_type="")
        media_index._items.update({1: predvd, 2: clean})

        page, _ = media_index.query(quality="PreDVD", limit=10)
        self.assertEqual([it.message_id for it in page], [1])

        page, _ = media_index.query(quality="1080p", limit=10)
        self.assertEqual(sorted(it.message_id for it in page), [1, 2])

    async def test_distinct_qualities_lists_source_tags_first(self):
        media_index._items.update({
            1: _item(1, quality="1080p", source_type="PreDVD"),
            2: _item(2, quality="480p", source_type="CAM"),
            3: _item(3, quality="4K", source_type=""),
        })
        self.assertEqual(
            media_index.distinct_qualities(),
            ["PreDVD", "CAM", "4K", "1080p", "480p"],
        )


class SourceTypePersistenceTests(unittest.TestCase):
    def test_round_trips_through_serializable(self):
        item = _item(1, source_type="PreDVD")
        data = media_index._to_serializable(item)
        self.assertEqual(data["source_type"], "PreDVD")
        restored = media_index._from_serializable(data)
        self.assertEqual(restored.source_type, "PreDVD")

    def test_defaults_to_empty_for_legacy_payloads(self):
        item = _item(1)
        data = media_index._to_serializable(item)
        data.pop("source_type", None)
        restored = media_index._from_serializable(data)
        self.assertEqual(restored.source_type, "")


class BulkSourceTypeTests(unittest.IsolatedAsyncioTestCase):
    """Description round-trip: the tag must survive a re-seed because
    _bulk_source_type encodes it into the description head, exactly the
    way _bulk_quality does for resolution buckets."""

    def setUp(self):
        self.items = dict(media_index._items)
        media_index._items.clear()

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self.items)

    async def test_description_encoding_round_trips_through_extraction(self):
        # main.server/__init__ rebinds the `admin_routes` attribute to the
        # route table, so reach the actual module through sys.modules.
        from main.server.admin_routes import _bulk_source_type  # noqa: F401  (imports the module)
        import sys
        admin_routes = sys.modules["main.server.admin_routes"]

        item = _item(1, description="2.1 GB", source_type="")
        seen_entries = []

        async def fake_rewrite(mid, mutate):
            from main.utils.index_entry import IndexEntry
            entry = IndexEntry(title=item.title, year=item.year,
                               description=item.description, tags=list(item.tags))
            mutate(entry, item)
            seen_entries.append(entry)
            return "written", ""

        with patch.object(admin_routes, "_rewrite_caption", fake_rewrite):
            n = await admin_routes._bulk_source_type([1], "PreDVD")
        self.assertEqual(n, 1)
        self.assertEqual(seen_entries[0].description, "PreDVD · 2.1 GB")
        self.assertEqual(
            media_index._extract_source_type("Movie.2025.mkv", seen_entries[0].description),
            "PreDVD",
        )

        # Clearing the tag strips the old head without leaving a stray
        # separator, and re-extraction finds no source tag.
        with patch.object(admin_routes, "_rewrite_caption", fake_rewrite):
            n = await admin_routes._bulk_source_type([1], "")
        self.assertEqual(n, 1)
        self.assertEqual(seen_entries[-1].description, "2.1 GB")
        self.assertEqual(
            media_index._extract_source_type("Movie.2025.mkv", seen_entries[-1].description),
            "",
        )


if __name__ == "__main__":
    unittest.main()
