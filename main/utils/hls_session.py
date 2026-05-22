"""
Long-lived ffmpeg-per-stream HLS sessions.

Replaces the per-segment ffmpeg pattern. One ffmpeg subprocess per file
produces all segments sequentially to /tmp via ``-f segment
-reset_timestamps 1``, and route handlers just send the .ts files from
disk. The big wins:

  * No per-segment startup cost — sequential playback is smooth.
  * Each output segment's PTS is reset to 0 (`-reset_timestamps 1`), so
    there are no PTS gaps/overlaps between segments. That's what was
    causing the frame-replay stutter at segment boundaries.
  * Backward seek within already-produced segments is free (file is on
    disk). Forward-seek beyond the current production cursor restarts
    ffmpeg from the seek point.

Constraints we accept:
  * Disk pressure: one session ≈ one source-file's worth of bytes on
    /tmp. Idle sessions are reaped after IDLE_TTL. Concurrent sessions
    are capped via MAX_SESSIONS (LRU eviction).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from main.utils import hls as hls_module


SEGMENT_SECONDS = hls_module.SEGMENT_SECONDS
WORK_ROOT = Path(os.environ.get("HLS_SESSION_ROOT", "/tmp/hls"))
IDLE_TTL = int(os.environ.get("HLS_SESSION_IDLE_TTL", str(30 * 60)))
# Sessions are now keyed on (message_id, audio_index), so a file with N
# audio tracks can consume up to N slots. Raise the default from 2 → 6 so
# a typical dual-audio (Tamil/English) file still has room for 3 concurrent
# viewers. Override with HLS_SESSION_MAX env var.
MAX_SESSIONS = int(os.environ.get("HLS_SESSION_MAX", "6"))
# If a requested segment is more than this many segments ahead of what
# ffmpeg has produced, restart ffmpeg from the requested segment instead
# of waiting (which would otherwise mean polling forever).
RESTART_AHEAD_THRESHOLD = int(os.environ.get("HLS_SESSION_AHEAD_THRESH", "20"))
SEGMENT_WAIT_TIMEOUT = float(os.environ.get("HLS_SESSION_WAIT_TIMEOUT", "30.0"))


class HlsSession:
    """One long-lived ffmpeg instance producing HLS segments for a single
    source file / audio track combination to disk."""

    def __init__(self, message_id: int, source_url: str,
                 duration: float, audio_codec: Optional[str],
                 audio_index: int = 0):
        self.message_id = message_id
        self.source_url = source_url
        self.duration = duration
        self.audio_codec = audio_codec
        self.audio_index = audio_index
        self.work_dir = WORK_ROOT / str(message_id) / f"a{audio_index}"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.proc_lock = asyncio.Lock()
        self.last_request = time.monotonic()
        # Segment number ffmpeg is currently producing FROM. Used to detect
        # backward-seek (need restart from the earlier point).
        self.start_segment = 0
        # Captured stderr from the most recent ffmpeg run, useful for logs.
        self._stderr_task: Optional[asyncio.Task] = None

    # -- file helpers --------------------------------------------------

    def segment_path(self, n: int) -> Path:
        return self.work_dir / f"{n:05d}.ts"

    def latest_produced(self) -> int:
        """Highest segment number currently on disk (-1 if none)."""
        try:
            files = list(self.work_dir.glob("*.ts"))
        except OSError:
            return -1
        latest = -1
        for f in files:
            try:
                n = int(f.stem)
                if n > latest:
                    latest = n
            except ValueError:
                continue
        return latest

    # -- ffmpeg control ------------------------------------------------

    def _ffmpeg_args(self, from_segment: int) -> list[str]:
        start_sec = from_segment * SEGMENT_SECONDS
        if self.audio_codec in hls_module.BROWSER_AUDIO_OK:
            audio_args = ["-c:a", "copy"]
        else:
            audio_args = ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
        return [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            # Regenerate PTS from DTS on the input so B-frame-heavy
            # sources don't surface non-monotonic timestamps to the
            # browser's MSE pipeline (which raises
            # CHUNK_DEMUXER_ERROR_APPEND_FAILED and stalls playback).
            # +discardcorrupt drops frames with broken timestamps rather
            # than emitting them and breaking the segment.
            "-fflags", "+genpts+discardcorrupt",
            "-ss", f"{start_sec:.3f}",
            "-i", self.source_url,
            "-map", "0:v:0?",
            "-map", f"0:a:{self.audio_index}?",
            "-c:v", "copy",
            *audio_args,
            "-f", "segment",
            "-segment_time", str(SEGMENT_SECONDS),
            "-segment_format", "mpegts",
            "-segment_start_number", str(from_segment),
            # Each segment's PTS resets to 0 — gives hls.js clean per-segment
            # timelines that it stitches via EXTINF, no PTS overlap-handling
            # quirks. This is the fix for the boundary frame-replay artifact.
            "-reset_timestamps", "1",
            "-avoid_negative_ts", "make_zero",
            str(self.work_dir / "%05d.ts"),
        ]

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                logging.debug("ffmpeg(msg=%d): %s", self.message_id,
                              line.decode(errors="replace").rstrip())
        except Exception:
            pass

    async def _kill_proc_unlocked(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()
            except ProcessLookupError:
                pass
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
        self.proc = None
        self._stderr_task = None

    async def _start_unlocked(self, from_segment: int) -> None:
        await self._kill_proc_unlocked()
        args = self._ffmpeg_args(from_segment)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logging.error("ffmpeg not installed; HLS sessions disabled")
            self.proc = None
            return
        self.start_segment = from_segment
        self._stderr_task = asyncio.create_task(self._drain_stderr(self.proc))
        logging.info(
            "hls_session msg=%d started ffmpeg from segment %d",
            self.message_id, from_segment,
        )

    # -- public API ----------------------------------------------------

    async def request(self, segment_n: int,
                      timeout: float = SEGMENT_WAIT_TIMEOUT) -> Optional[Path]:
        """Return the on-disk path of segment_n, waiting for ffmpeg to
        produce it if needed. Restarts ffmpeg if the segment is too far
        ahead of the current production cursor."""
        self.last_request = time.monotonic()
        path = self.segment_path(segment_n)
        if path.exists() and path.stat().st_size > 0:
            return path

        async with self.proc_lock:
            # Re-check after acquiring the lock.
            if path.exists() and path.stat().st_size > 0:
                return path

            need_restart = False
            if self.proc is None or self.proc.returncode is not None:
                need_restart = True
            else:
                latest = self.latest_produced()
                if segment_n > latest + RESTART_AHEAD_THRESHOLD:
                    need_restart = True
                elif segment_n < self.start_segment:
                    # User seeked backward to a segment we never produced
                    # in this ffmpeg run.
                    need_restart = True

            if need_restart:
                await self._start_unlocked(segment_n)

        # Poll for the file outside the lock so concurrent requests for
        # different segments don't serialize behind this one.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if path.exists() and path.stat().st_size > 0:
                # Tiny grace period for ffmpeg to finish flushing.
                await asyncio.sleep(0.05)
                return path
            await asyncio.sleep(0.25)
        return None

    async def stop(self) -> None:
        async with self.proc_lock:
            await self._kill_proc_unlocked()

    def cleanup_disk(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)
        # Remove the parent message-id dir if it's now empty (i.e. all
        # audio-track sub-dirs have been cleaned up). rmdir is a no-op when
        # sibling aN dirs still exist, so this is always safe to attempt.
        try:
            self.work_dir.parent.rmdir()
        except OSError:
            pass


# --- Registry of active sessions -------------------------------------

# Key is (message_id, audio_index) so each audio track gets its own session.
_sessions: Dict[Tuple[int, int], HlsSession] = {}
_sessions_lock = asyncio.Lock()
_reaper_task: Optional[asyncio.Task] = None


async def get_or_start(message_id: int, source_url: str,
                       duration: float, audio_codec: Optional[str],
                       audio_index: int = 0) -> HlsSession:
    key = (message_id, audio_index)
    async with _sessions_lock:
        session = _sessions.get(key)
        if session is not None:
            return session

        # LRU evict if over capacity.
        if len(_sessions) >= MAX_SESSIONS:
            victim_key = min(_sessions, key=lambda k: _sessions[k].last_request)
            victim = _sessions.pop(victim_key)
            asyncio.create_task(_retire(victim))
            logging.info("hls_session evicted msg=%d audio=%d (capacity)",
                         victim_key[0], victim_key[1])

        session = HlsSession(message_id, source_url, duration, audio_codec,
                             audio_index=audio_index)
        _sessions[key] = session
        return session


async def _retire(session: HlsSession) -> None:
    await session.stop()
    session.cleanup_disk()


async def _reaper() -> None:
    while True:
        try:
            await asyncio.sleep(300)
            now = time.monotonic()
            to_evict = []
            async with _sessions_lock:
                for key, sess in list(_sessions.items()):
                    if now - sess.last_request > IDLE_TTL:
                        to_evict.append(key)
                for key in to_evict:
                    sess = _sessions.pop(key, None)
                    if sess is not None:
                        asyncio.create_task(_retire(sess))
                        logging.info("hls_session evicted msg=%d audio=%d (idle)",
                                     key[0], key[1])
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("hls_session reaper error")


def ensure_reaper_running() -> None:
    """Idempotent — call once at startup."""
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper())


async def shutdown_all() -> None:
    async with _sessions_lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    await asyncio.gather(*(_retire(s) for s in sessions), return_exceptions=True)
