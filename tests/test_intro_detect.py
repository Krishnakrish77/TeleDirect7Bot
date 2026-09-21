"""Intro detection tests — synthetic fingerprints, no network, no fpcalc.

Covers the contracts that matter for the Skip intro button:
  - fpcalc base64 decoding (urlsafe, unpadded)
  - cross-episode matching (shift handling, ±1 tolerance, longest run)
  - clamp validation (15–150 s, window position)
  - manual-edit precedence (intro_source == "manual" never overwritten)
  - sweep bookkeeping (state transitions, persist calls)
"""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import intro_detect, media_index
from main.utils.hub_query import HubItem


def make_item(message_id: int, season=1, episode=None, intro_source="") -> HubItem:
    return HubItem(
        message_id=message_id,
        secure_hash=f"AgAD{message_id}hash",
        title=f"Episode {message_id}",
        year=None,
        description="",
        tags=[],
        duration=1200,  # 20 min
        file_size=500_000_000,
        has_thumb=False,
        media_kind="video",
        series_key="test-series",
        series_title="Test Series",
        season=season,
        episode=episode,
        intro_source=intro_source,
    )


def synth_fingerprint(points: int, seed: int = 1) -> list:
    """Deterministic pseudo-fingerprint — patterned so different seeds differ."""
    return [((seed * 2654435761 + i * 40503) ^ (i << 7)) & 0xFFFFFFFF for i in range(points)]


def fp_with_intro(base_seed: int, total: int, intro_start: int, intro_len: int, seed2: int) -> list:
    """First intro_len points shared with a sibling; rest unique to this episode."""
    shared = synth_fingerprint(intro_len, seed=base_seed)
    tail = synth_fingerprint(total - intro_len, seed=seed2)
    return shared + tail


class FingerprintDecodeTest(unittest.TestCase):
    def test_urlsafe_unpadded_decode(self):
        # urlsafe alphabet with '-' and '_' and no padding round-trips
        import base64
        import struct
        raw = b"\x00\x01\x02\x03" * 5
        b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        self.assertEqual(intro_detect._decode_fingerprint(b64), list(struct.unpack("<5I", raw)))

    def test_garbage_input_raises(self):
        with self.assertRaises(Exception):
            intro_detect._decode_fingerprint("!!!!not-base64!!!!")


class MatchingTest(unittest.TestCase):
    def test_identical_fingerprints_match_at_shift_zero(self):
        fp = synth_fingerprint(500, seed=7)
        run, start, shift = intro_detect._longest_contiguous_match(fp, fp)
        self.assertEqual(shift, 0)
        self.assertGreaterEqual(run, intro_detect.MIN_INTRO_POINTS)

    def test_shifted_intro_is_found_at_right_offset(self):
        # Episode A: 10s unique head, then 62.5s "intro", then unique tail.
        # Episode B: 30s unique head, then the same intro, then its own tail.
        intro_pts = int(62.5 / intro_detect.POINT_SECONDS)  # 488 points = 62.5s
        a = synth_fingerprint(int(10 / intro_detect.POINT_SECONDS), seed=101) \
            + synth_fingerprint(intro_pts, seed=42) \
            + synth_fingerprint(300, seed=102)
        b = synth_fingerprint(int(30 / intro_detect.POINT_SECONDS), seed=201) \
            + synth_fingerprint(intro_pts, seed=42) \
            + synth_fingerprint(300, seed=202)
        run, a_start, _shift = intro_detect._longest_contiguous_match(a, b)
        self.assertGreaterEqual(run, intro_pts - 2)  # tolerance for ±1 edges
        a_seconds = a_start * intro_detect.POINT_SECONDS
        self.assertAlmostEqual(a_seconds, 10.0, delta=1.0)  # intro starts ~10s into A

    def test_no_common_segment_returns_no_match(self):
        a = synth_fingerprint(500, seed=1)
        b = synth_fingerprint(500, seed=2)
        run, _, _ = intro_detect._longest_contiguous_match(a, b)
        self.assertLess(run, intro_detect.MIN_INTRO_POINTS)


class ClampTest(unittest.TestCase):
    def _pair(self, intro_pts: int, intro_start_pts: int):
        """Two episodes sharing `intro_pts` points at `intro_start_pts`."""
        head = intro_start_pts
        a = synth_fingerprint(head, seed=11) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=12)
        b = synth_fingerprint(head + 50, seed=21) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=22)
        return a, b

    def test_too_short_intro_is_rejected(self):
        # 10s intro < 15s minimum → detect_series_intros_sync records nothing
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        intro_pts = int(10 / intro_detect.POINT_SECONDS)
        fps = {
            1: synth_fingerprint(20, seed=5) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=6),
            2: synth_fingerprint(40, seed=7) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=8),
        }
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end, window: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(results, {})

    def test_intro_starting_late_is_rejected(self):
        # Intro starting at >90% of the fingerprint window → rejected
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        # window = min(1200 * 0.25, 600) = 300s = 2343 points
        intro_pts = int(60 / intro_detect.POINT_SECONDS)
        start_pts = int(290 / intro_detect.POINT_SECONDS)  # 290s of a 300s window
        fps = {
            1: synth_fingerprint(start_pts, seed=5) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=6),
            2: synth_fingerprint(start_pts + 30, seed=7) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=8),
        }
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end, window: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(results, {})

    def test_healthy_intro_detected_for_both_episodes(self):
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        intro_pts = int(60 / intro_detect.POINT_SECONDS)  # 60s — valid
        a_start = int(45 / intro_detect.POINT_SECONDS)
        fps = {
            1: synth_fingerprint(a_start, seed=5) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=6),
            2: synth_fingerprint(a_start + 25, seed=7) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=8),
        }
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end, window: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(set(results), {1, 2})
        s1, e1 = results[1]
        self.assertAlmostEqual(e1 - s1, 60, delta=1.5)
        self.assertAlmostEqual(s1, 45.0, delta=1.5)


class ManualPrecedenceTest(unittest.TestCase):
    def test_manual_edit_is_never_overwritten(self):
        eps = [
            make_item(1, episode=1, intro_source="manual"),
            make_item(2, episode=2),
        ]
        # Manual item: give it an existing admin-set intro
        eps[0].intro_start = 10.0
        eps[0].intro_end = 90.0
        intro_pts = int(60 / intro_detect.POINT_SECONDS)
        fps = {
            # Manual episode is fingerprinted too (it anchors sibling matches)
            1: synth_fingerprint(50, seed=5) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=6),
            2: synth_fingerprint(75, seed=7) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=8),
        }
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end, window: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertNotIn(1, results)  # manual episode untouched
        self.assertEqual(eps[0].intro_start, 10.0)
        self.assertEqual(eps[0].intro_end, 90.0)
        self.assertIn(2, results)


class NeedsProbeLoopSafetyTest(unittest.TestCase):
    def test_fingerprint_failure_does_not_crash_sweep(self):
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        with patch.object(
            intro_detect, "_fingerprint_sync",
            side_effect=RuntimeError("fpcalc missing"),
        ):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(results, {})


class SeriesBucketTest(unittest.TestCase):
    def test_single_episode_series_excluded(self):
        items = [make_item(1, episode=1)]
        with patch.object(media_index_mod := __import__("main.utils.media_index", fromlist=["_items"]), "_items", {1: items[0]}):
            buckets = intro_detect._series_with_multiple_episodes()
        self.assertEqual(buckets, {})


class SweepIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """End-to-end: seeded catalogue → detect_all_intros → persisted bounds."""

    def setUp(self):
        self._previous_items = dict(media_index._items)

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self._previous_items)
        intro_detect.intro_state.update(running=False, done=0, total=0, series_done=0, intros_found=0, finished_at=0.0)

    async def test_sweep_persists_intro_bounds(self):
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        intro_pts = int(60 / intro_detect.POINT_SECONDS)
        fps = {
            1: synth_fingerprint(45 * 8, seed=5) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=6),
            2: synth_fingerprint(45 * 8 + 25, seed=7) + synth_fingerprint(intro_pts, seed=99) + synth_fingerprint(100, seed=8),
        }
        media_index._items.clear()
        media_index._items.update({1: eps[0], 2: eps[1]})
        with (
            patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]),
            patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end, window: end),
            patch.object(media_index, "_store_active", return_value=False),
            patch.object(media_index, "_persist_unlocked", lambda: None),
        ):
            summary = await intro_detect.detect_all_intros()

        self.assertEqual(summary["intros_found"], 2)
        item1 = media_index.get_item(1)
        self.assertEqual(item1.intro_source, "auto")
        self.assertAlmostEqual(item1.intro_end - item1.intro_start, 60, delta=1.5)
        self.assertGreater(intro_detect.state()["finished_at"], 0)


if __name__ == "__main__":
    unittest.main()
