import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server.spa_routes import (
    _app_download_redirect,
    _budget_home_shelves,
    _compact_hub_card_payload,
    _hub_card,
    _home_shelf_limit,
    _related_rows,
    _video_choice_payload,
    _watched_movie_keys_for_keys,
)
from main.utils import media_index
from main.utils.hub_query import HubItem, MovieGroup


def _video_item(
    message_id: int = 101,
    *,
    secure_hash: str = "hash",
    title: str = "Kalki",
    movie_key: str = "",
    genres: list[str] | None = None,
) -> HubItem:
    return HubItem(
        message_id=message_id,
        secure_hash=secure_hash,
        title=title,
        year=2024,
        description="",
        tags=[],
        duration=7200,
        file_size=1024,
        has_thumb=True,
        quality="1080p",
        file_name=f"{title}.mkv",
        movie_key=movie_key,
        tmdb_genres=genres or ["Action"],
        media_kind="video",
    )


class SpaHubPayloadTest(unittest.TestCase):
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
        }

        compact = _compact_hub_card_payload(payload)

        self.assertEqual(compact["title"], "Kalki")
        self.assertEqual(compact["posterUrl"], "/thumb/hash101.jpg")
        self.assertEqual(compact["externalRating"]["label"], "8.1")
        self.assertEqual(compact["ratingCounts"], {"up": 3, "down": 1})
        self.assertEqual(compact["watchKey"], "hash101")
        self.assertEqual(compact["variantCount"], 1)
        self.assertTrue(compact["watched"])
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


if __name__ == "__main__":
    unittest.main()
