import json
import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

hub_routes = importlib.import_module("main.server.hub_routes")


class PwaAssetsTest(unittest.TestCase):
    def test_manifest_launches_react_app(self):
        manifest = json.loads(hub_routes._MANIFEST_JSON)

        # Keep the existing installed-app identity stable; only the launch URL
        # moves to the React shell.
        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["start_url"], "/app")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertTrue(any(icon["sizes"] == "192x192" for icon in manifest["icons"]))

    def test_service_worker_keeps_app_shell_cacheable(self):
        worker = hub_routes._SW_JS

        self.assertIn("const CACHE = 'td-v4'", worker)
        self.assertIn('const SHELL = ["/","/app"', worker)
        self.assertNotIn("url.pathname.startsWith('/static/app/')", worker)
        self.assertIn("url.pathname === '/app' || url.pathname.startsWith('/app/')", worker)
        self.assertIn("caches.match(shell)", worker)
        self.assertIn("url.pathname.startsWith('/api/')", worker)

    def test_service_worker_shell_includes_vite_entry_assets(self):
        with TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "index.html": {
                    "file": "assets/index-abc.js",
                    "css": ["assets/index-def.css"],
                    "imports": ["_shared.js"],
                    "dynamicImports": ["src/components/watch.tsx"],
                },
                "_shared.js": {
                    "file": "assets/shared-ghi.js",
                },
                "src/components/watch.tsx": {
                    "file": "assets/watch-jkl.js",
                    "imports": ["_hls.js"],
                },
                "_hls.js": {
                    "file": "assets/hls-mno.js",
                }
            }))

            self.assertEqual(
                hub_routes._load_react_app_shell_assets(manifest_path),
                [
                    "/static/app/assets/index-abc.js",
                    "/static/app/assets/index-def.css",
                    "/static/app/assets/shared-ghi.js",
                    "/static/app/assets/watch-jkl.js",
                    "/static/app/assets/hls-mno.js",
                ],
            )

    def test_service_worker_shell_uses_real_built_vite_assets_when_present(self):
        manifest_path = Path(hub_routes.__file__).resolve().parent / "static" / "app" / ".vite" / "manifest.json"
        if not manifest_path.exists():
            self.skipTest("React build manifest is not present")

        for asset in hub_routes._load_react_app_shell_assets(manifest_path):
            self.assertIn(asset, hub_routes._SW_JS)


if __name__ == "__main__":
    unittest.main()
