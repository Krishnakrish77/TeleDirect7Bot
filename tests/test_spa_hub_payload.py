import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server.spa_routes import _compact_hub_card_payload


class SpaHubPayloadTest(unittest.TestCase):
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
        }

        compact = _compact_hub_card_payload(payload)

        self.assertEqual(compact["title"], "Kalki")
        self.assertEqual(compact["posterUrl"], "/thumb/hash101.jpg")
        self.assertEqual(compact["externalRating"]["label"], "8.1")
        self.assertEqual(compact["ratingCounts"], {"up": 3, "down": 1})
        self.assertEqual(compact["watchKey"], "hash101")
        self.assertEqual(compact["variantCount"], 1)
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


if __name__ == "__main__":
    unittest.main()
