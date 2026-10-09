"""Reverse geocoding for photo GPS — Nominatim, no API key.

The photo pipeline extracts EXIF GPS (`gps {lat, lon}`). This module turns
that into a human-readable place label ("Lisbon, Portugal") stored on the
photo doc, making locations text-searchable and giving the SPA a Places
facet. Failures are logged and return "" — a missing label must never
block ingest.

Nominatim usage policy (operational constraints, not optional style):
  * absolute max 1 request/second — enforced by a module-level lock
  * a descriptive User-Agent identifying the app is required
Buckets: coordinates are rounded to 2 decimals (~1.1 km) to key the cache —
nearby photos of the same shoot share one lookup instead of thousands.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

log = logging.getLogger("photos.geocode")

_USER_AGENT = "TeleDirectPhotos/1.0 (self-hosted personal photo vault)"
_TIMEOUT = 10.0

# Single-flight + rate: one lookup at a time, >=1.1s between requests.
_lock = asyncio.Lock()
_last_request = 0.0

# {rounded "lat,lon" bucket: ("Place, Region", fetched_at)}
_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 30 * 24 * 3600.0  # place names are stable — 30 days


def bucket_key(lat: float, lon: float) -> str:
    """Cache/rate bucket for coordinates (~1.1 km grid)."""
    return f"{round(lat, 2)},{round(lon, 2)}"


async def reverse_geocode(lat: float, lon: float) -> str:
    """Place label for coordinates, or "" on any failure/absence.

    Cached per rounded bucket; serialized through the module lock so the
    1 req/s policy holds regardless of caller concurrency.
    """
    if lat is None or lon is None:
        return ""
    key = bucket_key(lat, lon)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[1] < _CACHE_TTL:
        return hit[0]

    async with _lock:
        # Re-check inside the lock: a queued caller may benefit from the
        # lookup the lock holder just completed.
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[1] < _CACHE_TTL:
            return hit[0]

        global _last_request
        wait = 1.1 - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()

        label = await _nominatim_lookup(lat, lon)
        if label:
            _cache[key] = (label, time.monotonic())
        # Failures are NOT cached as "" — a transient Nominatim outage
        # shouldn't pin an empty label for 30 days.
        return label


async def _nominatim_lookup(lat: float, lon: float) -> str:
    import json

    import aiohttp

    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=jsonv2&zoom=10&accept-language=en"
    )
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": _USER_AGENT}, timeout=aiohttp.ClientTimeout(total=_TIMEOUT)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    log.warning("geocode: nominatim HTTP %d for %s", resp.status, bucket_key(lat, lon))
                    return ""
                data = json.loads(await resp.text())
        label = _place_label(data)
        if not label:
            log.info("geocode: no place label for %s", bucket_key(lat, lon))
        return label
    except Exception:
        log.exception("geocode: lookup failed for %s", bucket_key(lat, lon))
        return ""


def _place_label(data: dict) -> str:
    """Compact label from a Nominatim reverse result: prefer city-level
    admin units over the raw display_name (which is a comma-joined address)."""
    if not isinstance(data, dict):
        return ""
    address = data.get("address") or {}
    for field in ("city", "town", "village", "municipality", "county", "state"):
        name = address.get(field)
        if name:
            country = address.get("country", "")
            return f"{name}, {country}" if country else name
    # Ocean/unincorporated results have no address parts — fall back to the
    # first two components of display_name.
    display = data.get("display_name") or ""
    if display:
        parts = [p.strip() for p in display.split(",") if p.strip()]
        return ", ".join(parts[:2])
    return ""


def reset_cache() -> None:
    """Test hook — clear the memo cache and rate timestamp."""
    _cache.clear()
    global _last_request
    _last_request = 0.0
