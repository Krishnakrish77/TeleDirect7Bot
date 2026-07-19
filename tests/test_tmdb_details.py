import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import tmdb


class TMDBDetailExtractionTests(unittest.TestCase):
    def test_movie_details_extract_user_facing_metadata(self):
        hit = tmdb._hit_from_details("movie", 55, {
            "id": 55,
            "title": "Example Film",
            "release_date": "2025-02-01",
            "runtime": 127,
            "genres": [{"name": "Drama"}],
            "keywords": {"keywords": [{"name": "coming of age"}, {"name": "friendship"}]},
            "images": {"logos": [{"iso_639_1": "en", "file_path": "/logo.png"}]},
            "release_dates": {"results": [
                {"iso_3166_1": "GB", "release_dates": [{"certification": "15"}]},
                {"iso_3166_1": "US", "release_dates": [{"certification": "PG-13"}]},
            ]},
            "external_ids": {"imdb_id": "tt123"},
            "credits": {"cast": [{"name": "Actor"}], "crew": [{"name": "Director", "job": "Director"}]},
        })

        self.assertEqual(hit.runtime_minutes, 127)
        self.assertEqual(hit.certification, "PG-13")
        self.assertEqual(hit.keywords, ["coming of age", "friendship"])
        self.assertEqual(hit.logo_path, "/logo.png")
        self.assertEqual(hit.cast, ["Actor"])
        self.assertEqual(hit.director, "Director")

    def test_tv_details_support_tv_specific_response_shapes(self):
        hit = tmdb._hit_from_details("tv", 77, {
            "id": 77,
            "name": "Example Series",
            "first_air_date": "2020-01-01",
            "episode_run_time": [24],
            "keywords": {"results": [{"name": "anime"}]},
            "content_ratings": {"results": [
                {"iso_3166_1": "IN", "rating": "U/A 13+"},
                {"iso_3166_1": "US", "rating": "TV-14"},
            ]},
            "images": {"logos": [
                {"iso_639_1": "ja", "file_path": "/ja.png"},
                {"iso_639_1": "en", "file_path": "/en.png"},
            ]},
        })

        self.assertEqual(hit.runtime_minutes, 24)
        self.assertEqual(hit.certification, "TV-14")
        self.assertEqual(hit.keywords, ["anime"])
        self.assertEqual(hit.logo_path, "/en.png")

