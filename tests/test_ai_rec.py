import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    def test_validate_cached_never_returns_watched_or_excluded_titles(self):
        valid = ai_rec._validate_cached(
            [
                {"itemId": "movie:watched", "href": "/watched", "watchKey": "done"},
                {"itemId": "series:excluded", "href": "/excluded", "tmdbId": 42, "tmdbKind": "tv"},
                {"itemId": "movie:fresh", "href": "/fresh", "tmdbId": 43, "tmdbKind": "movie"},
            ],
            exclude_keys={"done"},
            excluded_tmdb={(42, "tv")},
        )
        self.assertEqual([item["href"] for item in valid], ["/fresh"])

    def test_activity_is_not_cold_when_history_lacks_enrichment(self):
        # Older watches can legitimately have no TMDB seed yet. The fallback
        # must not tell a frequent viewer that it is still learning them.
        self.assertTrue(ai_rec._has_user_activity({}, {}, [{"cw_key": "old-watch"}], {}))
        self.assertTrue(ai_rec._has_user_activity({}, {}, [], {"in-progress": {}}))
        self.assertFalse(ai_rec._has_user_activity({}, {}, [], {}))

    def test_metadata_thin_history_is_not_reported_as_cold_start(self):
        async def run():
            with patch.object(ai_rec.rec_engine, "_collect_signal_profile", AsyncMock(return_value={})), patch.object(
                ai_rec.wh_store, "get_recent", AsyncMock(return_value=[{"cw_key": "legacy-watch"}])
            ), patch.object(ai_rec.cw_store, "get_all", AsyncMock(return_value={})), patch.object(
                ai_rec.dismissed_store, "get_dismissed_ids", AsyncMock(return_value=set())
            ), patch.object(ai_rec.ai_rec_store, "get_cached", AsyncMock(return_value=None)), patch.object(
                ai_rec, "_safe_stats", AsyncMock(return_value={})
            ), patch.object(ai_rec, "_trending_items", AsyncMock(return_value=[{"href": "/fresh"}])):
                return await ai_rec._generate(7, query=None, limit=12, refresh=False)

        result = __import__("asyncio").run(run())
        self.assertFalse(result["coldStart"])
        self.assertIn("activity is saved", result["message"])

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

    def test_reason_prompt_requires_a_concrete_personal_match(self):
        prompt = ai_rec._build_prompt("Likes crime dramas", [], "", 8)
        self.assertIn("max 9 words", prompt)
        self.assertIn("Do not start with", prompt)
        self.assertIn("from your library", prompt)

    def test_mix_ranking_stays_audio_only_and_grounded(self):
        def track(message_id, artist, *, kind="audio", hidden=False, tags=None):
            return SimpleNamespace(
                message_id=message_id,
                media_kind=kind,
                hidden=hidden,
                title=f"Track {message_id}",
                artist=artist,
                album_title="Album",
                tags=tags or [],
                tmdb_genres=[],
            )

        familiar = track(10, "Anirudh", tags=["night"])
        fresh = track(11, "New Artist", tags=["night"])
        video = track(12, "Anirudh", kind="video")
        hidden = track(13, "Anirudh", hidden=True)
        ranked = ai_rec._rank_mix_candidates(
            [fresh, video, hidden, familiar], [familiar], "night", "familiar"
        )
        self.assertEqual([item.message_id for item in ranked], [10, 11])

    def test_mix_selection_deduplicates_model_picks(self):
        first = SimpleNamespace(message_id=1)
        second = SimpleNamespace(message_id=2)
        selected = ai_rec._mix_items_from_picks(
            [{"id": "m0"}, {"id": "m0"}, {"id": "missing"}, {"id": "m1"}],
            {"m0": first, "m1": second},
            20,
        )
        self.assertEqual(selected, [first, second])

    def test_explicit_query_candidates_survive_candidate_pool_shuffle(self):
        query_matches = [_card(f"/query-{index}") for index in range(30)]
        other_matches = [_card(f"/other-{index}") for index in range(60)]

        selected = ai_rec._select_candidate_payloads(
            query_matches + other_matches,
            {item["href"] for item in query_matches},
        )

        self.assertEqual([item["href"] for item in selected[:24]], [f"/query-{index}" for index in range(24)])
        self.assertEqual(len(selected), ai_rec._MAX_CANDIDATES)

    def test_spa_routes_submodule_not_shadowed(self):
        # ai_rec lazily does `from main.server import spa_routes` and needs the
        # module's _card, not the RouteTableDef the __init__ aliases export.
        import main.server  # noqa: F401  (runs package __init__)
        from main.server import spa_routes as sp
        self.assertTrue(hasattr(sp, "_card"))


if __name__ == "__main__":
    unittest.main()
