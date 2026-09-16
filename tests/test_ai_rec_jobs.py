"""Tests for the in-process AI recommendation job runner and its routes."""

import asyncio
import importlib
import json
import os
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import ai_rec, ai_rec_jobs

ai_rec_routes = importlib.import_module("main.server.ai_rec_routes")


class _StreamResponse:
    """Minimal stand-in recording streamed SSE frames."""

    def __init__(self, *args, **kwargs):
        self.frames = []

    async def prepare(self, request):
        return self

    async def write(self, payload):
        self.frames.append(payload.decode("utf-8"))

    async def write_eof(self):
        pass


class _Request:
    def __init__(self, *, body=None, job_id=""):
        self._body = body or {}
        self.match_info = {"job_id": job_id}

    async def json(self):
        return self._body


def _events(response):
    return [frame for frame in response.frames]


def _runner():
    """A fresh runner per test — the module-level one holds worker tasks."""
    return ai_rec_jobs.JobRunner()


class JobRunnerTest(__import__("unittest").IsolatedAsyncioTestCase):
    async def test_enqueue_runs_job_and_lands_result(self):
        runner = _runner()
        calls = []

        async def execute(job, progress):
            calls.append(job.job_id)
            await progress("Searching")
            return {"items": [{"href": "/one"}], "message": ""}

        runner.bind(execute)
        job = runner.enqueue(7)
        self.assertIsNotNone(job)
        await job.wait(timeout=2)
        self.assertEqual(job.status, "done")
        self.assertEqual(job.result, {"items": [{"href": "/one"}], "message": ""})
        self.assertEqual(calls, [job.job_id])
        self.assertEqual(runner.get(job.job_id), job)

    async def test_job_failure_is_captured_not_raised(self):
        runner = _runner()

        async def execute(job, progress):
            raise RuntimeError("gemini exploded")

        runner.bind(execute)
        job = runner.enqueue(7)
        await job.wait(timeout=2)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error["status"], 502)
        self.assertTrue(job.error["retryable"])

    async def test_jobs_serialize_per_user(self):
        runner = _runner()
        active = 0
        max_active = 0

        async def execute(job, progress):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1

        runner.bind(execute)
        first = runner.enqueue(7)
        second = runner.enqueue(7)
        await first.wait(timeout=2)
        await second.wait(timeout=2)
        self.assertEqual(max_active, 1)  # never overlapped

    async def test_second_queued_job_beyond_limit_is_rejected(self):
        runner = _runner()

        async def execute(job, progress):
            await asyncio.sleep(10)

        runner.bind(execute)
        first = runner.enqueue(7)
        second = runner.enqueue(7)  # queued behind first — allowed
        self.assertIsNotNone(second)
        # One running + one queued = cap; a third is rejected.
        self.assertIsNone(runner.enqueue(7))
        first._event.set()
        # Let the worker observe the (already set) event and clean up.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_queued_job_times_out_at_job_timeout(self):
        runner = _runner()

        async def execute(job, progress):
            await asyncio.sleep(60)

        runner.bind(execute)
        job = runner.enqueue(7)
        # Shrink the wait: patch the event wait by completing through timeout path
        with patch.object(ai_rec_jobs, "JOB_TIMEOUT_SECONDS", 0.05):
            # worker already captured the constant? wait_for uses module constant
            # at call time through the module attribute; patching module attr works
            await job.wait(timeout=2)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error["status"], 504)

    async def test_reaper_drops_terminal_jobs_after_ttl(self):
        runner = _runner()

        async def execute(job, progress):
            return {"items": []}

        runner.bind(execute)
        job = runner.enqueue(7)
        await job.wait(timeout=2)
        with patch.object(ai_rec_jobs, "JOB_RESULT_TTL_SECONDS", -1):
            runner.enqueue(8)
        self.assertIsNone(runner.get(job.job_id))


class JobRoutesTest(__import__("unittest").IsolatedAsyncioTestCase):
    async def test_submit_returns_job_id_without_running_inline(self):
        job = ai_rec_jobs.RecommendationJob("airec-test", 7)
        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_routes, "_take_token", return_value=True) as take_token,
            patch.object(ai_rec_jobs.runner, "enqueue", return_value=job) as enqueue,
            patch.object(ai_rec, "get_ai_recommendations", new=AsyncMock()) as recommendations,
        ):
            response = await ai_rec_routes.ai_recommendations_job_submit(
                _Request(body={"query": "funny"}),
            )
        self.assertEqual(json.loads(response.text), {"jobId": "airec-test", "status": "queued"})
        take_token.assert_called_once_with(7)
        enqueue.assert_called_once_with(7)
        recommendations.assert_not_awaited()

    async def test_submit_requires_query_or_refresh(self):
        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
        ):
            response = await ai_rec_routes.ai_recommendations_job_submit(_Request(body={}))
        self.assertEqual(response.status, 400)

    async def test_submit_rate_limited_before_enqueue(self):
        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_routes, "_take_token", return_value=False),
            patch.object(ai_rec_jobs.runner, "enqueue") as enqueue,
        ):
            response = await ai_rec_routes.ai_recommendations_job_submit(
                _Request(body={"refresh": True}),
            )
        self.assertEqual(response.status, 429)
        enqueue.assert_not_called()

    async def test_stream_delivers_result_when_job_completes(self):
        job = ai_rec_jobs.RecommendationJob("airec-live", 7)
        job.status = "done"
        job.result = {"items": [{"href": "/landed"}], "message": ""}

        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_routes.web, "StreamResponse", _StreamResponse),
            patch.object(ai_rec_jobs.runner, "get", return_value=job),
        ):
            response = await ai_rec_routes.ai_recommendations_job_stream(
                _Request(job_id="airec-live"),
            )
        self.assertIn("event: result", "".join(response.frames))
        self.assertIn("/landed", "".join(response.frames))

    async def test_stream_waits_for_running_job(self):
        job = ai_rec_jobs.RecommendationJob("airec-running", 7)
        job.status = "running"

        async def land():
            await asyncio.sleep(0.01)
            job.status = "done"
            job.result = {"items": [], "message": ""}
            job._event.set()

        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_routes.web, "StreamResponse", _StreamResponse),
            patch.object(ai_rec_jobs.runner, "get", return_value=job),
        ):
            task = __import__("asyncio").get_running_loop().create_task(land())
            response = await ai_rec_routes.ai_recommendations_job_stream(
                _Request(job_id="airec-running"),
            )
            await task
        self.assertIn("event: result", "".join(response.frames))

    async def test_stream_rejects_other_users_jobs(self):
        job = ai_rec_jobs.RecommendationJob("airec-other", 99)
        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_jobs.runner, "get", return_value=job),
        ):
            response = await ai_rec_routes.ai_recommendations_job_stream(
                _Request(job_id="airec-other"),
            )
        self.assertEqual(response.status, 404)

    async def test_stream_unknown_job_is_404(self):
        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_jobs.runner, "get", return_value=None),
        ):
            response = await ai_rec_routes.ai_recommendations_job_stream(
                _Request(job_id="missing"),
            )
        self.assertEqual(response.status, 404)

    async def test_stream_relays_job_failure_as_error_event(self):
        job = ai_rec_jobs.RecommendationJob("airec-fail", 7)
        job.status = "failed"
        job.error = {"message": "This one took too long — try again.", "retryable": True, "status": 504}

        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_routes.web, "StreamResponse", _StreamResponse),
            patch.object(ai_rec_jobs.runner, "get", return_value=job),
        ):
            response = await ai_rec_routes.ai_recommendations_job_stream(
                _Request(job_id="airec-fail"),
            )
        self.assertIn("event: error", "".join(response.frames))
        self.assertIn("504", "".join(response.frames))
