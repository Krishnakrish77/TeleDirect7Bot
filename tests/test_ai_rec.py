import os
import unittest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import ai_rec


def _card(href, watch_key="", title="T", eyebrow="Movie"):
    return {"href": href, "watchKey": watch_key, "title": title, "eyebrow": eyebrow, "genres": ["Action"]}


class AiRecGroundingTest(unittest.TestCase):
    def test_dedup_and_grounding(self):
        payloads = [_card("/a", "k1"), _card("/a", "k1"), _card("/b", "k2"), _card("/c", "seen")]
        deduped = ai_rec._dedup_payloads(payloads, {"seen"})
        self.assertEqual([p["href"] for p in deduped], ["/a", "/b"])

        index, prompt_items = ai_rec._index_candidates(deduped)
        self.assertEqual([pi["id"] for pi in prompt_items], ["c0", "c1"])

        picks = [
            {"id": "c1", "reason": "you like B", "bucket": "discovery"},
            {"id": "c999", "reason": "hallucinated", "bucket": "comfort"},  # dropped
            {"id": "c0", "reason": "you like A", "bucket": "comfort"},
        ]
        items = ai_rec._apply_picks(picks, index, limit=10)
        self.assertEqual([i["href"] for i in items], ["/b", "/a"])  # hallucinated id gone
        self.assertEqual(items[0]["bucket"], "discovery")
        self.assertEqual(len(ai_rec._apply_picks(picks, index, limit=1)), 1)

        # bad bucket normalises to comfort; non-dict pick is skipped
        self.assertEqual(ai_rec._apply_picks([{"id": "c0", "reason": "x", "bucket": "weird"}], index, 10)[0]["bucket"], "comfort")
        mixed = ai_rec._apply_picks(["oops", {"id": "c0", "reason": "ok", "bucket": "comfort"}], index, 10)
        self.assertEqual([i["href"] for i in mixed], ["/a"])

    def test_validate_cached_drops_removed_items(self):
        valid = ai_rec._validate_cached([
            {"itemId": "999999", "href": "/gone"},   # not in empty _items -> dropped
            {"itemId": "movie:x", "href": "/kept"},  # grouped card -> kept
            {"itemId": "", "href": "/nokeyt"},        # no id -> kept
        ])
        self.assertEqual([i["href"] for i in valid], ["/kept", "/nokeyt"])

    def test_cold_start_detection(self):
        self.assertTrue(ai_rec._is_cold({}, {}, []))
        self.assertFalse(ai_rec._is_cold({"seeds": [(1, "movie")]}, {}, [1, 2, 3, 4, 5, 6]))

    def test_query_terms_and_tmdb_exclusions(self):
        self.assertEqual(
            ai_rec._query_terms("Show me something funny with heists like Inception"),
            ["funny", "heists", "inception"],
        )
        payloads = [
            {"href": "/hidden", "tmdbId": 10, "tmdbKind": "movie"},
            {"href": "/kept", "tmdbId": 11, "tmdbKind": "movie"},
            {"href": "/music"},
        ]
        filtered = ai_rec._exclude_tmdb_payloads(payloads, {(10, "movie")})
        self.assertEqual([payload["href"] for payload in filtered], ["/kept", "/music"])

    def test_spa_routes_submodule_not_shadowed(self):
        # ai_rec lazily does `from main.server import spa_routes` and needs the
        # module's _card, not the RouteTableDef the __init__ aliases export.
        import main.server  # noqa: F401  (runs package __init__)
        from main.server import spa_routes as sp
        self.assertTrue(hasattr(sp, "_card"))


if __name__ == "__main__":
    unittest.main()
