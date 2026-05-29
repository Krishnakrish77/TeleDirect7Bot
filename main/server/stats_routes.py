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

import re as _re
_env.filters["artist_slug"] = lambda s: _re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
from main.utils.media_index import _person_slug as _mpslug
_env.filters["person_slug"] = lambda s: _mpslug(s or "")

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
    # history_mids: int message IDs (for catalogue lookups)
    # history_cw_keys: raw cw_key strings (for comparing against cw_data keys,
    #   which are also strings — comparing set[int] against str always misses)
    history_mids: set = set()
    history_cw_keys: set = set()
    for h in history:
        ck = h.get("cw_key", "")
        if not ck:
            continue
        history_cw_keys.add(ck)
        m = _CW_KEY_RE.match(ck)
        if m:
            history_mids.add(int(m.group(1)))

    # ── Total watch/listen time (with audio/video split) ─────────────────
    total_seconds = 0.0
    video_seconds = 0.0
    audio_seconds = 0.0
    for ck, entry in cw_data.items():
        m = _CW_KEY_RE.match(ck)
        if m and int(m.group(1)) not in history_mids:
            pos = entry.get("pos", 0)
            total_seconds += pos
            _it = media_index.get_item(int(m.group(1)))
            if _it:  # skip split for pruned items — kind is unknown
                if _it.media_kind == "audio":
                    audio_seconds += pos
                else:
                    video_seconds += pos
    for h in history:
        m = _CW_KEY_RE.match(h.get("cw_key", ""))
        if m:
            item = media_index.get_item(int(m.group(1)))
            if item and item.duration:
                total_seconds += item.duration
                if item.media_kind == "audio":
                    audio_seconds += item.duration
                else:
                    video_seconds += item.duration

    total_hours = int(total_seconds // 3600)
    total_mins  = int((total_seconds % 3600) // 60)
    video_hours = int(video_seconds // 3600)
    video_mins  = int((video_seconds % 3600) // 60)
    audio_hours = int(audio_seconds // 3600)
    audio_mins  = int((audio_seconds % 3600) // 60)

    # Fun equivalents
    equiv_movies  = round(total_seconds / 6600)   # avg movie ~110 min
    equiv_flights = round(total_seconds / (4 * 3600))  # ~4h avg flight

    # ── Single pass: genre/kind/director/artist tallies + title grouping ────
    # Merged from two separate loops to halve the get_item() calls
    # (~500 lookups instead of ~1000 for a 500-entry history).
    genre_counts:    Counter = Counter()
    director_counts: Counter = Counter()
    artist_counts:   Counter = Counter()
    kind_counts:     Counter = Counter()
    title_counts:    Counter = Counter()
    title_meta:      dict    = {}

    for h in history:
        ck = h.get("cw_key", "")
        if not ck:
            continue
        m = _CW_KEY_RE.match(ck)
        if not m:
            continue
        item = media_index.get_item(int(m.group(1)))
        if not item:
            # Pruned — skip; can't group or tally without metadata.
            continue

        # Tallies
        # Genre: for audio items without TMDB genres, fall back to ID3 tags
        # which often carry genre strings (e.g. "Rock", "Carnatic", "Jazz").
        genres = item.tmdb_genres or []
        if not genres and item.media_kind == "audio" and item.tags:
            import re as _rx
            genres = [_rx.sub(r"^#", "", t).strip().title()
                      for t in item.tags if t and not t.startswith("@")]
        for g in genres:
            genre_counts[g] += 1
        kind_counts[item.media_kind or "video"] += 1
        if item.director:
            for d in item.director.split(","):
                d = d.strip()
                if d:
                    director_counts[d] += 1
        if item.artist:
            for a in item.artist.split(","):
                a = a.strip()
                if a:
                    artist_counts[a] += 1

        # Title grouping — album_key groups music (movie_key is "" for audio).
        # Use play_count from wh_store when available so re-watches count
        # properly; fall back to 1 for legacy entries without play_count.
        group = item.series_key or item.album_key or item.movie_key or ck
        title_counts[group] += h.get("play_count", 1)
        if group not in title_meta:
            if item.series_key:
                title = item.series_title or item.title
                url   = f"/series/{item.series_key}"
            elif item.album_key:
                title = item.album_title or item.title
                url   = f"/album/{item.album_key}"
            elif item.movie_key:
                title = item.title
                url   = f"/movie/{item.movie_key}"
            else:
                title = item.title
                url   = f"/watch/{item.secure_hash}{item.message_id}"
            poster = (f"https://image.tmdb.org/t/p/w342{item.poster_path}"
                      if item.poster_path
                      else f"/thumb/{item.secure_hash}{item.message_id}.jpg")
            title_meta[group] = {
                "title":      title or h.get("title", ""),
                "poster":     poster,
                "url":        url,
                "media_kind": item.media_kind or "video",
                "year":       item.year or "",
                "is_series":  bool(item.series_key),
            }

    top_genres   = genre_counts.most_common(5)
    top_genre    = top_genres[0][0] if top_genres else ""
    top_director = director_counts.most_common(1)
    top_artists  = artist_counts.most_common(3)   # top-3 for display

    n_video = kind_counts.get("video", 0)
    n_audio = kind_counts.get("audio", 0)
    total_k = n_video + n_audio
    audio_pct = int(n_audio / total_k * 100) if total_k else 0

    most_replayed = [
        {"count": c, **title_meta[g]}
        for g, c in title_counts.most_common(5)
        if g in title_meta
    ]
    top_title    = most_replayed[0] if most_replayed else None
    total_titles = len(title_counts)  # distinct series/movies/albums completed

    # ── Temporal patterns ─────────────────────────────────────────────────
    dow_counts:  Counter = Counter()   # 0=Mon … 6=Sun
    hour_counts: Counter = Counter()   # 0-23
    from datetime import date as _date_cls
    _today_utc     = datetime.utcnow().date()
    _this_monday   = _today_utc - timedelta(days=_today_utc.weekday())
    _heatmap_start = _this_monday - timedelta(weeks=12)
    # Align week_start to the Monday that opens the heatmap grid so every
    # cell in the grid can find its entry in daily_counts (no silent gap at
    # the start and no cut-off at the end of the current week).
    week_start     = datetime(_heatmap_start.year, _heatmap_start.month, _heatmap_start.day)
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

    # Also count days with in-progress CW activity (t = epoch-ms of last save).
    # Skip entries already in history_cw_keys to avoid double-counting the same
    # day (completion date from history + CW t date for the same item).
    for ck, entry in cw_data.items():
        if ck in history_cw_keys:
            continue  # already counted via history loop above
        t_ms = entry.get("t", 0)
        if t_ms > 0:
            cw_day = datetime.utcfromtimestamp(t_ms / 1000)
            if cw_day >= week_start:
                daily_counts[cw_day.strftime("%Y-%m-%d")] += 1

    # ── All-time active days set for streak computation ───────────────────
    # daily_counts is windowed to 12 weeks; using it for streaks would cap
    # them at 84. Build an unwindowed set from the full history (365-day TTL)
    # and cw_data (90-day TTL) so long streaks are reported correctly.
    _all_active_days: set = set()
    for h in history:
        _wa = h.get("watched_at")
        if _wa:
            if hasattr(_wa, 'tzinfo') and _wa.tzinfo:
                _wa = _wa.replace(tzinfo=None)
            _all_active_days.add(_wa.strftime("%Y-%m-%d"))
    for _ck, _entry in cw_data.items():
        _t = _entry.get("t", 0)
        if _t > 0:
            _all_active_days.add(
                datetime.utcfromtimestamp(_t / 1000).strftime("%Y-%m-%d")
            )

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
    # Use timed_plays (entries with a watched_at date) as the denominator —
    # total_plays counts all history rows, some of which may lack a timestamp,
    # which would make the percentages slightly low.
    weekend_plays = dow_counts[5] + dow_counts[6]
    total_plays   = len(history)
    timed_plays   = sum(dow_counts.values())
    weekend_pct   = int(weekend_plays / timed_plays * 100) if timed_plays else 0

    # Best time of day
    best_hour       = hour_counts.most_common(1)[0][0] if hour_counts else 12
    tod_label, tod_emoji = _time_label(best_hour)
    night_plays     = sum(hour_counts[h] for h in list(range(22,24)) + list(range(0,5)))
    night_pct       = int(night_plays / timed_plays * 100) if timed_plays else 0

    # ── Completion rate ───────────────────────────────────────────────────
    # Only count in-progress items that have a valid cw_key (valid regex +
    # minimum 5% progress) and are not already in watch history.
    in_progress = [
        k for k, v in cw_data.items()
        if k not in history_cw_keys
        and _CW_KEY_RE.match(k)
        and v.get("dur", 0) > 0
        and v.get("pos", 0) / v["dur"] >= 0.05
    ]
    started    = len(history_mids) + len(in_progress)
    finished   = len(history_mids)
    completion = int(finished / started * 100) if started else 0

    # ── Activity heatmap — Mon 12 weeks ago → today (inclusive) ─────────
    # Grid always ends on the current day so this week's activity is visible.
    active_days = sum(1 for v in daily_counts.values() if v > 0)
    heatmap = []
    _hday = _heatmap_start
    for _ in range((_today_utc - _heatmap_start).days + 1):
        dk = _hday.strftime("%Y-%m-%d")
        heatmap.append({"date": dk, "count": daily_counts.get(dk, 0), "dow": _hday.weekday()})
        _hday += timedelta(days=1)

    # ── Streak stats ──────────────────────────────────────────────────────
    # current_streak: consecutive days ending on the most recent active day
    # (today or yesterday). Watching at 11pm then checking stats at midnight
    # the next day shouldn't reset a long streak to 0.
    current_streak = 0
    _d = _today_utc
    if _d.strftime("%Y-%m-%d") not in _all_active_days:
        _d -= timedelta(days=1)  # allow streak to end yesterday
    while _d.strftime("%Y-%m-%d") in _all_active_days:
        current_streak += 1
        _d -= timedelta(days=1)

    # longest_streak: computed from _all_active_days (same 365-day window as
    # current_streak). Previously used the 84-day heatmap window, making the
    # two cards incomparable — longest could never exceed 84 even if current
    # was 200. Now both use the same dataset.
    longest_streak = 0
    _sorted_days = sorted(_all_active_days)
    _lrun = 0
    _lprev = None
    for _ds in _sorted_days:
        _dd = _date_cls.fromisoformat(_ds)
        if _lprev is not None and (_dd - _lprev).days == 1:
            _lrun += 1
        else:
            _lrun = 1
        if _lrun > longest_streak:
            longest_streak = _lrun
        _lprev = _dd

    # ── Most active month ─────────────────────────────────────────────────
    month_counts: Counter = Counter()
    for h in history:
        _mwa = h.get("watched_at")
        if _mwa:
            if hasattr(_mwa, 'tzinfo') and _mwa.tzinfo:
                _mwa = _mwa.replace(tzinfo=None)
            month_counts[_mwa.strftime("%b %Y")] += 1
    best_month = month_counts.most_common(1)[0] if month_counts else None

    # ── Personality — require meaningful activity before labelling ────────
    # Threshold: 10+ plays OR 3+ hours (avoids both the "1 episode = label"
    # problem for episodic content AND the "9 films = no label" problem for
    # movie watchers).
    personality = (
        _personality(top_genre, night_pct, weekend_pct, completion, audio_pct)
        if total_plays >= 10 or total_hours >= 3 else ""
    )

    tpl  = _env.get_template("stats.html")
    body = await tpl.render_async(
        user         = user,
        total_hours  = total_hours,
        total_mins   = total_mins,
        total_plays  = total_plays,
        total_titles = total_titles,
        active_days  = active_days,
        equiv_movies = equiv_movies,
        equiv_flights= equiv_flights,
        top_title    = top_title,
        most_replayed= most_replayed[1:] if len(most_replayed) > 1 else [],
        top_genres   = top_genres,
        top_genre    = top_genre,
        top_director = top_director[0] if top_director else None,
        top_artists  = top_artists,
        best_month   = best_month,
        finished     = finished,
        started      = started,
        n_video      = n_video,
        n_audio      = n_audio,
        dow_bars     = dow_bars,
        best_day     = best_day_name,
        tod_label    = tod_label,
        tod_emoji    = tod_emoji,
        timed_plays  = timed_plays,
        completion      = completion,
        personality     = personality,
        heatmap         = heatmap,
        current_streak  = current_streak,
        longest_streak  = longest_streak,
        video_hours     = video_hours,
        video_mins      = video_mins,
        audio_hours     = audio_hours,
        audio_mins      = audio_mins,
    )
    return web.Response(text=body, content_type="text/html")
