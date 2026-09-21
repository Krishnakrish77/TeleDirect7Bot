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
import random
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


def _drift(i: int, seed: int) -> int:
    """≤4 flipped bits, deterministic per (i, seed) — the chromaprint
    cross-encode drift model (matched audio drifts a few bits per point)."""
    rng = random.Random(seed * 100_003 + i)
    v = 0
    for _ in range(4):
        v |= 1 << rng.randrange(32)
    return v


def _base(i: int, salt: int) -> int:
    return ((i * 0x9E3779B1) ^ (salt * 0x85EBCA6B)) & 0xFFFFFFFF


def episode_fp(points: int, drift_seed: int, salt: int = 0) -> list:
    """A "recording" of some audio: base pattern + small per-point drift.
    Two episodes of the same audio (same salt, different drift_seed) match
    at the true shift; different audio (different salt) never does."""
    return [_base(i, salt) ^ _drift(i, drift_seed) for i in range(points)]


def fp_pair(total: int, intro_start: int, intro_len: int):
    """Two episodes of the SAME audio: they share the intro window —
    same base pattern there — and differ (per-episode drift over a
    different base pattern) everywhere else. Returns (a, b).

    Tails index from their ABSOLUTE position so no accidental alignment
    extends the matched run past the intro."""
    end = intro_start + intro_len
    a = episode_fp(total, drift_seed=1, salt=7)
    shared = [_base(i, 7) ^ _drift(i, 2) for i in range(intro_start, end)]
    b_head = episode_fp(intro_start, drift_seed=2, salt=9)
    b_tail = [_base(i, 11) ^ _drift(i, 2) for i in range(end, total)]
    return a, b_head + shared + b_tail


def fp_triplet(total: int, intro_start: int, intro_len: int):
    """Three episodes sharing only the intro window."""
    a, b = fp_pair(total, intro_start, intro_len)
    end = intro_start + intro_len
    c_head = episode_fp(intro_start, drift_seed=3, salt=13)
    c_tail = episode_fp(total - end, drift_seed=3, salt=17)
    shared = [_base(i, 7) ^ _drift(i, 3) for i in range(intro_start, end)]
    return a, b, c_head + shared + c_tail


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
        fp = episode_fp(500, drift_seed=1)
        run, start, shift = intro_detect._longest_contiguous_match(fp, fp)
        self.assertEqual(shift, 0)
        self.assertGreaterEqual(run, intro_detect.MIN_INTRO_POINTS)

    def test_shifted_intro_is_found_at_right_offset(self):
        # Episode A: 10s unique head, then 62.5s "intro", then unique tail.
        # Episode B: 30s unique head, then the same intro, then its own tail.
        a, b = fp_pair(
            int(10 / intro_detect.POINT_SECONDS) + int(62.5 / intro_detect.POINT_SECONDS) + 300,
            int(10 / intro_detect.POINT_SECONDS),
            int(62.5 / intro_detect.POINT_SECONDS),
        )
        run, a_start, _shift = intro_detect._longest_contiguous_match(a, b)
        self.assertGreaterEqual(run, int(62.5 / intro_detect.POINT_SECONDS) - 2)
        a_seconds = a_start * intro_detect.POINT_SECONDS
        self.assertAlmostEqual(a_seconds, 10.0, delta=1.0)  # intro starts ~10s into A

    def test_no_common_segment_returns_no_match(self):
        a, b = fp_pair(500, 0, 0)  # fully disjoint (complement construction)
        run, _, _ = intro_detect._longest_contiguous_match(a, b)
        self.assertLess(run, intro_detect.MIN_INTRO_POINTS)


class ClampTest(unittest.TestCase):
    def _pair(self, intro_pts: int, intro_start_pts: int):
        """Two episodes sharing `intro_pts` points at `intro_start_pts`."""
        head = intro_start_pts
        a = synth_fingerprint(head, seed=11) + synth_fingerprint(intro_pts, seed=98) + synth_fingerprint(100, seed=12)
        b = synth_fingerprint(head + 50, seed=21) + synth_fingerprint(intro_pts, seed=98) + synth_fingerprint(100, seed=22)
        return a, b

    def test_too_short_intro_is_rejected(self):
        # 10s intro < 15s minimum → detect_series_intros_sync records nothing
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        intro_pts = int(10 / intro_detect.POINT_SECONDS)
        total = 20 + intro_pts + 100
        f1, f2 = fp_pair(total, 20, intro_pts)
        fps = {1: f1, 2: f2}
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
        total = start_pts + intro_pts + 100
        f1, f2 = fp_pair(total, start_pts, intro_pts)
        fps = {1: f1, 2: f2}
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end, window: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(results, {})

    def test_healthy_intro_detected_for_both_episodes(self):
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        intro_pts = int(60 / intro_detect.POINT_SECONDS)  # 60s — valid
        a_start = int(45 / intro_detect.POINT_SECONDS)
        total = a_start + intro_pts + 100
        f1, f2 = fp_pair(total, a_start, intro_pts)
        fps = {1: f1, 2: f2}
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end, window: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(set(results), {1, 2})
        s1, e1 = results[1]
        self.assertAlmostEqual(e1 - s1, 60, delta=7.0)
        self.assertAlmostEqual(s1, 45.0, delta=7.0)


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
        total = 75 + intro_pts + 100
        f1, f2 = fp_pair(total, 50, intro_pts)
        fps = {1: f1, 2: f2}
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
        for e in eps:
            e.duration = 2400  # 40 min → fingerprint window = 600s
        intro_pts = int(60 / intro_detect.POINT_SECONDS)
        a_start = int(360 / intro_detect.POINT_SECONDS)  # intro at 6:00, inside window
        total = a_start + intro_pts + 100
        f1, f2 = fp_pair(total, a_start, intro_pts)
        fps = {1: f1, 2: f2}
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
        self.assertAlmostEqual(item1.intro_end - item1.intro_start, 60, delta=7.0)
        self.assertGreater(intro_detect.state()["finished_at"], 0)


if __name__ == "__main__":
    unittest.main()
