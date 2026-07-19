"""Shared Gemini generateContent helper (structured JSON output).

Centralises the raw-aiohttp call proven in filename_ai / admin AI-suggest so
new features (per-user AI recommendations) don't duplicate the request shape,
timeout, semaphore and error handling.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import aiohttp

from main.vars import Var

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/{model}:generateContent?key={key}"
)

# Cap concurrent Gemini calls process-wide so a burst of user requests can't
# exhaust the quota or pile up sockets.
_CONCURRENCY = 4
_semaphore: Optional[asyncio.Semaphore] = None


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_CONCURRENCY)
    return _semaphore


def available() -> bool:
    return bool(Var.GEMINI_API_KEY)


async def generate_json(
    prompt: str,
    *,
    model: str = "gemini-2.5-flash-lite",
    schema: Optional[dict] = None,
    timeout: float = 45.0,
) -> Optional[dict]:
    """Call Gemini and return the parsed JSON object, or None on any failure.

    When ``schema`` is given the model is constrained to that response shape
    (Gemini structured output). Every failure mode — missing key, non-200,
    timeout, safety block, malformed JSON — returns None so callers fall back.
    """
    if not Var.GEMINI_API_KEY or not prompt:
        return None
    url = _ENDPOINT.format(model=model, key=Var.GEMINI_API_KEY)
    generation_config: dict = {"response_mime_type": "application/json"}
    if schema is not None:
        generation_config["response_schema"] = schema
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    try:
        async with _sem():
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as r:
                    if r.status != 200:
                        logging.warning("gemini: %s returned HTTP %d", model, r.status)
                        return None
                    data = await r.json(content_type=None)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except asyncio.TimeoutError:
        logging.warning("gemini: timeout on %s", model)
        return None
    except Exception as exc:
        # Log the exception type only — never exc_info: some aiohttp errors embed
        # the request URL, which carries the API key as a query param.
        logging.debug("gemini: call failed (%s)", type(exc).__name__)
        return None
