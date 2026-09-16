"""Per-user AI recommendation endpoints (Gemini-backed, catalogue-grounded).

Both routes require a logged-in user and a configured GEMINI_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from aiohttp import web

from main.utils import ai_rec, ai_rec_jobs, gemini
from main.utils.user_auth import get_user

routes = web.RouteTableDef()

# Per-user token bucket over the Gemini-invoking actions (chat + refresh), so an
# authenticated user can't drain the API quota. ponytail: in-memory / per
# process — fine for the single-instance deploy; move to a shared store if this
# ever runs multi-instance.
_RATE_CAPACITY = 8.0
_RATE_REFILL_PER_SEC = 0.1  # ~1 token every 10s, burst of 8
_RATE_RETRY_AFTER = 10
_buckets: dict[int, tuple[float, float]] = {}


def _uid(request: web.Request) -> Optional[int]:
    user = get_user(request)
    if not user:
        return None
    try:
        return int(user["sub"])
    except (KeyError, TypeError, ValueError):
        return None


def _take_token(user_id: int) -> bool:
    now = time.monotonic()
    tokens, last = _buckets.get(user_id, (_RATE_CAPACITY, now))
    tokens = min(_RATE_CAPACITY, tokens + (now - last) * _RATE_REFILL_PER_SEC)
    if tokens < 1:
        _buckets[user_id] = (tokens, now)
        return False
    _buckets[user_id] = (tokens - 1, now)
    return True


def _rate_limited() -> web.Response:
    return web.json_response(
        {"error": "Too many requests — give the recommender a moment."},
        status=429,
        headers={"Retry-After": str(_RATE_RETRY_AFTER)},
    )


@routes.get("/api/app/ai/recommendations")
async def ai_recommendations(request: web.Request) -> web.Response:
    if not gemini.available():
        return web.json_response({"error": "AI recommendations are not enabled"}, status=404)
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    refresh = request.query.get("refresh") in ("1", "true", "yes")
    if not refresh:
        cached = await ai_rec.get_cached_ai_recommendations(uid)
        if cached:
            return web.json_response(cached)
    # A valid cache never consumes quota. Refreshes and cache misses make one
    # bounded agent run, so each actual Gemini request spends one token.
    if not _take_token(uid):
        return _rate_limited()
    result = await ai_rec.get_ai_recommendations(uid, refresh=refresh, agentic=True)
    return web.json_response(result)


@routes.post("/api/app/ai/recommendations")
async def ai_recommendations_chat(request: web.Request) -> web.Response:
    if not gemini.available():
        return web.json_response({"error": "AI recommendations are not enabled"}, status=404)
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    if not _take_token(uid):  # every chat query calls Gemini (cache-bypassed)
        return _rate_limited()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):  # a JSON list/scalar would break body.get()
        body = {}
    query = (str(body.get("query") or "")).strip()[:300]
    result = await ai_rec.get_ai_recommendations(uid, query=query or None, agentic=True)
    return web.json_response(result)


async def _stream_event(response: web.StreamResponse, event: str, data: dict | str) -> None:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if isinstance(data, dict) else json.dumps(str(data))
    await response.write(f"event: {event}\ndata: {body}\n\n".encode("utf-8"))


@routes.post("/api/app/ai/recommendations/jobs")
async def ai_recommendations_job_submit(request: web.Request) -> web.Response:
    """Enqueue an Ask/Refresh generation; returns ``{jobId}`` immediately.

    The heavy Gemini work runs on the job worker, so the panel stays usable
    and a budget overrun degrades to a job failure instead of a 504.
    """
    if not gemini.available():
        return web.json_response({"error": "AI recommendations are not enabled"}, status=404)
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    query = str(body.get("query") or "").strip()[:300]
    refresh = body.get("refresh") is True and not query
    if not query and not refresh:
        return web.json_response({"error": "query or refresh is required"}, status=400)

    # Rate limit at submit time: a rejected enqueue consumes no quota and the
    # client gets the same friendly 429 as the inline path.
    if not _take_token(uid):
        return _rate_limited()
    job = ai_rec_jobs.runner.enqueue(uid)
    if job is None:
        return web.json_response(
            {"error": "You already have picks generating — one moment.", "retryable": True, "status": 429},
            status=429,
        )

    async def execute(job: ai_rec_jobs.RecommendationJob, progress) -> dict:
        return await ai_rec.get_ai_recommendations(
            uid, query=query or None, refresh=refresh, agentic=True, progress=progress,
        )

    ai_rec_jobs.runner.bind(execute)
    return web.json_response({"jobId": job.job_id, "status": job.status})


@routes.get("/api/app/ai/recommendations/jobs/{job_id}/stream")
async def ai_recommendations_job_stream(request: web.Request) -> web.StreamResponse:
    """SSE for one job: ``status`` events, then a terminal ``result``/``error``."""
    if not gemini.available():
        return web.json_response({"error": "AI recommendations are not enabled"}, status=404)
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    job = ai_rec_jobs.runner.get(request.match_info["job_id"])
    if job is None or job.user_id != uid:
        return web.json_response({"error": "job not found"}, status=404)

    response = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)

    # The simple, race-free shape: send the essential queued → running arc,
    # then the terminal snapshot when the job's event fires. Generation's
    # fine-grained stage statuses stay on the inline path.
    try:
        if job.status in {"queued", "running"}:
            await _stream_event(response, "status", {"message": "Finding your next watch"})
            await job.wait(timeout=ai_rec_jobs.JOB_TIMEOUT_SECONDS)
        if job.status == "done":
            await _stream_event(response, "result", job.result or {})
        elif job.status == "failed":
            await _stream_event(response, "error", job.error or {"message": "Could not process that request.", "retryable": True, "status": 502})
        else:
            await _stream_event(response, "error", {"message": "Job still pending — try again.", "retryable": True, "status": 504})
    except asyncio.TimeoutError:
        await _stream_event(response, "error", {"message": "This one took too long — try again.", "retryable": True, "status": 504})
    except (ConnectionResetError, asyncio.CancelledError):
        raise
    except Exception:
        await _stream_event(response, "error", {"message": "Could not process that request. Please try again.", "retryable": True, "status": 502})
    finally:
        try:
            await response.write_eof()
        except ConnectionResetError:
            pass
    return response


@routes.post("/api/app/ai/recommendations/stream")
async def ai_recommendations_stream(request: web.Request) -> web.StreamResponse:
    """SSE for a cache-first panel open or bounded Ask/Refresh agent."""
    if not gemini.available():
        return web.json_response({"error": "AI recommendations are not enabled"}, status=404)
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    query = str(body.get("query") or "").strip()[:300]
    refresh = body.get("refresh") is True and not query
    initial = body.get("initial") is True and not query and not refresh
    if not query and not refresh and not initial:
        return web.json_response({"error": "query, refresh, or initial is required"}, status=400)

    response = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)

    async def progress(status: str) -> None:
        await _stream_event(response, "status", {"message": status})

    try:
        if initial:
            cached = await ai_rec.get_cached_ai_recommendations(uid)
            if cached:
                await _stream_event(response, "result", cached)
                return response
        if not _take_token(uid):
            await _stream_event(response, "error", {"message": "Too many requests — give the recommender a moment.", "retryable": True, "status": 429})
            return response
        # get_ai_recommendations catches agent failures and returns its
        # deterministic shelf, so a successful stream never hangs on Gemini.
        result = await asyncio.wait_for(
            ai_rec.get_ai_recommendations(uid, query=query or None, refresh=refresh, agentic=True, progress=progress),
            timeout=28,
        )
        await _stream_event(response, "result", result)
    except asyncio.TimeoutError:
        await _stream_event(response, "error", {"message": "Recommendations took too long. Please try again.", "retryable": True, "status": 504})
    except (ConnectionResetError, asyncio.CancelledError):
        raise
    except Exception:
        # Do not surface implementation detail or catalogue/user content.
        await _stream_event(response, "error", {"message": "Could not process that request. Please try again.", "retryable": True, "status": 502})
    finally:
        try:
            await response.write_eof()
        except ConnectionResetError:
            pass
    return response


@routes.post("/api/app/ai/mix")
async def ai_mix(request: web.Request) -> web.Response:
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if gemini.available() and not _take_token(uid):
        return _rate_limited()
    result = await ai_rec.get_ai_mix(
        uid,
        prompt=str(body.get("prompt") or "")[:240],
        discovery=str(body.get("discovery") or "balanced"),
    )
    if result.get("error"):
        return web.json_response(result, status=422)
    return web.json_response(result)
