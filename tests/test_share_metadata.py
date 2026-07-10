import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

hub_routes = importlib.import_module("main.server.hub_routes")
spa_routes = importlib.import_module("main.server.spa_routes")

from main.utils.hub_query import HubItem


def _video_item(**overrides) -> HubItem:
    data = {
        "message_id": 101,
        "secure_hash": "hash",
        "title": "Youth",
        "year": 2026,
        "description": "",
        "tags": [],
        "duration": 7200,
        "file_size": 1024,
        "has_thumb": True,
        "quality": "480p",
        "file_name": "Youth.mkv",
        "media_kind": "video",
        "tmdb_id": 1542352,
        "tmdb_kind": "movie",
        "poster_path": "/youth-poster.jpg",
        "overview": "Two versions are available, but this is one title.",
        "tmdb_genres": ["Drama"],
        "cast": ["Actor"],
        "director": "Director",
    }
    data.update(overrides)
    return HubItem(**data)


class ShareMetadataTest(unittest.IsolatedAsyncioTestCase):
    def _with_index(self):
        tmp = tempfile.TemporaryDirectory()
        index = Path(tmp.name) / "index.html"
        index.write_text(
            """<!doctype html>
<html lang="en">
  <head>
    <meta name="description" content="Generic TeleDirect app" />
    <title>TeleDirect</title>
  </head>
  <body><div id="root"></div></body>
</html>""",
            encoding="utf-8",
        )
        self.addCleanup(tmp.cleanup)
        return index

    async def test_movie_page_uses_open_graph_metadata(self):
        item = _video_item(movie_key="youth")
        request = SimpleNamespace(match_info={"key": "youth"}, cookies={}, query_string="")

        with (
            patch.object(hub_routes.media_index, "variants_for_movie", return_value=[item]),
            patch.object(hub_routes, "_cache_get", return_value=None),
            patch.object(hub_routes, "_cache_set", lambda *_args: None),
            patch.object(hub_routes.share_meta.Var, "URL", "https://media.example/"),
        ):
            response = await hub_routes.hub_movie(request)

        html = response.text
        self.assertIn('property="og:title" content="Youth (2026)"', html)
        self.assertIn('property="og:description" content="Two versions are available, but this is one title."', html)
        self.assertIn('property="og:type" content="video.movie"', html)
        self.assertIn('property="og:url" content="https://media.example/movie/youth"', html)
        self.assertIn('property="og:image" content="https://image.tmdb.org/t/p/w780/youth-poster.jpg"', html)

    async def test_series_page_uses_open_graph_metadata(self):
        item = _video_item(
            title="Training Day",
            year=2026,
            tmdb_kind="tv",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=1,
            poster_path="/castle-poster.jpg",
            overview="A mystery series with many episodes.",
        )
        request = SimpleNamespace(
            match_info={"key": "castle"},
            cookies={},
            query_string="",
            query={},
        )

        async def fake_prewarm(_ids):
            return None

        with (
            patch.object(hub_routes.media_index, "episodes_for_series", return_value=[item]),
            patch.object(hub_routes.thumb_cache, "prewarm_from_store", fake_prewarm),
            patch.object(hub_routes, "_cache_get", return_value=None),
            patch.object(hub_routes, "_cache_set", lambda *_args: None),
            patch.object(hub_routes.share_meta.Var, "URL", "https://media.example/"),
        ):
            response = await hub_routes.hub_series(request)

        html = response.text
        self.assertIn('property="og:title" content="Castle"', html)
        self.assertIn('property="og:description" content="A mystery series with many episodes."', html)
        self.assertIn('property="og:type" content="video.tv_show"', html)
        self.assertIn('property="og:url" content="https://media.example/series/castle"', html)
        self.assertIn('property="og:image" content="https://image.tmdb.org/t/p/w780/castle-poster.jpg"', html)

    async def test_react_watch_shell_uses_route_open_graph_metadata(self):
        item = _video_item(
            message_id=42,
            secure_hash="abc",
            title="Castle",
            tmdb_kind="tv",
            series_key="castle",
            series_title="Castle",
            season=6,
            episode=14,
            episode_title="Bad Santa",
            episode_overview="Castle and Beckett investigate a case tied to a TV star.",
            poster_path="/castle-poster.jpg",
        )
        request = SimpleNamespace(path="/app/watch/abc42")

        with (
            patch.object(spa_routes, "_APP_INDEX", self._with_index()),
            patch.object(spa_routes.media_index, "get_item", return_value=item),
            patch.object(spa_routes.share_meta.Var, "URL", "https://media.example/"),
        ):
            response = spa_routes._app_index_response(request)

        html = response.text
        self.assertIn("<title>Castle S06E14 · Bad Santa</title>", html)
        self.assertIn('property="og:title" content="Castle S06E14 · Bad Santa"', html)
        self.assertIn('property="og:description" content="Castle and Beckett investigate a case tied to a TV star."', html)
        self.assertIn('property="og:type" content="video.episode"', html)
        self.assertIn('property="og:url" content="https://media.example/app/watch/abc42"', html)
        self.assertIn('property="og:image" content="https://image.tmdb.org/t/p/w780/castle-poster.jpg"', html)
        self.assertIn('name="twitter:card" content="summary_large_image"', html)
        self.assertNotIn("Generic TeleDirect app", html)

    async def test_react_movie_shell_uses_route_open_graph_metadata(self):
        item = _video_item(movie_key="youth")
        request = SimpleNamespace(path="/app/movie/youth")

        with (
            patch.object(spa_routes, "_APP_INDEX", self._with_index()),
            patch.object(spa_routes.media_index, "variants_for_movie", return_value=[item]),
            patch.object(spa_routes.share_meta.Var, "URL", "https://media.example/"),
        ):
            response = spa_routes._app_index_response(request)

        html = response.text
        self.assertIn("<title>Youth (2026)</title>", html)
        self.assertIn('property="og:title" content="Youth (2026)"', html)
        self.assertIn('property="og:type" content="video.movie"', html)
        self.assertIn('property="og:url" content="https://media.example/app/movie/youth"', html)
        self.assertIn('property="og:image" content="https://image.tmdb.org/t/p/w780/youth-poster.jpg"', html)

    async def test_react_series_shell_uses_route_open_graph_metadata(self):
        item = _video_item(
            title="Training Day",
            year=2026,
            tmdb_kind="tv",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=1,
            poster_path="/castle-poster.jpg",
            overview="A mystery series with many episodes.",
        )
        request = SimpleNamespace(path="/app/series/castle")

        with (
            patch.object(spa_routes, "_APP_INDEX", self._with_index()),
            patch.object(spa_routes.media_index, "episodes_for_series", return_value=[item]),
            patch.object(spa_routes.share_meta.Var, "URL", "https://media.example/"),
        ):
            response = spa_routes._app_index_response(request)

        html = response.text
        self.assertIn("<title>Castle</title>", html)
        self.assertIn('property="og:title" content="Castle"', html)
        self.assertIn('property="og:description" content="A mystery series with many episodes."', html)
        self.assertIn('property="og:type" content="video.tv_show"', html)
        self.assertIn('property="og:url" content="https://media.example/app/series/castle"', html)
        self.assertIn('property="og:image" content="https://image.tmdb.org/t/p/w780/castle-poster.jpg"', html)

    async def test_react_watch_shell_suppresses_hidden_metadata(self):
        item = _video_item(
            message_id=42,
            secure_hash="abc",
            title="Hidden Movie",
            overview="This should not leak into app previews.",
            poster_path="/hidden-poster.jpg",
            hidden=True,
        )
        request = SimpleNamespace(path="/app/watch/abc42")

        with (
            patch.object(spa_routes.media_index, "get_item", return_value=item),
            patch.object(spa_routes.share_meta.Var, "URL", "https://media.example/"),
        ):
            meta = spa_routes._app_share_metadata(request.path)

        self.assertIsNone(meta)


if __name__ == "__main__":
    unittest.main()
