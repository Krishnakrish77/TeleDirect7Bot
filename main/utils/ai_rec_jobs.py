"""Bounded in-process job runner for AI recommendation generation.

Ask/Refresh spend up to ~25s per run inside the Gemini agent. Running them
inline on the HTTP request couples the user's browse session to that latency
and turns any budget overrun into a visible 504. Instead, a POST enqueues a
job and returns its id at once; the panel subscribes to
``/api/app/ai/recommendations/jobs/{id}/stream`` and renders when the job
completes — the user keeps browsing meanwhile.

Single process, in memory, by design: mirrors the rate limiter in
ai_rec_routes. Move to a shared store only if the deploy goes multi-instance.
Jobs are serialized per user (one running, one queued max) so a second Ask
can never double-spend Gemini quota. Finished jobs are reaped after a short
TTL; the per-process cap bounds memory if a client streams and vanishes.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

# A job may take the full agent budget (25s) plus fallback headroom; give the
# worker 4 minutes before declaring it lost, then keep the result around for
# late subscribers.
JOB_TIMEOUT_SECONDS = 240.0
JOB_RESULT_TTL_SECONDS = 600.0
MAX_TRACKED_JOBS = 200
MAX_QUEUED_PER_USER = 1

JobStatus = str  # "queued" | "running" | "done" | "failed"


class RecommendationJob:
    __slots__ = ("job_id", "user_id", "status", "result", "error", "queued_at", "_event")

    def __init__(self, job_id: str, user_id: int):
        self.job_id = job_id
        self.user_id = user_id
        self.status: JobStatus = "queued"
        self.result: dict | None = None
        self.error: dict | None = None
        self.queued_at = time.monotonic()
        # Set when status leaves queued/running; late subscribers await it.
        self._event = asyncio.Event()

    def snapshot(self) -> dict:
        payload: dict[str, Any] = {"jobId": self.job_id, "status": self.status}
        if self.status == "done":
            payload["result"] = self.result
        if self.status == "failed":
            payload["error"] = self.error
        return payload

    async def wait(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._event.wait(), timeout=timeout)


class JobRunner:
    def __init__(self) -> None:
        self._jobs: dict[str, RecommendationJob] = {}
        self._queues: dict[int, asyncio.Queue] = {}
        self._workers: dict[int, asyncio.Task] = {}
        self._execute: Callable[[RecommendationJob, Callable[[str], Awaitable[None]]], Awaitable[dict]] | None = None

    def bind(self, execute: Callable[[RecommendationJob, Callable[[str], Awaitable[None]]], Awaitable[dict]]) -> None:
        """Install the generation coroutine: ``(job, progress) -> result dict``."""
        self._execute = execute

    def _reap(self) -> None:
        now = time.monotonic()
        stale = [
            job_id for job_id, job in self._jobs.items()
            if job.status in {"done", "failed"} and now - job.queued_at > JOB_RESULT_TTL_SECONDS
        ]
        for job_id in stale:
            del self._jobs[job_id]
        # Hard cap: drop oldest terminal jobs when a vanished client leaked.
        if len(self._jobs) > MAX_TRACKED_JOBS:
            terminal = sorted(
                (job for job in self._jobs.values() if job.status in {"done", "failed"}),
                key=lambda job: job.queued_at,
            )
            for job in terminal[: len(self._jobs) - MAX_TRACKED_JOBS]:
                self._jobs.pop(job.job_id, None)

    def enqueue(self, user_id: int) -> RecommendationJob | None:
        """Create a job, queued behind at most one pending run for this user."""
        if self._execute is None:
            raise RuntimeError("JobRunner used before bind()")
        self._reap()
        pending = [
            job for job in self._jobs.values()
            if job.user_id == user_id and job.status in {"queued", "running"}
        ]
        if len(pending) >= MAX_QUEUED_PER_USER + 1:
            return None
        job = RecommendationJob(f"airec-{uuid.uuid4().hex[:12]}", user_id)
        self._jobs[job.job_id] = job
        queue = self._queues.setdefault(user_id, asyncio.Queue())
        queue.put_nowait(job)
        worker = self._workers.get(user_id)
        if worker is None or worker.done():
            self._workers[user_id] = asyncio.create_task(self._run_worker(user_id))
        return job

    def get(self, job_id: str) -> RecommendationJob | None:
        return self._jobs.get(job_id)

    async def _run_worker(self, user_id: int) -> None:
        queue = self._queues[user_id]
        while not queue.empty():
            job = await queue.get()
            job.status = "running"

            async def progress(status: str) -> None:
                logging.debug("airec_job %s: %s", job.job_id, status)

            try:
                job.result = await asyncio.wait_for(
                    self._execute(job, progress),  # type: ignore[misc]
                    timeout=JOB_TIMEOUT_SECONDS,
                )
                job.status = "done"
            except asyncio.TimeoutError:
                logging.warning("airec_job %s: timed out after %.0fs", job.job_id, JOB_TIMEOUT_SECONDS)
                job.status = "failed"
                job.error = {"message": "This one took too long — try again.", "retryable": True, "status": 504}
            except Exception:
                logging.exception("airec_job %s: generation failed", job.job_id)
                job.status = "failed"
                job.error = {"message": "Could not process that request. Please try again.", "retryable": True, "status": 502}
            finally:
                job._event.set()
                queue.task_done()


runner = JobRunner()
