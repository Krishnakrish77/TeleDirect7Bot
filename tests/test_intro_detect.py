"""Intro detection tests — synthetic fingerprints, no network, no fpcalc.

Covers the contracts that matter for the Skip intro button:
  - fpcalc ``-raw`` payload parsing (decimal uint32 CSV; compressed form rejected)
  - cross-episode matching (shift handling, ±1 tolerance, longest run)
  - clamp validation (15–150 s, window position)
  - silence-snap window around the matched intro end
  - manual-edit precedence (intro_source == "manual" never overwritten)
  - sweep bookkeeping (state transitions, persist calls)
"""
import asyncio
import os
import random
import unittest
from types import SimpleNamespace
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
    # Real fpcalc output for the same 4 s of audio, both formats.
    RAW_PAYLOAD = ",".join(["2238577015"] * 11)
    COMPRESSED_PAYLOAD = "AQAAC0mUaEkSRZEKAAAAAA"

    def test_raw_csv_payload_parses_to_uint32_points(self):
        self.assertEqual(
            intro_detect._decode_fingerprint(self.RAW_PAYLOAD),
            [2238577015] * 11,
        )

    def test_compressed_payload_is_rejected(self):
        # fpcalc's default (non -raw) output is a delta/bit-packed encoding.
        # Treating it as a uint32 array silently produces ~40% fewer,
        # meaningless points and every reported timing is wrong — it must
        # never be accepted as a fingerprint.
        with self.assertRaises(ValueError):
            intro_detect._decode_fingerprint(self.COMPRESSED_PAYLOAD)

    def test_garbage_input_raises(self):
        for bad in ("", "   ", "!!!!not-a-fingerprint!!!!", "1,,2", "1, 2", "-5,3"):
            with self.assertRaises(ValueError):
                intro_detect._decode_fingerprint(bad)


class FingerprintPipelineTest(unittest.TestCase):
    """The fpcalc invocation and the decoder must agree on one format.

    Regression: the detector ran fpcalc *without* ``-raw`` while decoding the
    payload as a uint32 array, so real runs matched compressed bitstream words
    and produced garbage timings (usually below the 15 s floor → no intro at
    all). The stub below emulates fpcalc faithfully: it emits the compressed
    payload unless ``-raw`` is requested.
    """

    RAW_PAYLOAD = ",".join(str(1000 + i) for i in range(40))
    COMPRESSED_PAYLOAD = "AQAAC0mUaEkSRZEKAAAAAA"

    def _stub_fpcalc(self, cmd, **_kwargs):
        class R:
            returncode = 0
            stdout = (
                f"FINGERPRINT={self.RAW_PAYLOAD}"
                if "-raw" in cmd else f"FINGERPRINT={self.COMPRESSED_PAYLOAD}"
            ).encode()

        return R()

    def test_fingerprint_sync_requests_and_parses_raw_points(self):
        item = make_item(7, episode=1)
        with (
            patch.object(intro_detect.subprocess, "run", side_effect=self._stub_fpcalc),
            patch.object(media_index, "_store_active", return_value=False),
        ):
            points = intro_detect._fingerprint_sync(item)
        self.assertEqual(points, [1000 + i for i in range(40)])


class SnapEndToSilenceTest(unittest.TestCase):
    """The silence snap may only accept a silence inside its scan window.

    Regression: the previous version accepted anything within ±(-8, +4) s of
    the matched end, so a quiet beat inside the following dialogue could push
    the intro end into the episode. Jellyfin's window is [end-5, end+2].
    """

    END = 60.0  # matched intro end → scan starts at 55 s

    def _scan(self, *relative_offsets):
        out = "".join(
            f"[silencedetect @ 0x1] silence_start: {t}\n"
            f"[silencedetect @ 0x1] silence_end: {t + 0.4} | silence_duration: 0.4\n"
            for t in relative_offsets
        )
        return patch.object(
            intro_detect.subprocess, "run",
            return_value=SimpleNamespace(stdout=out, returncode=0),
        )

    def test_silence_inside_window_snaps_to_it(self):
        with self._scan(4.0):  # 55 + 4 = 59 s, one second before the matched end
            self.assertAlmostEqual(
                intro_detect._snap_end_to_silence("http://127.0.0.1/x", self.END), 59.0)

    def test_silence_after_outward_window_is_ignored(self):
        with self._scan(9.0):  # 64 s — past end+2, would extend the intro
            self.assertAlmostEqual(
                intro_detect._snap_end_to_silence("http://127.0.0.1/x", self.END), self.END)

    def test_no_silence_keeps_the_matched_end(self):
        with self._scan():
            self.assertAlmostEqual(
                intro_detect._snap_end_to_silence("http://127.0.0.1/x", self.END), self.END)

    def test_scan_failure_keeps_the_matched_end(self):
        with patch.object(intro_detect.subprocess, "run", side_effect=RuntimeError("boom")):
            self.assertAlmostEqual(
                intro_detect._snap_end_to_silence("http://127.0.0.1/x", self.END), self.END)


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
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
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
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
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
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
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
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
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


class ConsensusTest(unittest.TestCase):
    """Regression: a single noisy pair must not stamp phantom intros.

    Real-library failure (Silicon Valley): with a lax threshold, the
    best-of-siblings run landed on contiguous noise and every episode got a
    ~32s 'intro' mid-dialogue. A detected run must be corroborated by a
    majority of siblings at a consistent position/length."""

    def test_three_episodes_share_intro_all_detected(self):
        eps = [make_item(1, episode=1), make_item(2, episode=2), make_item(3, episode=3)]
        intro_pts = int(60 / intro_detect.POINT_SECONDS)
        a_start = int(45 / intro_detect.POINT_SECONDS)
        f1, f2, f3 = fp_triplet(a_start + intro_pts + 300, a_start, intro_pts)
        fps = {1: f1, 2: f2, 3: f3}
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(set(results), {1, 2, 3})

    def test_one_noisy_pair_does_not_poison_sibling(self):
        # Episodes 1 & 2 share a real intro; episode 3 is unrelated audio.
        # At the 6-bit threshold ep3's cross-pairs produce no qualifying run,
        # so only eps 1 & 2 enter the consensus vote — and each finds the
        # other agreeing at the shared position. A phantom (longer, misplaced)
        # run on either episode would be rejected: no sibling corroborates it.
        eps = [make_item(1, episode=1), make_item(2, episode=2), make_item(3, episode=3)]
        intro_pts = int(60 / intro_detect.POINT_SECONDS)
        a_start = int(45 / intro_detect.POINT_SECONDS)
        total = a_start + intro_pts + 300
        f1, f2 = fp_pair(total, a_start, intro_pts)
        f3 = episode_fp(total, drift_seed=5, salt=777)  # fully unrelated
        fps = {1: f1, 2: f2, 3: f3}
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
            results = intro_detect.detect_series_intros_sync(eps)
        # The two related episodes still detect; the unrelated one finds nothing.
        self.assertEqual(set(results), {1, 2})
        s1, e1 = results[1]
        self.assertAlmostEqual(s1, 45.0, delta=7.0)

    def test_two_episodes_still_detect_without_consensus(self):
        # With only 2 episodes there is no majority to corroborate — the
        # shared intro must still be found (consensus gate is skipped).
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        intro_pts = int(60 / intro_detect.POINT_SECONDS)
        a_start = int(45 / intro_detect.POINT_SECONDS)
        f1, f2 = fp_pair(a_start + intro_pts + 300, a_start, intro_pts)
        fps = {1: f1, 2: f2}
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(set(results), {1, 2})


class VariedColdOpenTest(unittest.TestCase):
    """Regression: the same theme sits at a different absolute time in every
    episode, because cold opens differ in length.

    Real-library failure (#Love, 6 episodes): the shared ~40 s opening sits at
    35/64/80/98/127 s, so the old consensus compared 98.1 s against 64.3 s,
    35.0 s, … and rejected every episode — the per-series action reported
    "No recurring intro found across 6 episodes" although five of them shared
    the opening.
    """

    @staticmethod
    def _episode(total: int, start: int, length: int, seed: int) -> list:
        """Per-episode unique head/tail with the shared theme at ``start``.

        The theme's base pattern is indexed from the theme's own start so the
        same audio aligns under the right shift; the per-episode drift models
        cross-encode point differences.
        """
        head = [_base(k, 100 + seed) ^ _drift(k, seed) for k in range(start)]
        theme = [_base(k, 7) ^ _drift(k, seed) for k in range(length)]
        tail = [_base(k, 200 + seed) ^ _drift(k, seed) for k in range(total - start - length)]
        return head + theme + tail

    def test_theme_at_different_offsets_is_detected_for_every_episode(self):
        length = int(40 / intro_detect.POINT_SECONDS)
        starts = [int(s / intro_detect.POINT_SECONDS) for s in (98.0, 64.0, 35.0)]
        eps = [make_item(i + 1, episode=i + 1) for i in range(3)]
        total = max(starts) + length + 300
        fps = {i + 1: self._episode(total, starts[i], length, seed=i + 1) for i in range(3)}
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(set(results), {1, 2, 3})
        for i, start_pts in enumerate(starts):
            self.assertAlmostEqual(
                results[i + 1][0], start_pts * intro_detect.POINT_SECONDS, delta=7.0,
            )


class StaticAudioTest(unittest.TestCase):
    """Regression: a long stretch of identical fingerprint points is silence or
    a static lead-in, never shared audio.

    Real-library failure (#Love): every episode opens with ~19 s of static whose
    fingerprint is one repeated value, so unrelated episodes matched the whole
    stretch and it qualified as an intro (19 s > the 15 s minimum).
    """

    FROZEN = 0x12345678

    def _static_episode(self, total: int, frozen: int, seed: int) -> list:
        return [self.FROZEN] * frozen + [
            _base(k, 100 + seed) ^ _drift(k, seed) for k in range(total - frozen)
        ]

    def test_static_head_is_not_reported_as_an_intro(self):
        frozen = int(20 / intro_detect.POINT_SECONDS)
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        fps = {1: self._static_episode(1500, frozen, seed=1),
               2: self._static_episode(1500, frozen, seed=2)}
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(results, {})

    def test_static_head_does_not_hide_a_real_theme_behind_it(self):
        frozen = int(20 / intro_detect.POINT_SECONDS)
        length = int(40 / intro_detect.POINT_SECONDS)
        starts = [int(s / intro_detect.POINT_SECONDS) for s in (60.0, 90.0)]
        eps = [make_item(1, episode=1), make_item(2, episode=2)]
        total = max(starts) + length + 200

        def episode(start: int, seed: int) -> list:
            return (
                [self.FROZEN] * frozen
                + [_base(k, 100 + seed) ^ _drift(k, seed) for k in range(start - frozen)]
                + [_base(k, 7) ^ _drift(k, seed) for k in range(length)]
                + [_base(k, 200 + seed) ^ _drift(k, seed) for k in range(total - start - length)]
            )

        fps = {1: episode(starts[0], 1), 2: episode(starts[1], 2)}
        with patch.object(intro_detect, "_fingerprint_sync", side_effect=lambda it: fps[it.message_id]), \
             patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end):
            results = intro_detect.detect_series_intros_sync(eps)
        self.assertEqual(set(results), {1, 2})
        self.assertAlmostEqual(
            results[1][0], starts[0] * intro_detect.POINT_SECONDS, delta=7.0,
        )


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
            patch.object(intro_detect, "_snap_end_to_silence", side_effect=lambda url, end: end),
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
