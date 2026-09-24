"""Gemini-powered filename sanitiser.

Replaces the growing regex list in _clean_file_name / clean_for_search
with a single Gemini call that handles:

  • Device-generated names  (video_2026-05-19_12-51-06.mp4, untitled.mp4,
                             VID_20260519_125106.mp4, (untitled).mp4 …)
  • Channel/site prefixes   (www_1TamilBlasters_, @channel_, lme-tvp_all)
  • Release noise           (BluRay, HDRip, x264, HEVC, language tags, …)
  • Title / year / quality  extracted in the same pass

Only runs when GEMINI_API_KEY is configured. Falls back gracefully —
callers always check the return value and keep the regex result if AI
parsing is unavailable or fails.

Integration: called from indexer.py as a background task right before
TMDB enrichment, so the cleaned title feeds directly into the TMDB
search query and improves match rates.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Optional

import aiohttp

from main.vars import Var


_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "clean_filename": {
            "type": "STRING",
            "description": "Readable display name, e.g. 'Dhurandhar The Revenge (2026).mkv'",
        },
        "title": {
            "type": "STRING",
            "description": "Movie or show title, no year, no noise",
        },
        "year": {
            "type": "INTEGER",
            "description": "4-digit release year, 0 if unknown",
        },
        "quality": {
            "type": "STRING",
            "description": "One of: 4K 1080p 720p 480p — or empty string",
        },
        "is_device_generated": {
            "type": "BOOLEAN",
            "description": "True when the filename is a device/recorder default with no real title",
        },
        "reasoning": {
            "type": "STRING",
            "description": "One-sentence explanation of what was stripped",
        },
    },
    "required": [
        "clean_filename", "title", "year", "quality",
        "is_device_generated", "reasoning",
    ],
}

_PROMPT = """\
You are a media filename parser. Given a raw video filename, extract structured metadata.

Strip the following noise categories:
1. Channel / site prefixes — e.g. "www_1TamilBlasters_garden_", "@tvp_all_", "lme-tvp_all"
2. Quality / codec tags — BluRay, HDRip, WEB-DL, x264, x265, HEVC, AVC, H.264, 10bit …
3. Language / region tags — Tamil, Hindi, Telugu, English, Dual, Multi …
4. Resolution as a tag — [1080p], 720p, 4K (keep the value in 'quality', strip from title)
5. Website watermarks — www.*, 1TamilBlasters, TamilMV, TamilRockers …
6. Brackets with noise — [TamilBlasters.net], (Official) etc.

Device-generated names (is_device_generated = true):
  video_2026-05-19_12-51-06.mp4, untitled.mp4, (untitled).mp4,
  VID_20260519_125106.mp4, MOV_20260519_125106.mov, recording.mp4, capture.mp4,
  screen recording.mp4, DCIM_001.mp4, bare timestamps …

Examples:
  "www_1TamilBlasters_garden_Dhurandhar_The_Revenge_2026_Tamil_HQ_HDRip.mkv"
    → title="Dhurandhar The Revenge", year=2026, quality="", clean_filename="Dhurandhar The Revenge (2026).mkv"

  "Harry.Potter.and.the.Sorcerers.Stone.2001.1080p.BluRay.x264.mkv"
    → title="Harry Potter and the Sorcerers Stone", year=2001, quality="1080p"

  "video_2026-05-19_12-51-06.mp4"
    → is_device_generated=true, title="", clean_filename=""

Filename to parse:
"""

_semaphore: Optional[asyncio.Semaphore] = None
_CONCURRENCY = 2


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_CONCURRENCY)
    return _sem()


async def parse_filename(
    raw_name: str,
    model: str = "gemini-2.5-flash-lite",
    known_series: Optional[list] = None,
) -> Optional[dict]:
    """Call Gemini to parse a raw filename. Returns the parsed dict or None.

    known_series: optional list of existing series titles in the catalogue.
    When provided, the prompt instructs the model to prefer those exact
    spellings so a near-duplicate doesn't split a series across two cards.
    """
    if not Var.GEMINI_API_KEY or not raw_name:
        return None

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{model}:generateContent?key={Var.GEMINI_API_KEY}"
    )
    context_block = ""
    if known_series:
        joined = ", ".join(f'"{s}"' for s in known_series[:30])
        context_block = (
            "\nCatalogue context — existing series titles "
            "(use these exact spellings when the filename matches):\n"
            f"  {joined}\n\n"
        )
    payload = {
        "contents": [{"parts": [{"text": _PROMPT + context_block + repr(raw_name)}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": _SCHEMA,
        },
    }

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status != 200:
                    logging.debug(
                        "filename_ai: Gemini returned %d for %r", r.status, raw_name
                    )
                    return None
                data = await r.json(content_type=None)

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
        logging.debug("filename_ai: %r → %s", raw_name, result.get("reasoning", ""))
        return result
    except asyncio.TimeoutError:
        logging.debug("filename_ai: timeout parsing %r", raw_name)
        return None
    except Exception:
        logging.debug("filename_ai: failed parsing %r", raw_name, exc_info=True)
        return None

