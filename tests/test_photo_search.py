"""Photos server-side search & filter query building.

Pure tests over build_timeline_query — no Mongo, no network. The builder is
the single source of the timeline filter dict (search q, kind, camera, date
range, min size, favorites/album/trash composition), so its exact shape is
the contract the facets aggregation and timeline_page both rely on.
"""
import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils.photo_store import build_timeline_query, _parse_iso_date


class BuildTimelineQueryTest(unittest.TestCase):
    def test_base_query_scopes_owner_and_excludes_trash(self):
        self.assertEqual(
            build_timeline_query(7),
            {"owner_user_id": 7, "deleted": False},
        )

    def test_trash_view_inverts_deleted(self):
        self.assertEqual(
            build_timeline_query(7, trash=True)["deleted"], True,
        )

    def test_search_uses_text_index(self):
        q = build_timeline_query(7, q="  portugal  ")
        self.assertEqual(q["$text"], {"$search": "portugal"})

    def test_search_quoted_phrase_preserved(self):
        q = build_timeline_query(7, q='"beach day"')
        self.assertEqual(q["$text"], {"$search": '"beach day"'})

    def test_empty_or_whitespace_search_omits_text_clause(self):
        self.assertNotIn("$text", build_timeline_query(7, q="   "))

    def test_kind_camera_mime_exact_match(self):
        q = build_timeline_query(7, kind="video", camera="Apple iPhone 15", mime="image/heic")
        self.assertEqual(q["kind"], "video")
        self.assertEqual(q["camera"], "Apple iPhone 15")
        self.assertEqual(q["mime"], "image/heic")

    def test_date_range_parses_to_utc(self):
        q = build_timeline_query(7, taken_after="2026-01-01", taken_before="2026-06-30T23:59:59")
        self.assertEqual(q["taken_at"]["$gte"], datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(
            q["taken_at"]["$lte"],
            datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
        )

    def test_garbage_dates_dropped_not_400(self):
        q = build_timeline_query(7, taken_after="not-a-date", taken_before="")
        self.assertNotIn("taken_at", q)

    def test_naive_date_treated_as_utc(self):
        parsed = _parse_iso_date("2026-03-01")
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_min_size_floor(self):
        self.assertEqual(build_timeline_query(7, min_size=1024)["size"], {"$gte": 1024})
        # zero/negative means "no filter", not "$gte: 0"
        self.assertNotIn("size", build_timeline_query(7, min_size=0))
        self.assertNotIn("size", build_timeline_query(7, min_size=-5))

    def test_all_filters_compose(self):
        q = build_timeline_query(
            7, favorites=True, album_id="abc", q="sunset", kind="photo",
            taken_after="2026-01-01", min_size=500,
        )
        self.assertEqual(q["owner_user_id"], 7)
        self.assertEqual(q["deleted"], False)
        self.assertEqual(q["favorite"], True)
        self.assertEqual(q["album_ids"], "abc")
        self.assertEqual(q["kind"], "photo")
        self.assertEqual(q["size"], {"$gte": 500})
        self.assertIn("$text", q)
        self.assertIn("taken_at", q)


if __name__ == "__main__":
    unittest.main()
