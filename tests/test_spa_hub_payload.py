import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server.spa_routes import (
    _album_detail_payload,
    _app_download_redirect,
    _budget_home_shelves,
    _compact_hub_card_payload,
    _home_recommendation_shelf,
    _hub_card,
    _home_shelf_limit,
    _related_rows,
    _video_choice_payload,
    _watched_movie_keys_for_keys,
)
from main.utils import media_index
from main.utils.hub_query import HubItem, MovieGroup, SeriesGroup

hub_routes = importlib.import_module("main.server.hub_routes")
spa_routes = importlib.import_module("main.server.spa_routes")
admin_routes = importlib.import_module("main.server.admin_routes")
watchlist_routes = importlib.import_module("main.server.watchlist_routes")


def _video_item(
    message_id: int = 101,
    *,
    secure_hash: str = "hash",
    title: str = "Kalki",
    movie_key: str = "",
    genres: list[str] | None = None,
    **overrides,
) -> HubItem:
    data = {
        "message_id": message_id,
        "secure_hash": secure_hash,
        "title": title,
        "year": 2024,
        "description": "",
        "tags": [],
        "duration": 7200,
        "file_size": 1024,
        "has_thumb": True,
        "quality": "1080p",
        "file_name": f"{title}.mkv",
        "movie_key": movie_key,
        "tmdb_genres": genres or ["Action"],
        "media_kind": "video",
    }
    data.update(overrides)
    return HubItem(**data)


class SpaHubPayloadTest(unittest.TestCase):
    def test_watchlist_excludes_audio_saved_items(self):
        video = {"item_id": "101", "kind": "video", "title": "Film"}
        audio = {"item_id": "202", "kind": "audio", "title": "Song"}
        album = {"item_id": "album:record", "kind": "album", "title": "Record"}

        async def get_ids(_user_id):
            return ["101", "202", "album:record"]

        async def get_continue(_user_id):
            return {}

        with patch.object(watchlist_routes.watchlist_store, "get_ids", get_ids), patch.object(
            watchlist_routes, "_resolve_item", side_effect=[video, audio, album]
        ), patch.object(watchlist_routes.cw_store, "get_all", get_continue):
            items = asyncio.run(watchlist_routes._items_for_user(1))

        self.assertEqual(items, [{**video, "cw_pct": None}])

    def test_album_payload_links_each_artist_credit_individually(self):
        track = HubItem(
            message_id=1,
            secure_hash="hash",
            title="Pattampoochi",
            year=2026,
            description="",
            tags=[],
            duration=120,
            file_size=1024,
            has_thumb=False,
            media_kind="audio",
            artist="G. V. Prakash Kumar, Sublahshini",
            album_title="Vishwanath & Sons",
            album_key="vishwanath-sons",
        )
        with patch.object(media_index, "tracks_for_album", return_value=[track]), patch(
            "main.server.spa_routes._related_rows", return_value=[]
        ):
            payload = _album_detail_payload("vishwanath-sons")

        self.assertEqual(payload["artistHref"], "/app/artist/g-v-prakash-kumar")
        self.assertEqual(payload["artistCredits"], [
            {"name": "G. V. Prakash Kumar", "href": "/app/artist/g-v-prakash-kumar"},
            {"name": "Sublahshini", "href": "/app/artist/sublahshini"},
        ])

    def test_app_watch_download_redirects_to_raw_stream(self):
        request = SimpleNamespace(
            rel_url=SimpleNamespace(query={"download": "1"}),
            match_info={"tail": "watch/AgADPSEAAoYXqFU2930"},
        )

        redirect = _app_download_redirect(request)

        self.assertIsNotNone(redirect)
        self.assertEqual(redirect.status, 302)
        self.assertEqual(
            redirect.headers["Location"],
            "/AgADPSEAAoYXqFU2930?download=1",
        )

    def test_app_watch_download_redirect_preserves_query_params(self):
        request = SimpleNamespace(
            rel_url=SimpleNamespace(query={"download": "yes", "vt": "abc"}),
            match_info={"tail": "watch/AgADPSEAAoYXqFU2930"},
        )

        redirect = _app_download_redirect(request)

        self.assertIsNotNone(redirect)
        self.assertIn("/AgADPSEAAoYXqFU2930?", redirect.headers["Location"])
        self.assertIn("download=1", redirect.headers["Location"])
        self.assertIn("vt=abc", redirect.headers["Location"])

    def test_app_download_redirect_ignores_non_watch_paths(self):
        request = SimpleNamespace(
            rel_url=SimpleNamespace(query={"download": "1"}),
            match_info={"tail": "series/castle"},
        )

        self.assertIsNone(_app_download_redirect(request))

    def test_compact_hub_card_keeps_renderer_fields_only(self):
        payload = {
            "type": "movie",
            "itemId": "movie:kalki",
            "messageId": 101,
            "secureHash": "hash",
            "title": "Kalki",
            "subtitle": "1 version",
            "year": 2024,
            "mediaKind": "video",
            "posterUrl": "/thumb/hash101.jpg",
            "thumbUrl": "/thumb/hash101.jpg",
            "backdropUrl": "/api/tmdb-image/w1280/backdrop.jpg",
            "duration": 10800,
            "durationLabel": "3h",
            "fileSize": 1024,
            "fileSizeLabel": "1 KB",
            "quality": "1080p",
            "genres": ["Action"],
            "tags": ["featured"],
            "overview": "A long overview that belongs on detail pages.",
            "tmdbId": 123,
            "tmdbKind": "movie",
            "imdbId": "tt1234567",
            "imdbHref": "https://www.imdb.com/title/tt1234567/",
            "externalRating": {"provider": "TMDB", "value": 8.1, "label": "8.1", "count": 1000},
            "ratingCounts": {"up": 3, "down": 1},
            "artist": "",
            "albumTitle": "",
            "trailerKey": "abc123",
            "href": "/app/movie/kalki",
            "playHref": "/app/watch/hash101",
            "detailsHref": "/app/movie/kalki",
            "streamHref": "/hash101",
            "watchKey": "hash101",
            "eyebrow": "Movie",
            "badge": "1 version",
            "aspect": "poster",
            "variantCount": 1,
            "watched": True,
            "newEpisode": {
                "label": "S01E02",
                "title": "The New One",
                "playHref": "/app/watch/hash102",
                "watchKey": "hash102",
            },
        }

        compact = _compact_hub_card_payload(payload)

        self.assertEqual(compact["title"], "Kalki")
        self.assertEqual(compact["posterUrl"], "/thumb/hash101.jpg")
        self.assertEqual(compact["externalRating"]["label"], "8.1")
        self.assertEqual(compact["ratingCounts"], {"up": 3, "down": 1})
        self.assertEqual(compact["watchKey"], "hash101")
        self.assertEqual(compact["variantCount"], 1)
        self.assertTrue(compact["watched"])
        self.assertEqual(compact["newEpisode"]["label"], "S01E02")
        for unused in (
            "messageId",
            "secureHash",
            "thumbUrl",
            "backdropUrl",
            "duration",
            "fileSize",
            "fileSizeLabel",
            "tags",
            "overview",
            "tmdbId",
            "tmdbKind",
            "imdbId",
            "imdbHref",
            "streamHref",
            "eyebrow",
            "badge",
        ):
            self.assertNotIn(unused, compact)

    def test_hub_card_marks_direct_video_as_watched(self):
        item = _video_item(message_id=101, secure_hash="hash")

        payload = _hub_card(item, watched_keys={"hash101"}, watched_movie_keys=set())

        self.assertTrue(payload["watched"])

    def test_video_choice_payload_marks_detail_variant_as_watched(self):
        item = _video_item(message_id=101, secure_hash="hash")

        payload = _video_choice_payload(item, watched_keys={"hash101"})

        self.assertTrue(payload["watched"])

    def test_hub_card_marks_movie_group_as_watched_without_variant_scan(self):
        variant = _video_item(
            message_id=202,
            secure_hash="moviehash",
            movie_key="kalki::2024",
        )
        group = MovieGroup(
            movie_key="kalki::2024",
            title="Kalki",
            year=2024,
            variant_count=2,
            latest_message_id=202,
            poster_item=variant,
        )

        with patch.object(media_index, "get_item", return_value=variant) as get_item:
            watched_movie_keys = _watched_movie_keys_for_keys({"moviehash202"})

        payload = _hub_card(group, watched_keys={"moviehash202"}, watched_movie_keys=watched_movie_keys)

        get_item.assert_called_once_with(202)
        self.assertEqual(watched_movie_keys, {"kalki::2024"})
        self.assertTrue(payload["watched"])

    def test_related_rows_preserve_watched_status(self):
        source = _video_item(message_id=300, secure_hash="source", movie_key="source::2024")
        related = _video_item(
            message_id=301,
            secure_hash="related",
            title="Related Movie",
            movie_key="related::2024",
        )
        group = MovieGroup(
            movie_key="related::2024",
            title="Related Movie",
            year=2024,
            variant_count=1,
            latest_message_id=301,
            poster_item=related,
        )

        with patch.object(media_index, "query_grouped", return_value=([group], 1)):
            rows = _related_rows(
                source,
                watched_keys={"related301"},
                watched_movie_keys={"related::2024"},
            )

        self.assertEqual(rows[0]["items"][0]["itemId"], "movie:related::2024")
        self.assertTrue(rows[0]["items"][0]["watched"])

    def test_movie_group_card_prefers_tmdb_poster_over_newer_telegram_thumb(self):
        previous = dict(media_index._items)
        media_index._items.clear()
        try:
            plain_newer = _video_item(
                message_id=302,
                secure_hash="newer",
                title="Kalki",
                movie_key="kalki::2024",
                tmdb_id=None,
                poster_path="",
            )
            enriched_older = _video_item(
                message_id=301,
                secure_hash="older",
                title="Kalki",
                movie_key="kalki::2024",
                tmdb_id=123,
                tmdb_kind="movie",
                poster_path="/tmdb-poster.jpg",
            )
            media_index._items.update({
                plain_newer.message_id: plain_newer,
                enriched_older.message_id: enriched_older,
            })

            cards, total = media_index.query_grouped(view="movies", limit=10)

            self.assertEqual(total, 1)
            self.assertEqual(cards[0].poster_item.message_id, 302)
            self.assertEqual(cards[0].art_item.message_id, 301)
            payload = _hub_card(cards[0])
            self.assertEqual(payload["posterUrl"], "/api/tmdb-image/w342/tmdb-poster.jpg")
            self.assertEqual(payload["watchKey"], "newer302")
        finally:
            media_index._items.clear()
            media_index._items.update(previous)

    def test_series_group_card_prefers_tmdb_poster_over_newer_telegram_thumb(self):
        previous = dict(media_index._items)
        media_index._items.clear()
        try:
            plain_newer = _video_item(
                message_id=402,
                secure_hash="seriesnew",
                title="Castle S01E02",
                series_key="castle",
                series_title="Castle",
                season=1,
                episode=2,
                movie_key="",
                tmdb_id=None,
                poster_path="",
            )
            enriched_older = _video_item(
                message_id=401,
                secure_hash="seriesold",
                title="Castle S01E01",
                series_key="castle",
                series_title="Castle",
                season=1,
                episode=1,
                movie_key="",
                tmdb_id=456,
                tmdb_kind="tv",
                poster_path="/series-poster.jpg",
            )
            media_index._items.update({
                plain_newer.message_id: plain_newer,
                enriched_older.message_id: enriched_older,
            })

            cards, total = media_index.query_grouped(view="series", limit=10)

            self.assertEqual(total, 1)
            self.assertEqual(cards[0].poster_item.message_id, 402)
            self.assertEqual(cards[0].art_item.message_id, 401)
            payload = _hub_card(cards[0])
            self.assertEqual(payload["posterUrl"], "/api/tmdb-image/w342/series-poster.jpg")
            self.assertEqual(payload["watchKey"], "seriesnew402")
        finally:
            media_index._items.clear()
            media_index._items.update(previous)

    def test_new_episode_series_card_highlights_latest_episode(self):
        latest = _video_item(
            message_id=452,
            secure_hash="latest",
            title="Castle S01E02",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            episode_title="Nanny McDead",
            movie_key="",
        )
        poster = _video_item(
            message_id=451,
            secure_hash="poster",
            title="Castle S01E01",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=1,
            movie_key="",
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=452,
            poster_item=poster,
            new_episode_item=latest,
        )

        payload = _hub_card(group)

        self.assertEqual(payload["type"], "series")
        self.assertEqual(payload["href"], "/app/series/castle")
        self.assertEqual(payload["detailsHref"], "/app/series/castle")
        self.assertEqual(payload["playHref"], "/app/watch/latest452")
        self.assertEqual(payload["watchKey"], "latest452")
        self.assertEqual(payload["newEpisode"], {
            "label": "S01E02",
            "title": "Nanny McDead",
            "playHref": "/app/watch/latest452",
            "watchKey": "latest452",
        })

    def test_new_episode_payload_falls_back_to_item_title_without_episode_label(self):
        latest = _video_item(
            message_id=462,
            secure_hash="latest",
            title="Castle Latest Upload",
            series_key="castle",
            series_title="Castle",
            season=None,
            episode=None,
            episode_title="",
            movie_key="",
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=1,
            season_count=1,
            latest_message_id=462,
            poster_item=latest,
            new_episode_item=latest,
        )

        payload = _hub_card(group)

        self.assertEqual(payload["newEpisode"], {
            "label": "",
            "title": "Castle Latest Upload",
            "playHref": "/app/watch/latest462",
            "watchKey": "latest462",
        })

    def test_new_episode_payload_omits_noisy_title_when_label_is_available(self):
        latest = _video_item(
            message_id=463,
            secure_hash="latest",
            title="Castle.S01E02.1080p.WEB-DL",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            episode_title="",
            movie_key="",
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=463,
            poster_item=latest,
            new_episode_item=latest,
        )

        payload = _hub_card(group)

        self.assertEqual(payload["newEpisode"], {
            "label": "S01E02",
            "title": "",
            "playHref": "/app/watch/latest463",
            "watchKey": "latest463",
        })

    def test_new_episode_payload_preserves_clean_raw_title_when_label_is_available(self):
        latest = _video_item(
            message_id=464,
            secure_hash="latest",
            title="Nanny McDead",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            episode_title="",
            movie_key="",
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=464,
            poster_item=latest,
            new_episode_item=latest,
        )

        payload = _hub_card(group)

        self.assertEqual(payload["newEpisode"], {
            "label": "S01E02",
            "title": "Nanny McDead",
            "playHref": "/app/watch/latest464",
            "watchKey": "latest464",
        })

    def test_new_episode_series_card_uses_episode_rating(self):
        latest = _video_item(
            message_id=465,
            secure_hash="latest",
            title="Castle S01E02",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            episode_title="Nanny McDead",
            movie_key="",
            tmdb_id=1419,
            tmdb_kind="tv",
            tmdb_vote_average=8.5,
            tmdb_vote_count=2000,
            episode_tmdb_vote_average=7.2,
            episode_tmdb_vote_count=42,
            episode_tmdb_vote_checked_at=1.0,
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=465,
            poster_item=latest,
            new_episode_item=latest,
        )

        payload = _hub_card(group)

        self.assertEqual(payload["externalRating"], {
            "provider": "TMDB",
            "value": 7.2,
            "label": "7.2",
            "count": 42,
        })

    def test_new_episode_series_card_does_not_fall_back_to_series_rating(self):
        latest = _video_item(
            message_id=466,
            secure_hash="latest",
            title="Castle S01E02",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            movie_key="",
            tmdb_vote_average=8.5,
            tmdb_vote_count=2000,
            episode_tmdb_vote_average=0.0,
            episode_tmdb_vote_count=0,
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=466,
            poster_item=latest,
            new_episode_item=latest,
        )

        payload = _hub_card(group)

        self.assertNotIn("externalRating", payload)

    def test_new_episode_series_card_uses_latest_episode_rating_counts(self):
        poster = _video_item(
            message_id=467,
            secure_hash="poster",
            title="Castle S01E01",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=1,
            movie_key="",
        )
        latest = _video_item(
            message_id=468,
            secure_hash="latest",
            title="Castle S01E02",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            movie_key="",
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=468,
            poster_item=poster,
            new_episode_item=latest,
        )

        payload = _hub_card(
            group,
            rating_counts={
                poster.message_id: {"up": 9, "down": 0},
                latest.message_id: {"up": 2, "down": 1},
            },
        )

        self.assertEqual(payload["ratingCounts"], {"up": 2, "down": 1})

    def test_clear_tmdb_fields_clears_episode_rating(self):
        item = _video_item(
            message_id=469,
            secure_hash="latest",
            title="Castle S01E02",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            movie_key="",
            tmdb_id=1419,
            tmdb_kind="tv",
            tmdb_vote_average=8.5,
            tmdb_vote_count=2000,
            episode_tmdb_vote_average=7.2,
            episode_tmdb_vote_count=42,
            episode_tmdb_vote_checked_at=1.0,
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=469,
            poster_item=item,
            new_episode_item=item,
        )

        admin_routes._clear_tmdb_fields(item)
        payload = _hub_card(group)

        self.assertIsNone(item.tmdb_id)
        self.assertEqual(item.tmdb_vote_average, 0.0)
        self.assertEqual(item.tmdb_vote_count, 0)
        self.assertEqual(item.tmdb_vote_checked_at, 0.0)
        self.assertEqual(item.episode_tmdb_vote_average, 0.0)
        self.assertEqual(item.episode_tmdb_vote_count, 0)
        self.assertEqual(item.episode_tmdb_vote_checked_at, 0.0)
        self.assertNotIn("externalRating", payload)

    def test_stale_tmdb_rating_is_not_rendered_without_tmdb_id(self):
        item = _video_item(
            message_id=470,
            secure_hash="stale",
            title="Stale Rating",
            tmdb_id=None,
            tmdb_kind="",
            tmdb_vote_average=9.1,
            tmdb_vote_count=100,
        )

        payload = _hub_card(item)

        self.assertIsNone(payload["externalRating"])

    def test_classic_series_card_renders_new_episode_highlight(self):
        latest = _video_item(
            message_id=472,
            secure_hash="latest",
            title="Castle S01E02",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            episode_title="Nanny McDead",
            movie_key="",
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=472,
            poster_item=latest,
            new_episode_item=latest,
        )

        html = asyncio.run(hub_routes._env.get_template("_card.html").render_async(item=group))

        self.assertIn("/series/castle", html)
        self.assertIn("New", html)
        self.assertIn("S01E02", html)
        self.assertIn("Nanny McDead", html)

    def test_classic_series_card_omits_noisy_fallback_when_episode_label_exists(self):
        latest = _video_item(
            message_id=473,
            secure_hash="latest",
            title="Castle.S01E02.1080p.WEB-DL",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            episode_title="",
            movie_key="",
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=473,
            poster_item=latest,
            new_episode_item=latest,
        )

        html = asyncio.run(hub_routes._env.get_template("_card.html").render_async(item=group))

        self.assertIn("S01E02", html)
        self.assertNotIn("Castle.S01E02.1080p.WEB-DL", html)

    def test_classic_series_card_preserves_clean_raw_title_when_episode_label_exists(self):
        latest = _video_item(
            message_id=474,
            secure_hash="latest",
            title="Nanny McDead",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=2,
            episode_title="",
            movie_key="",
        )
        group = SeriesGroup(
            series_key="castle",
            series_title="Castle",
            episode_count=2,
            season_count=1,
            latest_message_id=474,
            poster_item=latest,
            new_episode_item=latest,
        )

        html = asyncio.run(hub_routes._env.get_template("_card.html").render_async(item=group))

        self.assertIn("S01E02", html)
        self.assertIn("Nanny McDead", html)

    def test_raw_movie_card_prefers_tmdb_poster_from_group_sibling(self):
        previous = dict(media_index._items)
        media_index._items.clear()
        try:
            plain_newer = _video_item(
                message_id=502,
                secure_hash="newmovie",
                title="Kalki",
                movie_key="kalki::2024",
                tmdb_id=None,
                poster_path="",
            )
            enriched_older = _video_item(
                message_id=501,
                secure_hash="oldmovie",
                title="Kalki",
                movie_key="kalki::2024",
                tmdb_id=123,
                tmdb_kind="movie",
                poster_path="/tmdb-movie.jpg",
            )
            media_index._items.update({
                plain_newer.message_id: plain_newer,
                enriched_older.message_id: enriched_older,
            })

            payload = _hub_card(plain_newer)

            self.assertEqual(payload["posterUrl"], "/api/tmdb-image/w342/tmdb-movie.jpg")
            self.assertEqual(payload["watchKey"], "newmovie502")
            self.assertEqual(media_index.poster_path_for_item(plain_newer, cache={}), "/tmdb-movie.jpg")
        finally:
            media_index._items.clear()
            media_index._items.update(previous)

    def test_raw_series_episode_card_prefers_tmdb_poster_from_group_sibling(self):
        previous = dict(media_index._items)
        media_index._items.clear()
        try:
            plain_newer = _video_item(
                message_id=602,
                secure_hash="newep",
                title="Castle S01E02",
                series_key="castle",
                series_title="Castle",
                season=1,
                episode=2,
                movie_key="",
                tmdb_id=None,
                poster_path="",
            )
            enriched_older = _video_item(
                message_id=601,
                secure_hash="oldep",
                title="Castle S01E01",
                series_key="castle",
                series_title="Castle",
                season=1,
                episode=1,
                movie_key="",
                tmdb_id=456,
                tmdb_kind="tv",
                poster_path="/tmdb-series.jpg",
            )
            media_index._items.update({
                plain_newer.message_id: plain_newer,
                enriched_older.message_id: enriched_older,
            })

            payload = _hub_card(plain_newer)

            self.assertEqual(payload["posterUrl"], "/api/tmdb-image/w342/tmdb-series.jpg")
            self.assertEqual(payload["watchKey"], "newep602")
            self.assertEqual(media_index.poster_path_for_item(plain_newer, cache={}), "/tmdb-series.jpg")
        finally:
            media_index._items.clear()
            media_index._items.update(previous)

    def test_new_episodes_shelf_collapses_multiple_episodes_per_series(self):
        previous = dict(media_index._items)
        media_index._items.clear()
        try:
            castle_old = _video_item(
                message_id=701,
                secure_hash="castleold",
                title="Castle S01E01",
                series_key="castle",
                series_title="Castle",
                season=1,
                episode=1,
                movie_key="",
            )
            office = _video_item(
                message_id=702,
                secure_hash="office",
                title="The Office S01E01",
                series_key="the-office",
                series_title="The Office",
                season=1,
                episode=1,
                movie_key="",
            )
            lost = _video_item(
                message_id=703,
                secure_hash="lost",
                title="Lost S01E01",
                series_key="lost",
                series_title="Lost",
                season=1,
                episode=1,
                movie_key="",
            )
            castle_latest = _video_item(
                message_id=704,
                secure_hash="castlelatest",
                title="Castle S01E02",
                series_key="castle",
                series_title="Castle",
                season=1,
                episode=2,
                episode_title="Nanny McDead",
                movie_key="",
            )
            media_index._items.update({
                item.message_id: item
                for item in (castle_old, office, lost, castle_latest)
            })

            new_shelf = next(shelf for shelf in media_index.shelves(per_shelf=10) if shelf["name"] == "New episodes")

            self.assertEqual(new_shelf["total"], 3)
            self.assertEqual([item.series_key for item in new_shelf["items"]], ["castle", "lost", "the-office"])
            self.assertEqual([item.new_episode_item.message_id for item in new_shelf["items"]], [704, 703, 702])
        finally:
            media_index._items.clear()
            media_index._items.update(previous)

    def test_new_episodes_shelf_surfaces_single_updated_series(self):
        previous = dict(media_index._items)
        media_index._items.clear()
        try:
            episodes = [
                _video_item(
                    message_id=801 + idx,
                    secure_hash=f"castle{idx}",
                    title=f"Castle S01E0{idx + 1}",
                    series_key="castle",
                    series_title="Castle",
                    season=1,
                    episode=idx + 1,
                    movie_key="",
                )
                for idx in range(3)
            ]
            media_index._items.update({item.message_id: item for item in episodes})

            new_shelf = next(shelf for shelf in media_index.shelves(per_shelf=10) if shelf["name"] == "New episodes")

            self.assertEqual(new_shelf["total"], 1)
            self.assertEqual([item.series_key for item in new_shelf["items"]], ["castle"])
            self.assertEqual(new_shelf["items"][0].new_episode_item.message_id, 803)
        finally:
            media_index._items.clear()
            media_index._items.update(previous)

    def test_home_shelf_budget_keeps_high_signal_rows(self):
        shelves = [
            {"name": "Recently added", "items": [1]},
            {"name": "Series", "items": [1]},
            {"name": "Recently added movies", "items": [1]},
            {"name": "Hidden gems", "items": [1]},
            {"name": "Action", "items": [1]},
            {"name": "Drama", "items": [1]},
            {"name": "Music", "items": [1]},
            {"name": "Recommended for you", "items": [1]},
            {"name": "Because you like Mystery", "items": [1]},
            {"name": "Trending", "items": [1]},
            {"name": "Most Played", "items": [1]},
            {"name": "New episodes", "items": [1]},
        ]

        budgeted = _budget_home_shelves(shelves, limit=7)

        self.assertEqual(
            [shelf["name"] for shelf in budgeted],
            [
                "Recommended for you",
                "Because you like Mystery",
                "Recently added",
                "New episodes",
                "Trending",
                "Most Played",
                "Music",
            ],
        )

    def test_home_shelf_budget_uses_fallback_rows_when_priority_rows_are_missing(self):
        shelves = [
            {"name": "Action", "items": [1]},
            {"name": "Drama", "items": [1]},
            {"name": "Hidden gems", "items": [1]},
            {"name": "Series", "items": [1]},
            {"name": "Music", "items": []},
        ]

        budgeted = _budget_home_shelves(shelves, limit=3)

        self.assertEqual(
            [shelf["name"] for shelf in budgeted],
            ["Series", "Hidden gems", "Action"],
        )

    def test_home_shelf_limit_reads_env_with_bounds(self):
        with patch.dict(os.environ, {"HUB_HOME_SHELVES": "10"}):
            self.assertEqual(_home_shelf_limit(), 10)
        with patch.dict(os.environ, {"HUB_HOME_SHELVES": "99"}):
            self.assertEqual(_home_shelf_limit(), 12)
        with patch.dict(os.environ, {"HUB_HOME_SHELVES": "bad"}):
            self.assertEqual(_home_shelf_limit(), 7)


class HomeRecommendationShelfTest(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_one_profile_for_rows_and_card_reasons(self):
        profile = {"seed_genres": {"Drama": 4.0}}
        dismissed = {(10, "movie")}
        rec_items = [object()]
        personal_shelves = [{"name": "Because you like Drama", "items": [object()]}]

        with (
            patch.object(
                spa_routes.rec_engine,
                "_collect_signal_profile",
                new=AsyncMock(return_value=profile),
            ) as collect_profile,
            patch.object(
                spa_routes.rec_engine.dismissed_store,
                "get_dismissed_ids",
                new=AsyncMock(return_value=dismissed),
            ) as get_dismissed,
            patch.object(
                spa_routes.rec_engine,
                "get_recommendations",
                new=AsyncMock(return_value=rec_items),
            ) as get_recommendations,
            patch.object(
                spa_routes.rec_engine,
                "get_personal_shelves",
                new=AsyncMock(return_value=personal_shelves),
            ) as get_personal_shelves,
            patch.object(
                spa_routes.rec_engine,
                "get_recommendation_reasons",
                new=AsyncMock(return_value=["Because you like Drama"]),
            ) as get_reasons,
        ):
            result = await _home_recommendation_shelf(7)

        self.assertEqual(result, (rec_items, personal_shelves, ["Because you like Drama"]))
        collect_profile.assert_awaited_once_with(7)
        get_dismissed.assert_awaited_once_with(7)
        get_recommendations.assert_awaited_once_with(7, profile=profile, dismissed=dismissed)
        get_personal_shelves.assert_awaited_once_with(7, profile=profile, dismissed=dismissed)
        get_reasons.assert_awaited_once_with(7, rec_items, profile=profile)


if __name__ == "__main__":
    unittest.main()
