"""Service worker versioning — the cache name must rotate per deploy.

Regression for the "stale PWA" bug: a hand-pinned cache name ('td-v6')
survived redeploys, so the cache-first asset handler kept serving the
previous deploy's chunks forever — users were stuck on old code ("the
overlay never goes", even after refresh).
"""
import os
import unittest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.server.hub_routes import (  # module import — main.server.hub_routes is shadowed by the RouteTableDef re-export
    _SW_JS,
    _cache_version,
)


class ServiceWorkerVersionTest(unittest.TestCase):
    def test_no_unsubstituted_placeholders(self):
        self.assertNotIn("__CACHE_VERSION__", _SW_JS)
        self.assertNotIn("__SHELL__", _SW_JS)

    def test_cache_name_is_content_hashed(self):
        line = next(
            l for l in _SW_JS.splitlines() if l.startswith("const CACHE")
        )
        self.assertTrue(line.startswith("const CACHE = 'td-"), line)
        # 12-hex version embedded
        version = line.split("'td-")[1].rstrip("';")
        self.assertRegex(version, r"^[0-9a-f]{12}$")

    def test_version_changes_when_chunk_content_changes(self):
        # Hash covers chunk BYTES, not just the manifest — a redeploy that
        # rewrites chunks rotates the version even if the manifest is
        # name-identical.
        import importlib
        from pathlib import Path
        hr = importlib.import_module("main.server.hub_routes")
        v1 = hr._cache_version()
        manifest = (
            Path(hr.__file__).parent
            / "static" / "app" / ".vite" / "manifest.json"
        )
        original = manifest.read_bytes()
        try:
            mutated = dict(hr.json.loads(original.decode()))
            mutated["_test_marker"] = "x"
            manifest.write_bytes(hr.json.dumps(mutated).encode())
            v2 = hr._cache_version()
            self.assertNotEqual(v1, v2)
        finally:
            manifest.write_bytes(original)

    def test_activate_deletes_old_caches(self):
        # The activate handler must clean up every cache but the current one.
        self.assertIn(
            "keys.filter(k => k !== CACHE).map(k => caches.delete(k))", _SW_JS,
        )


if __name__ == "__main__":
    unittest.main()
