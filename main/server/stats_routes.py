"""Viewing / listening stats — Spotify Wrapped style year-in-review."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main.server.tmdb_images import tmdb_image_url
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

def _tmdb_poster(item) -> str:
    """Return a displayable poster URL for a catalogue item."""
    if item.poster_path:
        return tmdb_image_url(item.poster_path, "w342")
    suffix = "?v=audio3" if getattr(item, "media_kind", "") == "audio" else ""
    return f"/thumb/{item.secure_hash}{item.message_id}.jpg{suffix}"


def _json(data: dict, *, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, separators=(",", ":")),
        content_type="application/json",
        status=status,
        headers={"Cache-Control": "no-store"},
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


def _utc_naive_from_ms(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _utc_naive(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _play_count(entry: dict) -> int:
    try:
        count = int(entry.get("play_count") or 1)
    except (TypeError, ValueError):
        count = 1
    return max(1, min(count, 500))


def _binge_stats(timestamps: list, gap_hours: int = 3, min_len: int = 3) -> tuple[int, int]:
    """Given completion timestamps, return (longest_binge, binge_sessions).

    A "session" is a run of completions each within ``gap_hours`` of the last;
    a "binge" is a session of at least ``min_len`` completions. longest_binge
    is the largest session size (0 if none reaches min_len).

    ponytail: same-second duplicate completion events (a double-fire, or looping
    a very short track) can over-count a session by a unit. Not worth de-duping
    on (cw_key, second) unless it ever shows up as wrong.
    """
    stamps = sorted(t for t in timestamps if t is not None)
    if not stamps:
        return 0, 0
    gap = timedelta(hours=gap_hours)
    longest = current = 1
    sessions = 0
    for prev, nxt in zip(stamps, stamps[1:]):
        if nxt - prev <= gap:
            current += 1
        else:
            if current >= min_len:
                sessions += 1
            current = 1
        longest = max(longest, current)
    if current >= min_len:
        sessions += 1
    return (longest if longest >= min_len else 0), sessions


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


async def _stats_payload(user_id: int) -> dict:
    import asyncio
    cw_data, summary_history, events = await asyncio.gather(
        cw_store.get_all(user_id),
        wh_store.get_recent(user_id, limit=500),
        wh_store.get_events(user_id),
    )

    # New immutable events give exact replay timing. Preserve the compact
    # legacy summary as a synthetic event only for completions that predate
    # event tracking, so existing users do not lose their totals on upgrade.
    event_counts = Counter(event.get("cw_key", "") for event in events)
    history = list(events)
    for summary in summary_history:
        key = summary.get("cw_key", "")
        legacy_plays = max(0, _play_count(summary) - event_counts[key])
        if legacy_plays:
            legacy = dict(summary)
            legacy["play_count"] = legacy_plays
            history.append(legacy)
    history.sort(key=lambda entry: _utc_naive(entry.get("watched_at")) or datetime.min, reverse=True)

    # ── Completed items and active CW sessions ──────────────────────────────
    # history_mids are used for catalogue lookups and completion-rate totals.
    history_mids: set = set()
    for h in history:
        ck = h.get("cw_key", "")
        if not ck:
            continue
        m = _CW_KEY_RE.match(ck)
        if m:
            history_mids.add(int(m.group(1)))

    # A title completed on an earlier session can legitimately be in progress
    # again. Only suppress CW progress when its completion happened after the
    # current playback session began.
    last_completion: dict[str, datetime] = {}
    for h in history:
        key = h.get("cw_key", "")
        watched_at = _utc_naive(h.get("watched_at"))
        if key and watched_at and (key not in last_completion or watched_at > last_completion[key]):
            last_completion[key] = watched_at

    def _cw_is_already_completed(key: str, entry: dict) -> bool:
        watched_at = last_completion.get(key)
        if watched_at is None:
            return False
        try:
            started_ms = int(entry.get("startedAt") or entry.get("t") or 0)
        except (TypeError, ValueError):
            started_ms = 0
        started_at = _utc_naive_from_ms(started_ms)
        return bool(started_at and watched_at >= started_at)

    in_progress_entries = [
        (key, entry) for key, entry in cw_data.items()
        if _CW_KEY_RE.match(key)
        and entry.get("dur", 0) > 0
        and entry.get("pos", 0) / entry["dur"] >= 0.05
        and not _cw_is_already_completed(key, entry)
    ]

    # ── Total watch/listen time (with audio/video split) ─────────────────
    total_seconds = 0.0
    video_seconds = 0.0
    audio_seconds = 0.0
    for ck, entry in cw_data.items():
        m = _CW_KEY_RE.match(ck)
        if m and not _cw_is_already_completed(ck, entry):
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
                plays = _play_count(h)
                total_seconds += item.duration * plays
                if item.media_kind == "audio":
                    audio_seconds += item.duration * plays
                else:
                    video_seconds += item.duration * plays

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
    decade_counts:   Counter = Counter()   # plays by release decade (from item.year)
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
        plays = _play_count(h)

        # Tallies
        # Genre: for audio items without TMDB genres, fall back to ID3 tags
        # which often carry genre strings (e.g. "Rock", "Carnatic", "Jazz").
        genres = item.tmdb_genres or []
        if not genres and item.media_kind == "audio" and item.tags:
            import re as _rx
            genres = [_rx.sub(r"^#", "", t).strip().title()
                      for t in item.tags if t and not t.startswith("@")]
        for g in genres:
            genre_counts[g] += plays
        kind_counts[item.media_kind or "video"] += plays
        if item.year:
            decade_counts[(int(item.year) // 10) * 10] += plays
        if item.director:
            for d in item.director.split(","):
                d = d.strip()
                if d:
                    director_counts[d] += plays
        if item.artist:
            for a in item.artist.split(","):
                a = a.strip()
                if a:
                    artist_counts[a] += plays

        # Title grouping — album_key groups music (movie_key is "" for audio).
        group = item.series_key or item.album_key or item.movie_key or ck
        title_counts[group] += plays
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
            poster = _tmdb_poster(item)
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
    top_title_label = "Most played"
    if top_title is None and in_progress_entries:
        progress_key, _progress = max(in_progress_entries, key=lambda pair: pair[1].get("t", 0))
        progress_match = _CW_KEY_RE.match(progress_key)
        progress_item = media_index.get_item(int(progress_match.group(1))) if progress_match else None
        if progress_item:
            if progress_item.series_key:
                progress_title = progress_item.series_title or progress_item.title
                progress_url = f"/series/{progress_item.series_key}"
            elif progress_item.album_key:
                progress_title = progress_item.album_title or progress_item.title
                progress_url = f"/album/{progress_item.album_key}"
            elif progress_item.movie_key:
                progress_title = progress_item.title
                progress_url = f"/movie/{progress_item.movie_key}"
            else:
                progress_title = progress_item.title
                progress_url = f"/watch/{progress_item.secure_hash}{progress_item.message_id}"
            top_title = {
                "title": progress_title,
                "poster": _tmdb_poster(progress_item),
                "url": progress_url,
                "media_kind": progress_item.media_kind or "video",
                "year": progress_item.year or "",
                "is_series": bool(progress_item.series_key),
            }
            top_title_label = "Continue watching"
    total_titles = len(title_counts)  # distinct series/movies/albums completed

    # ── Temporal patterns ─────────────────────────────────────────────────
    dow_counts:  Counter = Counter()   # 0=Mon … 6=Sun
    hour_counts: Counter = Counter()   # 0-23
    from datetime import date as _date_cls
    _today_utc     = datetime.now(timezone.utc).date()
    _this_monday   = _today_utc - timedelta(days=_today_utc.weekday())
    _heatmap_start = _this_monday - timedelta(weeks=12)
    # Align week_start to the Monday that opens the heatmap grid so every
    # cell in the grid can find its entry in daily_counts (no silent gap at
    # the start and no cut-off at the end of the current week).
    week_start     = datetime(_heatmap_start.year, _heatmap_start.month, _heatmap_start.day)
    daily_counts: defaultdict = defaultdict(int)

    for h in history:
        wa = _utc_naive(h.get("watched_at"))
        if not wa:
            continue
        plays = _play_count(h)
        dow_counts[wa.weekday()] += plays
        hour_counts[wa.hour]     += plays
        if wa >= week_start:
            daily_counts[wa.strftime("%Y-%m-%d")] += plays

    # Also count days with genuinely in-progress CW activity (t = epoch-ms of
    # the last save), including a rewatch started after an earlier completion.
    for ck, entry in in_progress_entries:
        t_ms = entry.get("t", 0)
        if t_ms > 0:
            cw_day = _utc_naive_from_ms(t_ms)
            if cw_day is None:
                continue
            if cw_day >= week_start:
                daily_counts[cw_day.strftime("%Y-%m-%d")] += 1
            dow_counts[cw_day.weekday()] += 1
            hour_counts[cw_day.hour] += 1

    # ── All-time active days set for streak computation ───────────────────
    # daily_counts is windowed to 12 weeks; using it for streaks would cap
    # them at 84. Build an unwindowed set from the full history (365-day TTL)
    # and cw_data (90-day TTL) so long streaks are reported correctly.
    _all_active_days: set = set()
    for h in history:
        _wa = _utc_naive(h.get("watched_at"))
        if _wa:
            _all_active_days.add(_wa.strftime("%Y-%m-%d"))
    for _ck, _entry in in_progress_entries:
        _t = _entry.get("t", 0)
        if _t > 0:
            _cw_day = _utc_naive_from_ms(_t)
            if _cw_day:
                _all_active_days.add(_cw_day.strftime("%Y-%m-%d"))

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
    total_plays   = sum(_play_count(h) for h in history)
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
    in_progress = [key for key, _entry in in_progress_entries]
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
        _mwa = _utc_naive(h.get("watched_at"))
        if _mwa:
            month_counts[_mwa.strftime("%b %Y")] += _play_count(h)
    best_month = month_counts.most_common(1)[0] if month_counts else None

    # ── Recent watch history (last 20 distinct titles) ────────────────────
    recent_history: list[dict] = []
    _seen_groups: set = set()
    for h in history:
        if len(recent_history) >= 20:
            break
        ck = h.get("cw_key", "")
        m = _CW_KEY_RE.match(ck)
        if not m:
            continue
        item = media_index.get_item(int(m.group(1)))
        if not item:
            continue
        group = item.series_key or item.album_key or item.movie_key or ck
        if group in _seen_groups:
            continue
        _seen_groups.add(group)
        if item.series_key:
            rh_title = item.series_title or item.title
            rh_url = f"/series/{item.series_key}"
        elif item.album_key:
            rh_title = item.album_title or item.title
            rh_url = f"/album/{item.album_key}"
        elif item.movie_key:
            rh_title = item.title
            rh_url = f"/movie/{item.movie_key}"
        else:
            rh_title = item.title
            rh_url = f"/watch/{item.secure_hash}{item.message_id}"
        rh_poster = _tmdb_poster(item)
        wa = _utc_naive(h.get("watched_at"))
        if wa:
            rh_date = wa.strftime("%b %d")
        else:
            rh_date = ""
        recent_history.append({
            "title": rh_title or h.get("title", ""),
            "poster": rh_poster,
            "url": rh_url,
            "media_kind": item.media_kind or "video",
            "year": item.year or "",
            "watched_at": rh_date,
        })

    # ── Personality — require meaningful activity before labelling ────────
    # Threshold: 10+ plays OR 3+ hours (avoids both the "1 episode = label"
    # problem for episodic content AND the "9 films = no label" problem for
    # movie watchers).
    personality = (
        _personality(top_genre, night_pct, weekend_pct, completion, audio_pct)
        if total_plays >= 10 or total_hours >= 3 else ""
    )

    # ── New metrics: decade mix, rewatch ratio, genre diversity, binges ────
    decades = [{"label": f"{d}s", "count": c} for d, c in sorted(decade_counts.items())]

    # Rewatch ratio at ITEM granularity (per cw_key). Grouping by series/album
    # would count a fresh multi-episode/track binge as ~90% "rewatches"; a
    # per-episode/per-track count correctly reports that as 0% until something
    # is actually replayed.
    item_plays: Counter = Counter()
    for h in history:
        ck = h.get("cw_key", "")
        if ck:
            item_plays[ck] += _play_count(h)
    total_item_plays = sum(item_plays.values())
    rewatch_plays = max(0, total_item_plays - len(item_plays))
    rewatch_pct = round(rewatch_plays / total_item_plays * 100) if total_item_plays else 0
    rewatch_label = (
        "Comfort re-watcher" if rewatch_pct >= 40
        else "Always something new" if rewatch_pct <= 15
        else "A bit of both"
    )

    genres_explored = len(genre_counts)
    diversity_label = (
        "Explorer" if genres_explored >= 12
        else "Focused" if genres_explored <= 4
        else "Balanced"
    )

    longest_binge, binge_sessions = _binge_stats(
        [_utc_naive(e.get("watched_at")) for e in events]
    )

    return {
        "total_seconds": total_seconds,
        "video_seconds": video_seconds,
        "audio_seconds": audio_seconds,
        "total_hours": total_hours,
        "total_mins": total_mins,
        "video_hours": video_hours,
        "video_mins": video_mins,
        "audio_hours": audio_hours,
        "audio_mins": audio_mins,
        "total_plays": total_plays,
        "total_titles": total_titles,
        "in_progress": len(in_progress),
        "has_activity": bool(total_seconds or total_plays or in_progress),
        "active_days": active_days,
        "equiv_movies": equiv_movies,
        "equiv_flights": equiv_flights,
        "top_title": top_title,
        "top_title_label": top_title_label,
        "most_replayed": most_replayed[1:] if len(most_replayed) > 1 else [],
        "top_genres": top_genres,
        "top_genre": top_genre,
        "top_director": top_director[0] if top_director else None,
        "top_artists": top_artists,
        "best_month": best_month,
        "finished": finished,
        "started": started,
        "n_video": n_video,
        "n_audio": n_audio,
        "dow_bars": dow_bars,
        "best_day": best_day_name,
        "tod_label": tod_label,
        "tod_emoji": tod_emoji,
        "timed_plays": timed_plays,
        "completion": completion,
        "personality": personality,
        "heatmap": heatmap,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "recent_history": recent_history,
        "decades": decades,
        "rewatch_pct": rewatch_pct,
        "rewatch_label": rewatch_label,
        "genres_explored": genres_explored,
        "diversity_label": diversity_label,
        "longest_binge": longest_binge,
        "binge_sessions": binge_sessions,
    }


@routes.get("/stats")
async def stats_page(request: web.Request) -> web.Response:
    if request.cookies.get("td_ui") == "react" and request.headers.get("HX-Request") != "true":
        raise web.HTTPFound("/app/stats")

    user = get_user(request)
    if not user:
        raise web.HTTPFound("/")

    payload = await _stats_payload(int(user["sub"]))
    tpl  = _env.get_template("stats.html")
    body = await tpl.render_async(user=user, **payload)
    return web.Response(text=body, content_type="text/html")


@routes.get("/api/app/stats")
async def api_app_stats(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    payload = await _stats_payload(int(user["sub"]))
    return _json(payload)
