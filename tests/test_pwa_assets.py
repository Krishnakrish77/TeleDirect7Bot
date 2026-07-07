import json
import importlib
import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

hub_routes = importlib.import_module("main.server.hub_routes")


class PwaAssetsTest(unittest.TestCase):
    def test_manifest_launches_react_app(self):
        manifest = json.loads(hub_routes._MANIFEST_JSON)

        self.assertEqual(manifest["id"], "/app")
        self.assertEqual(manifest["start_url"], "/app")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertTrue(any(icon["sizes"] == "192x192" for icon in manifest["icons"]))

    def test_service_worker_keeps_app_shell_cacheable(self):
        worker = hub_routes._SW_JS

        self.assertIn("const CACHE = 'td-v4'", worker)
        self.assertIn("const SHELL = ['/', '/app'", worker)
        self.assertNotIn("url.pathname === '/app'", worker)
        self.assertNotIn("url.pathname.startsWith('/static/app/')", worker)
        self.assertIn("url.pathname.startsWith('/api/')", worker)


if __name__ == "__main__":
    unittest.main()
