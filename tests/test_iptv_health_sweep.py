import asyncio
import os
import unittest
from unittest import mock


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("IPTV_STORE_PATH", "/tmp/iptv_test_sweep.json")

import importlib

iptv_routes = importlib.import_module("main.server.iptv_routes")
from main.server.iptv_routes import _run_health_sweep
from main.utils import iptv_store


def _channel(name: str, healthy: bool) -> dict:
    return {"id": name.lower(), "name": name, "streamUrl": f"https://example.com/{name}.m3u8", "streamHeaders": {}}


class IptvHealthSweepTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        iptv_routes._HEALTH_SWEEP = None
        self.store_patcher = mock.patch("main.server.iptv_routes.iptv_store")

    async def asyncTearDown(self):
        iptv_routes._HEALTH_SWEEP = None

    async def test_disable_after_three_failed_attempts(self):
        healthy = _channel("Alive", True)
        dead = _channel("Dead", False)
        store = self.store_patcher.start()
        store.list_channels = mock.AsyncMock(return_value=[healthy, dead])
        store.save_channel = mock.AsyncMock(return_value=(True, {**dead, "enabled": False}, ""))
        probe_calls: list[str] = []

        async def fake_probe(channel):
            probe_calls.append(channel["name"])
            return channel["streamUrl"] != "https://example.com/Dead.m3u8"

        with mock.patch.object(iptv_routes, "_health_probe_channel", side_effect=fake_probe):
            await _run_health_sweep("disable", 3)

        # 3 attempts for the dead channel, 1 for the healthy one (short-circuits on first success)
        self.assertEqual(probe_calls.count("Dead"), 3)
        self.assertEqual(probe_calls.count("Alive"), 1)
        store.save_channel.assert_awaited_once()
        self.assertEqual(store.save_channel.call_args[0][0]["id"], "dead")
        self.assertIs(store.save_channel.call_args[0][0]["enabled"], False)
        # processed counts every probed channel, not just the dead ones
        self.assertEqual(iptv_routes._HEALTH_SWEEP["processed"], 2)
        self.assertEqual(iptv_routes._HEALTH_SWEEP["affected"], [{"id": "dead", "name": "Dead", "action": "disabled"}])
        self.assertIs(iptv_routes._HEALTH_SWEEP["running"], False)

    async def test_healthy_channel_untouched_even_when_second_attempt_succeeds(self):
        flaky = _channel("Flaky", True)
        store = self.store_patcher.start()
        store.list_channels = mock.AsyncMock(return_value=[flaky])
        store.save_channel = mock.AsyncMock()

        attempts = {"n": 0}

        async def fake_probe(_channel):
            attempts["n"] += 1
            return attempts["n"] >= 2  # fails once, then succeeds

        with mock.patch.object(iptv_routes, "_health_probe_channel", side_effect=fake_probe):
            await _run_health_sweep("disable", 3)

        store.save_channel.assert_not_awaited()
        self.assertEqual(iptv_routes._HEALTH_SWEEP["processed"], 1)
        self.assertEqual(iptv_routes._HEALTH_SWEEP["affected"], [])

    async def test_hard_delete_mode(self):
        dead = _channel("Dead", False)
        store = self.store_patcher.start()
        store.list_channels = mock.AsyncMock(return_value=[dead])
        store.delete_channel = mock.AsyncMock(return_value=True)

        async def fake_probe(_channel):
            return False

        with mock.patch.object(iptv_routes, "_health_probe_channel", side_effect=fake_probe):
            await _run_health_sweep("delete", 3)

        # delete mode purges the whole catalogue: disabled channels included
        store.list_channels.assert_awaited_once_with(include_disabled=True)
        store.delete_channel.assert_awaited_once_with("dead")
        self.assertEqual(iptv_routes._HEALTH_SWEEP["affected"], [{"id": "dead", "name": "Dead", "action": "deleted"}])

    async def test_disable_mode_ignores_disabled_channels(self):
        dead_disabled = {**_channel("DeadDisabled", False), "enabled": False}
        store = self.store_patcher.start()
        store.list_channels = mock.AsyncMock(return_value=[])
        store.save_channel = mock.AsyncMock()

        async def fake_probe(_channel):
            return False

        with mock.patch.object(iptv_routes, "_health_probe_channel", side_effect=fake_probe):
            await _run_health_sweep("disable", 3)

        # disable mode only sweeps the enabled working set
        store.list_channels.assert_awaited_once_with(include_disabled=False)
        store.save_channel.assert_not_awaited()

    async def test_no_channels_completes_immediately(self):
        store = self.store_patcher.start()
        store.list_channels = mock.AsyncMock(return_value=[])
        await _run_health_sweep("disable", 3)
        self.assertIs(iptv_routes._HEALTH_SWEEP["running"], False)
        self.assertIsNotNone(iptv_routes._HEALTH_SWEEP["finishedAt"])

    async def test_sweep_survives_probe_crash(self):
        # A raw socket error (uvloop OSError, e.g. network unreachable) must
        # count the channel as dead, not blow up the whole sweep task.
        dead = _channel("Dead", False)
        store = self.store_patcher.start()
        store.list_channels = mock.AsyncMock(return_value=[dead])
        store.save_channel = mock.AsyncMock(return_value=(True, dead, ""))

        async def crash(_channel):
            raise OSError("Network is unreachable")

        with mock.patch.object(iptv_routes, "_health_probe_channel", side_effect=crash):
            await _run_health_sweep("disable", 3)

        store.save_channel.assert_awaited_once()
        self.assertEqual(iptv_routes._HEALTH_SWEEP["affected"], [{"id": "dead", "name": "Dead", "action": "disabled"}])


if __name__ == "__main__":
    unittest.main()
