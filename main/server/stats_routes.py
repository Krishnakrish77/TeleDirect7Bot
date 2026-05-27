"""User viewing / listening stats page.

GET /stats — personal activity page showing:
  - Total hours watched / listened
  - Favourite genres (by play count)
  - Most replayed titles (from watch history)
  - Weekly activity heatmap (last 12 weeks)
  - Media type breakdown (video vs audio)
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main.utils.user_auth import get_user
from main.utils import cw_store, wh_store, media_index

routes = web.RouteTableDef()

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=True,
)

_CW_KEY_RE = re.compile(r'^[A-Za-z0-9_-]*[A-Za-z_-](\d+)$')


def _fmt_hours(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h >= 1:
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{m}m" if m else "< 1m"


@routes.get("/stats")
async def stats_page(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        raise web.HTTPFound("/")

    user_id = int(user["sub"])

    # Fetch CW data (position + duration for in-progress items)
    # and watch history (completed plays) concurrently
    import asyncio
    cw_data, history = await asyncio.gather(
        cw_store.get_all(user_id),
        wh_store.get_recent(user_id, limit=500),
    )

    # ── Total watch time ──────────────────────────────────────────────────
    total_seconds = 0.0
    # From CW: sum of current positions (partially watched)
    for entry in cw_data.values():
        total_seconds += entry.get("pos", 0)
    # From watch history: each completed watch contributes its duration from
    # the catalogue item; fall back to 0 if item not found
    for h in history:
        m = _CW_KEY_RE.match(h.get("cw_key", ""))
        if m:
            item = media_index.get_item(int(m.group(1)))
            if item and item.duration:
                total_seconds += item.duration

    # ── Genre breakdown ───────────────────────────────────────────────────
    genre_counts: Counter = Counter()
    kind_counts: Counter = Counter()
    for h in history:
        m = _CW_KEY_RE.match(h.get("cw_key", ""))
        if not m:
            continue
        item = media_index.get_item(int(m.group(1)))
        if not item:
            continue
        for g in (item.tmdb_genres or []):
            genre_counts[g] += 1
        kind_counts[item.media_kind or "video"] += 1

    top_genres = genre_counts.most_common(6)

    # ── Most replayed (watch history, deduplicated by title) ───────────────
    title_counts: Counter = Counter()
    title_meta: dict = {}
    for h in history:
        title = h.get("title", "")
        if title:
            title_counts[title] += 1
            if title not in title_meta:
                m = _CW_KEY_RE.match(h.get("cw_key", ""))
                if m:
                    item = media_index.get_item(int(m.group(1)))
                    if item:
                        title_meta[title] = {
                            "poster": (f"https://image.tmdb.org/t/p/w92{item.poster_path}"
                                       if item.poster_path
                                       else f"/thumb/{item.secure_hash}{item.message_id}.jpg"),
                            "url": f"/watch/{item.secure_hash}{item.message_id}",
                            "media_kind": item.media_kind or "video",
                        }
    most_replayed = [
        {"title": t, "count": c, **title_meta.get(t, {})}
        for t, c in title_counts.most_common(10)
        if c > 1
    ]

    # ── Weekly heatmap (last 12 weeks, Mon-Sun) ───────────────────────────
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(weeks=12)
    daily_counts: defaultdict = defaultdict(int)
    for h in history:
        watched_at = h.get("watched_at")
        if watched_at and watched_at >= week_start:
            day_key = watched_at.strftime("%Y-%m-%d")
            daily_counts[day_key] += 1
    # Build 12×7 grid (12 weeks, Mon=0 … Sun=6)
    heatmap = []
    # Find the Monday 12 weeks ago
    monday = (now - timedelta(weeks=12)).date()
    monday -= timedelta(days=monday.weekday())
    for _ in range(12 * 7):
        dk = monday.strftime("%Y-%m-%d")
        heatmap.append({"date": dk, "count": daily_counts.get(dk, 0),
                         "dow": monday.weekday()})
        monday += timedelta(days=1)

    tpl = _env.get_template("stats.html")
    body = await tpl.render_async(
        user=user,
        total_time=_fmt_hours(total_seconds),
        total_plays=len(history),
        top_genres=top_genres,
        kind_video=kind_counts.get("video", 0),
        kind_audio=kind_counts.get("audio", 0),
        most_replayed=most_replayed,
        heatmap=heatmap,
    )
    return web.Response(text=body, content_type="text/html")
