"""Viewing / listening stats — Spotify Wrapped style year-in-review."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

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

_DAYS   = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_HOURS  = {
    range(5,  12): ("Morning",   "☀️"),
    range(12, 17): ("Afternoon", "🌤"),
    range(17, 22): ("Evening",   "🌆"),
}

def _time_label(hour: int) -> tuple:
    for rng, label in _HOURS.items():
        if hour in rng:
            return label
    return ("Night", "🌙")

def _personality(top_genre: str, night_pct: int, weekend_pct: int,
                  completion: int, audio_pct: int) -> str:
    if audio_pct >= 60:
        return "Music Lover 🎵"
    if completion >= 85:
        return "Devoted Finisher 🎯"
    if night_pct >= 50:
        return "Night Owl 🌙"
    if weekend_pct >= 60:
        return "Weekend Binger 🛋️"
    if top_genre in ("Drama", "Romance"):
        return "Drama Lover 🎭"
    if top_genre in ("Action", "Thriller"):
        return "Thrill Seeker ⚡"
    if top_genre in ("Comedy",):
        return "Laugh Hunter 😂"
    if top_genre in ("Animation",):
        return "Animation Fan 🎨"
    if top_genre in ("Documentary",):
        return "Knowledge Seeker 📚"
    return "Dedicated Viewer 🍿"


@routes.get("/stats")
async def stats_page(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        raise web.HTTPFound("/")

    user_id = int(user["sub"])

    import asyncio
    cw_data, history = await asyncio.gather(
        cw_store.get_all(user_id),
        wh_store.get_recent(user_id, limit=500),
    )

    # ── Avoid double-counting CW items that also appear in history ─────────
    history_mids: set = set()
    for h in history:
        m = _CW_KEY_RE.match(h.get("cw_key", ""))
        if m:
            history_mids.add(int(m.group(1)))

    # ── Total watch/listen time ───────────────────────────────────────────
    total_seconds = 0.0
    for ck, entry in cw_data.items():
        m = _CW_KEY_RE.match(ck)
        if m and int(m.group(1)) not in history_mids:
            total_seconds += entry.get("pos", 0)
    for h in history:
        m = _CW_KEY_RE.match(h.get("cw_key", ""))
        if m:
            item = media_index.get_item(int(m.group(1)))
            if item and item.duration:
                total_seconds += item.duration

    total_hours = int(total_seconds // 3600)
    total_mins  = int((total_seconds % 3600) // 60)

    # Fun equivalents
    equiv_movies  = round(total_seconds / 6600)   # avg movie ~110 min
    equiv_flights = round(total_seconds / (4 * 3600))  # ~4h avg flight

    # ── Genre, kind, director, artist tallies ─────────────────────────────
    genre_counts:    Counter = Counter()
    director_counts: Counter = Counter()
    artist_counts:   Counter = Counter()
    kind_counts:     Counter = Counter()

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
        if item.director:
            director_counts[item.director] += 1
        if item.artist:
            for a in item.artist.split(","):
                a = a.strip()
                if a:
                    artist_counts[a] += 1

    top_genres   = genre_counts.most_common(5)
    top_genre    = top_genres[0][0] if top_genres else ""
    top_director = director_counts.most_common(1)
    top_artist   = artist_counts.most_common(1)

    n_video = kind_counts.get("video", 0)
    n_audio = kind_counts.get("audio", 0)
    total_k = n_video + n_audio
    audio_pct = int(n_audio / total_k * 100) if total_k else 0

    # ── Most replayed (keyed by cw_key to avoid title collisions) ─────────
    ck_counts: Counter = Counter()
    ck_meta:   dict    = {}
    for h in history:
        ck = h.get("cw_key", "")
        if not ck:
            continue
        ck_counts[ck] += 1
        if ck not in ck_meta:
            m = _CW_KEY_RE.match(ck)
            if m:
                item = media_index.get_item(int(m.group(1)))
                if item:
                    ck_meta[ck] = {
                        "title":      item.title or h.get("title", ""),
                        "poster":     (f"https://image.tmdb.org/t/p/w342{item.poster_path}"
                                       if item.poster_path
                                       else f"/thumb/{item.secure_hash}{item.message_id}.jpg"),
                        "url":        f"/watch/{item.secure_hash}{item.message_id}",
                        "media_kind": item.media_kind or "video",
                        "year":       item.year or "",
                    }

    most_replayed = [
        {"count": c, **ck_meta[ck]}
        for ck, c in ck_counts.most_common(5)
        if ck in ck_meta
    ]
    top_title = most_replayed[0] if most_replayed else None

    # ── Temporal patterns ─────────────────────────────────────────────────
    dow_counts:  Counter = Counter()   # 0=Mon … 6=Sun
    hour_counts: Counter = Counter()   # 0-23
    week_start  = datetime.utcnow() - timedelta(weeks=12)
    daily_counts: defaultdict = defaultdict(int)

    for h in history:
        wa = h.get("watched_at")
        if not wa:
            continue
        if hasattr(wa, 'tzinfo') and wa.tzinfo:
            wa = wa.replace(tzinfo=None)
        dow_counts[wa.weekday()] += 1
        hour_counts[wa.hour]     += 1
        if wa >= week_start:
            daily_counts[wa.strftime("%Y-%m-%d")] += 1

    # Day-of-week bar: max=100 so bars are relative
    max_dow = max(dow_counts.values(), default=1)
    dow_bars = [
        {"label": _DAYS[i], "count": dow_counts[i],
         "pct": int(dow_counts[i] / max_dow * 100)}
        for i in range(7)
    ]

    # Best day
    best_dow_idx  = dow_counts.most_common(1)[0][0] if dow_counts else -1
    best_day_name = _DAYS[best_dow_idx] if best_dow_idx >= 0 else "—"

    # Weekend vs weekday split
    weekend_plays = dow_counts[5] + dow_counts[6]
    total_plays   = len(history)
    weekend_pct   = int(weekend_plays / total_plays * 100) if total_plays else 0

    # Best time of day
    best_hour       = hour_counts.most_common(1)[0][0] if hour_counts else 12
    tod_label, tod_emoji = _time_label(best_hour)
    night_plays     = sum(hour_counts[h] for h in list(range(22,24)) + list(range(0,5)))
    night_pct       = int(night_plays / total_plays * 100) if total_plays else 0

    # ── Completion rate ───────────────────────────────────────────────────
    started  = len(history_mids) + len([k for k in cw_data if k not in history_mids])
    finished = len(history_mids)
    completion = int(finished / started * 100) if started else 0

    # ── Activity heatmap (12 weeks) ───────────────────────────────────────
    active_days = sum(1 for v in daily_counts.values() if v > 0)
    heatmap = []
    from datetime import date as _date
    monday = (_date.today() - timedelta(weeks=12))
    monday -= timedelta(days=monday.weekday())
    for _ in range(12 * 7):
        dk = monday.strftime("%Y-%m-%d")
        heatmap.append({"date": dk, "count": daily_counts.get(dk, 0),
                         "dow": monday.weekday()})
        monday += timedelta(days=1)

    # ── Personality ───────────────────────────────────────────────────────
    personality = _personality(top_genre, night_pct, weekend_pct,
                                completion, audio_pct)

    tpl  = _env.get_template("stats.html")
    body = await tpl.render_async(
        user         = user,
        total_hours  = total_hours,
        total_mins   = total_mins,
        total_plays  = total_plays,
        active_days  = active_days,
        equiv_movies = equiv_movies,
        equiv_flights= equiv_flights,
        top_title    = top_title,
        most_replayed= most_replayed[1:] if len(most_replayed) > 1 else [],
        top_genres   = top_genres,
        top_genre    = top_genre,
        top_director = top_director[0] if top_director else None,
        top_artist   = top_artist[0]   if top_artist   else None,
        n_video      = n_video,
        n_audio      = n_audio,
        dow_bars     = dow_bars,
        best_day     = best_day_name,
        tod_label    = tod_label,
        tod_emoji    = tod_emoji,
        completion   = completion,
        personality  = personality,
        heatmap      = heatmap,
    )
    return web.Response(text=body, content_type="text/html")
