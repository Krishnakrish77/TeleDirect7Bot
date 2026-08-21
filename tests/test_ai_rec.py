import os
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import ai_rec, rec_engine


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

    def test_dedup_drops_a_watched_group_even_when_card_uses_another_upload(self):
        payloads = [
            {"href": "/series/a", "itemId": "series:a", "watchKey": "latest-upload"},
            {"href": "/movie/b", "itemId": "movie:b", "watchKey": "unwatched"},
        ]
        result = ai_rec._dedup_payloads(payloads, {"older-watched-upload"}, {"series:a"})
        self.assertEqual([item["href"] for item in result], ["/movie/b"])

    def test_validate_cached_drops_removed_items(self):
        valid = ai_rec._validate_cached([
            {"itemId": "999999", "href": "/gone"},   # not in empty _items -> dropped
            {"itemId": "movie:x", "href": "/gone-group"},  # deleted group -> dropped
            {"itemId": "", "href": "/nokeyt"},        # no id -> kept
        ])
        self.assertEqual([i["href"] for i in valid], ["/nokeyt"])

    def test_validate_cached_never_returns_watched_or_excluded_titles(self):
        valid = ai_rec._validate_cached(
            [
                {"itemId": "movie:watched", "href": "/watched", "watchKey": "done"},
                {"itemId": "series:excluded", "href": "/excluded", "tmdbId": 42, "tmdbKind": "tv"},
                {"itemId": "", "href": "/fresh", "tmdbId": 43, "tmdbKind": "movie"},
            ],
            exclude_keys={"done"},
            excluded_tmdb={(42, "tv")},
        )
        self.assertEqual([item["href"] for item in valid], ["/fresh"])

    def test_watched_card_ids_use_movie_and_series_groups(self):
        def item(*, message_id, movie_key="", series_key=""):
            return SimpleNamespace(message_id=message_id, movie_key=movie_key, series_key=series_key)

        by_key = {
            "movie-upload": item(message_id=1, movie_key="kalki"),
            "series-upload": item(message_id=2, series_key="dark"),
            "track-upload": item(message_id=3),
        }
        with patch.object(ai_rec.rec_engine, "_item_for_cw_key", side_effect=by_key.get):
            result = ai_rec._watched_card_ids(set(by_key))
        self.assertEqual(result, {"movie:kalki", "series:dark", "3"})

    def test_ai_picks_uses_the_full_retained_watch_history_for_exclusion(self):
        self.assertEqual(ai_rec._AI_REC_HISTORY_LIMIT, 200)

    def test_ai_picks_tool_call_limit_defaults_to_five_and_is_capped_at_ten(self):
        self.assertEqual(ai_rec._bounded_env_int("MISSING_AI_REC_TEST_LIMIT", 5, 1, 10), 5)
        with patch.dict(os.environ, {"MISSING_AI_REC_TEST_LIMIT": "99"}):
            self.assertEqual(ai_rec._bounded_env_int("MISSING_AI_REC_TEST_LIMIT", 5, 1, 10), 10)
        with patch.dict(os.environ, {"MISSING_AI_REC_TEST_LIMIT": "bad"}):
            self.assertEqual(ai_rec._bounded_env_int("MISSING_AI_REC_TEST_LIMIT", 5, 1, 10), 5)

    def test_recommendation_metadata_is_safe_and_distinguishes_fallbacks(self):
        with patch.object(ai_rec.time, "time", return_value=1_700_000_000):
            agent = ai_rec._recommendation_meta("agent")
            fallback = ai_rec._recommendation_meta("unknown", cached=True, fallback=True, generated_at=123)
        self.assertEqual(agent, {"origin": "agent", "cached": False, "fallback": False, "generatedAt": 1_700_000_000})
        self.assertEqual(fallback, {"origin": "library", "cached": True, "fallback": True, "generatedAt": 123})

    def test_initial_open_ignores_a_cached_library_fallback(self):
        async def run():
            with patch.object(
                ai_rec.ai_rec_store,
                "get_cached_entry",
                AsyncMock(return_value={"items": [{"href": "/weaker"}], "origin": "library"}),
            ):
                return await ai_rec._cached_ai_recommendations(
                    7, profile={}, history=[], cw_map={}, dismissed=set(),
                )

        self.assertIsNone(__import__("asyncio").run(run()))

    def test_agent_fallback_does_not_write_an_initial_pick_cache(self):
        async def run():
            with (
                patch.object(ai_rec.rec_engine, "_collect_signal_profile", AsyncMock(return_value={})),
                patch.object(ai_rec.wh_store, "get_recent", AsyncMock(return_value=[])),
                patch.object(ai_rec.cw_store, "get_all", AsyncMock(return_value={})),
                patch.object(ai_rec.dismissed_store, "get_dismissed_ids", AsyncMock(return_value=set())),
                patch.object(ai_rec.ai_rec_store, "get_cached_entry", AsyncMock(return_value=None)),
                patch.object(ai_rec.ai_rec_store, "set_cached", AsyncMock()) as set_cached,
                patch.object(ai_rec, "_safe_stats", AsyncMock(return_value={})),
                patch.object(ai_rec, "_trending_items", AsyncMock(return_value=[{"href": "/fresh"}])),
            ):
                result = await ai_rec._generate(
                    7, query=None, limit=12, refresh=False, rank_with_gemini=False, cache_result=False,
                )
            set_cached.assert_not_awaited()
            return result

        self.assertEqual(__import__("asyncio").run(run())["items"], [{"href": "/fresh"}])

    def test_external_pick_cache_uses_a_monotonic_clock(self):
        async def run():
            ai_rec._external_pick_cache.clear()
            with patch.object(ai_rec.tmdb, "is_configured", return_value=True), patch.object(
                ai_rec.request_store, "is_available", return_value=True
            ), patch.object(ai_rec.request_store, "requested_keys", AsyncMock(return_value=set())), patch.object(
                ai_rec.rec_engine, "_fetch_recs_for_seeds", AsyncMock(return_value=[])
            ):
                result = await ai_rec._requestable_picks(7, {"seeds": []}, set(), "")
            self.assertEqual(result, [])
            self.assertIn(7, ai_rec._external_pick_cache)

        __import__("asyncio").run(run())

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

    def test_ai_picks_exclude_music_from_candidates_and_cache(self):
        payloads = [
            {"href": "/movie", "eyebrow": "Movie", "itemId": ""},
            {"href": "/track", "eyebrow": "Music", "aspect": "square", "itemId": ""},
        ]
        self.assertEqual([item["href"] for item in ai_rec._video_payloads(payloads)], ["/movie"])
        self.assertEqual([item["href"] for item in ai_rec._validate_cached(payloads)], ["/movie"])
        self.assertEqual(ai_rec._clean_agent_args("search_library", {"kind": "music"})["kind"], "")

    def test_ai_picks_fallback_keeps_comfort_and_discovery_when_possible(self):
        items = ai_rec._fallback_items([_card("/one"), _card("/two"), _card("/three")], 12)
        self.assertEqual([item["bucket"] for item in items], ["comfort", "comfort", "discovery"])
        self.assertEqual(ai_rec._fallback_items([_card("/one")], 12)[0]["bucket"], "comfort")
        mixed = ai_rec._balanced_buckets([
            {"href": "/one", "bucket": "comfort"}, {"href": "/two", "bucket": "discovery"},
        ])
        self.assertEqual([item["bucket"] for item in mixed], ["comfort", "discovery"])

    def test_agent_tool_arguments_are_constrained(self):
        self.assertEqual(
            ai_rec._clean_agent_args("search_library", {
                "query": "  Neon   crime  ", "kind": "movies", "year": "2020", "sort": "bad",
            }),
            {"kind": "movies", "genre": "", "sort": "relevance", "query": "Neon crime", "year": 2020},
        )
        self.assertEqual(
            ai_rec._clean_agent_args("get_title_details", {"ids": ["card_1"] * 20})["ids"],
            ["card_1"] * ai_rec._AGENT_TOOL_RESULT_LIMIT,
        )

    def test_agent_loop_only_applies_ids_returned_by_tools(self):
        class Catalogue:
            def __init__(self, **_kwargs):
                self.payloads = {"card_1": _card("/one"), "card_2": _card("/two")}

            def run(self, name, _args):
                self.last_name = name
                return [{"id": "card_1", "title": "One", "availability": {"playable": True}}]

            def _compact(self, identifier, payload):
                return {"id": identifier, "title": payload["title"], "availability": {"playable": True}}

        async def run():
            function_response = {"candidates": [{"content": {"parts": [{"functionCall": {
                "name": "search_library", "args": {"query": "one"},
            }}]}}]}
            no_call_response = {"candidates": [{"content": {"parts": [{"text": "done"}]}}]}
            statuses = []
            with patch.object(ai_rec.rec_engine, "_collect_signal_profile", AsyncMock(return_value={"seeds": []})), patch.object(
                ai_rec.wh_store, "get_recent", AsyncMock(return_value=[])
            ), patch.object(ai_rec.cw_store, "get_all", AsyncMock(return_value={})), patch.object(
                ai_rec.dismissed_store, "get_dismissed_ids", AsyncMock(return_value=set())
            ), patch.object(ai_rec, "_safe_stats", AsyncMock(return_value={})), patch.object(
                ai_rec, "_AgentCatalogue", Catalogue
            ), patch.object(
                ai_rec.gemini, "generate_content", AsyncMock(side_effect=[function_response, no_call_response])
            ) as generate_content, patch.object(
                ai_rec.gemini, "generate_json", AsyncMock(return_value={"picks": [{"id": "card_2", "reason": "grounded", "bucket": "comfort"}, {"id": "invented", "reason": "no", "bucket": "comfort"}]})
            ), patch.object(ai_rec, "_requestable_picks", AsyncMock(return_value=[])), patch.object(
                ai_rec.ai_rec_store, "set_cached", AsyncMock()
            ) as set_cached:
                result = await ai_rec._generate_agentic(
                    7, query="", refresh=False, limit=12, progress=lambda status: _append(statuses, status),
                )
            self.assertEqual([item["href"] for item in result["items"]], ["/two", "/one"])
            self.assertIn("Searching your library", statuses)
            self.assertIn("Curating picks", statuses)
            self.assertEqual(
                generate_content.await_args_list[0].kwargs["tool_config"],
                ai_rec._AGENT_INITIAL_TOOL_CONFIG,
            )
            self.assertEqual(
                generate_content.await_args_list[0].kwargs["model"],
                ai_rec.Var.GEMINI_AI_REC_MODEL,
            )
            self.assertIsNone(generate_content.await_args_list[1].kwargs["tool_config"])
            set_cached.assert_awaited_once_with(7, result["items"], origin="agent")

        async def _append(values, value):
            values.append(value)

        __import__("asyncio").run(run())

    def test_agent_recovers_when_all_model_searches_are_empty(self):
        class Catalogue:
            def __init__(self, **_kwargs):
                self.payloads = {}
                self.source_counts = Counter()
                self.recovered = False

            def run(self, _name, _args):
                return []

            def recover_with_broad_browse(self):
                self.recovered = True
                self.payloads = {"card_1": _card("/recovered", title="Recovered")}
                self.source_counts["recovery_browse"] = 1
                return [{"id": "card_1", "title": "Recovered", "availability": {"playable": True}}]

            def _compact(self, identifier, payload):
                return {"id": identifier, "title": payload["title"], "availability": {"playable": True}}

        async def run():
            empty_calls = {"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "search_library", "args": {"query": "missing one"}}},
                {"functionCall": {"name": "search_library", "args": {"query": "missing two"}}},
                {"functionCall": {"name": "browse_library", "args": {"genre": "missing"}}},
            ]}}]}
            catalogues = []

            def make_catalogue(**kwargs):
                catalogue = Catalogue(**kwargs)
                catalogues.append(catalogue)
                return catalogue

            with patch.object(ai_rec.rec_engine, "_collect_signal_profile", AsyncMock(return_value={"seeds": []})), patch.object(
                ai_rec.wh_store, "get_recent", AsyncMock(return_value=[])
            ), patch.object(ai_rec.cw_store, "get_all", AsyncMock(return_value={})), patch.object(
                ai_rec.dismissed_store, "get_dismissed_ids", AsyncMock(return_value=set())
            ), patch.object(ai_rec, "_safe_stats", AsyncMock(return_value={})), patch.object(
                ai_rec, "_AgentCatalogue", side_effect=make_catalogue
            ), patch.object(
                ai_rec.gemini, "generate_content", AsyncMock(return_value=empty_calls)
            ), patch.object(
                ai_rec.gemini, "generate_json", AsyncMock(return_value={"picks": [{"id": "card_1", "reason": "grounded", "bucket": "comfort"}]})
            ), patch.object(ai_rec, "_requestable_picks", AsyncMock(return_value=[])), patch.object(
                ai_rec.ai_rec_store, "set_cached", AsyncMock()
            ):
                result = await ai_rec._generate_agentic(7, query="", refresh=False, limit=12)
            self.assertEqual([item["href"] for item in result["items"]], ["/recovered"])
            self.assertTrue(catalogues[0].recovered)

        __import__("asyncio").run(run())

    def test_reason_prompt_requires_a_concrete_personal_match(self):
        prompt = ai_rec._build_prompt("Likes crime dramas", [], "", 8)
        self.assertIn("max 9 words", prompt)
        self.assertIn("Do not start with", prompt)
        self.assertIn("from your library", prompt)

    def test_taste_summary_shares_compact_positive_and_negative_signals(self):
        cards = {
            (1, "movie"): SimpleNamespace(title="Arrival"),
            (2, "tv"): SimpleNamespace(series_title="Dark"),
            (3, "movie"): SimpleNamespace(title="Loud Comedy"),
        }
        profile = {
            "seeds": [(1, "movie"), (2, "tv")],
            "liked_tmdb": {(1, "movie")},
            "disliked_tmdb": {(3, "movie")},
            "seed_genres": Counter({"Science Fiction": 8, "Mystery": 5}),
            "seed_keywords": Counter({"time travel": 6, "existential": 3}),
            "negative_genres": Counter({"Comedy": 3}),
        }
        with patch.object(ai_rec.media_index, "card_for_tmdb_id", side_effect=lambda tid, kind: cards.get((tid, kind))):
            summary = ai_rec._taste_summary(profile, {})
        self.assertIn("Explicit likes: Arrival", summary)
        self.assertIn("Strong viewing signals: Arrival, Dark", summary)
        self.assertIn("Explicit dislikes (avoid close matches): Loud Comedy", summary)
        self.assertIn("Strong genres: Science Fiction, Mystery", summary)
        self.assertIn("Preferred themes: time travel, existential", summary)
        self.assertIn("Genres to avoid unless requested: Comedy", summary)

    def test_personalized_catalogue_ranker_uses_affinities_and_preserves_search_relevance(self):
        def card(message_id, title, genres, *, director="", keywords=None):
            return SimpleNamespace(
                message_id=message_id, title=title, tmdb_id=message_id, tmdb_kind="movie",
                series_key="", movie_key="", tmdb_genres=genres, director=director,
                tmdb_keywords=keywords or [], overview="Overview",
            )

        affinity = card(1, "Affinity", ["Drama"], director="Denis Villeneuve", keywords=["memory"])
        avoided = card(2, "Avoided", ["Comedy"], director="Other")
        profile = {
            "seed_genres": Counter({"Drama": 5}),
            "seed_keywords": Counter({"memory": 4}),
            "seed_directors": Counter({"denis villeneuve": 3}),
            "negative_genres": Counter({"Comedy": 5}),
        }
        with patch.object(rec_engine.media_index, "_items", {1: affinity, 2: avoided}):
            ranked = rec_engine.rank_catalogue_cards([avoided, affinity], profile, limit=2)
        self.assertEqual([item.title for item, _score in ranked], ["Affinity", "Avoided"])

        with patch.object(rec_engine.media_index, "_items", {1: affinity, 2: avoided}), patch.object(
            rec_engine.media_index, "card_search_score", side_effect=lambda item, query: 9 if item is avoided else 2
        ):
            searched = rec_engine.rank_catalogue_cards([affinity, avoided], profile, query="title", limit=2)
        self.assertEqual([item.title for item, _score in searched], ["Avoided", "Affinity"])

    def test_agent_final_reranker_fills_and_enforces_a_balanced_shelf(self):
        payloads = {
            f"card_{index}": {
                "href": f"/{index}", "title": f"Title {index}",
                "genres": ["Drama" if index < 7 else "Mystery"],
                "tmdbKind": "movie" if index % 2 else "tv",
            }
            for index in range(12)
        }

        class Catalogue:
            scores = {f"/{index}": float(12 - index) for index in range(12)}

            def __init__(self):
                self.payloads = payloads

            def payloads_by_href(self):
                return [(payload["href"], payload) for payload in self.payloads.values()]

        first = {**payloads["card_0"], "recReason": "Model choice", "bucket": "comfort"}
        result = ai_rec._rerank_agent_picks(
            [first], Catalogue(), {"seed_genres": Counter({"Drama": 1})}, 12, pinned_id="card_0",
        )
        self.assertEqual(len(result), 12)
        self.assertEqual(result[0]["href"], "/0")
        self.assertEqual(sum(item["bucket"] == "comfort" for item in result), 7)
        self.assertEqual(sum(item["bucket"] == "discovery" for item in result), 5)
        self.assertTrue(all(item["recReason"] for item in result))

    def test_taste_match_prompt_and_assessment_require_a_grounded_title(self):
        prompt = ai_rec._build_prompt("Likes dystopian mysteries", [], "Will I like Silo?", 8)
        self.assertTrue(ai_rec._is_taste_match_question("Will I like Silo?"))
        self.assertIn("taste-match question", prompt)
        self.assertIn("likely/maybe/unlikely", prompt)
        payloads = {"card_1": _card("/silo", title="Silo")}
        self.assertEqual(
            ai_rec._validated_assessment({"id": "card_1", "verdict": "likely", "reason": "Dystopian mystery matches your usual intrigue."}, payloads),
            {"title": "Silo", "verdict": "likely", "reason": "Dystopian mystery matches your usual intrigue."},
        )
        self.assertIsNone(ai_rec._validated_assessment({"id": "invented", "verdict": "likely", "reason": "Nope"}, payloads))

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
