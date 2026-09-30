"""CouchMate server-side tools — read-only catalogue and watch-state lookups.

The model never touches these directly: it emits a functionCall, the buddy
chat route executes it here against server-trusted state (media_index,
cw_store, wh_store), and the result goes back as a functionResponse. Tools
are therefore read-only and user-scoped by construction — no model-supplied
user id exists, so a hostile turn cannot read another user's state or mutate
anything.

Every tool returns plain JSON-serialisable dicts capped in size; the route
serialises them into the functionResponse part verbatim.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Optional

from main.utils import cw_store, media_index, wh_store
from main.utils.buddy_context import _item_for_cw_key, _ratio

# Result caps: keep every tool response small enough that a chatty model
# can't blow the context window by calling tools in a loop.
_MAX_RESULTS = 8
_OVERVIEW_CAP = 220
_MAX_CALLS_PER_TURN = 4

_TOOL_DECLARATIONS = [
    {
        "name": "search_catalogue",
        "description": (
            "Search the platform's library for movies, series, music "
            "(albums/tracks) or books by title, artist or keyword. Use it to "
            "answer 'is X available?', to find something the user vaguely "
            "describes, or to ground a recommendation in titles the user can "
            "actually play. Results include a playHref the assistant may "
            "quote as plain text."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Title, artist or keyword to search for.",
                },
                "kind": {
                    "type": "STRING",
                    "description": "Optional filter: 'movie', 'series', 'album', 'audio' or 'book'.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "title_details",
        "description": (
            "Look up rich details for one library title by its exact playHref "
            "(from search_catalogue) — overview, genres, cast, year, runtime "
            "and, for series, the episode list with the user's watch state. "
            "Use it when the user asks about a specific show or film."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "playHref": {
                    "type": "STRING",
                    "description": "The playHref of the title, exactly as returned by search_catalogue.",
                },
            },
            "required": ["playHref"],
        },
    },
    {
        "name": "where_was_i",
        "description": (
            "Report the user's exact position across media: what is in "
            "progress right now (with percent through), what they finished "
            "most recently, and what they are reading (books, with percent "
            "through). Use it for 'what episode am I on?', 'what was the "
            "last thing I watched?' and 'where am I in that book?'."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "my_taste",
        "description": (
            "Summarise the user's taste profile: favourite genres, "
            "directors, frequently watched titles, explicit likes/dislikes "
            "and what they are currently partway through. Use it whenever "
            "the user asks for a recommendation, or asks 'what should I "
            "watch?' — personalise the answer with it."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def declarations() -> list[dict]:
    """Tool declarations passed to generateContent."""
    return [{"functionDeclarations": _TOOL_DECLARATIONS}]


def max_calls_per_turn() -> int:
    return _MAX_CALLS_PER_TURN


def _suggestion_row(item) -> dict:
    """One compact catalogue row (same shape as media_index.suggest)."""
    if item.series_key:
        return {
            "title": item.series_title or item.title,
            "kind": "series",
            "playHref": f"/series/{item.series_key}",
            "year": item.year,
            "overview": (getattr(item, "overview", "") or "")[:_OVERVIEW_CAP],
        }
    if item.movie_key:
        return {
            "title": item.title,
            "kind": "movie",
            "playHref": f"/movie/{item.movie_key}",
            "year": item.year,
            "overview": (getattr(item, "overview", "") or "")[:_OVERVIEW_CAP],
        }
    if getattr(item, "album_key", ""):
        return {
            "title": getattr(item, "album_title", "") or item.title,
            "kind": "album",
            "playHref": f"/album/{item.album_key}",
            "artist": getattr(item, "artist", "") or "",
            "year": item.year,
        }
    if (getattr(item, "media_kind", "") or "") == "book":
        return {
            "title": item.title,
            "kind": "book",
            "playHref": f"/books",
            "authors": list(getattr(item, "book_authors", None) or [])[:3],
            "overview": (getattr(item, "overview", "") or "")[:_OVERVIEW_CAP],
        }
    return {
        "title": item.title,
        "kind": "audio",
        "playHref": f"/play/{item.message_id}",
        "artist": getattr(item, "artist", "") or "",
        "year": item.year,
    }


def search_catalogue(query: str, kind: str = "") -> dict:
    """Search the catalogue, collapsing series/movies to one row each."""
    results = media_index.suggest((query or "").strip(), limit=_MAX_RESULTS + 4)
    if kind:
        wanted = kind.strip().lower()
        if wanted == "audio":
            wanted = "audio"
        results = [r for r in results if r.get("kind") == wanted]
    return {"results": results[:_MAX_RESULTS]}


def title_details(play_href: str) -> dict:
    """Details for one catalogue group: overview, cast, episodes, watch state.

    ``play_href`` is the model echo of a server-issued /series|movie|play
    href; anything that doesn't parse is 'unknown' rather than an error so
    the model can recover conversationally.
    """
    href = str(play_href or "").split("?", 1)[0].strip("/")
    parts = href.split("/", 1)
    if len(parts) != 2 or parts[0] not in {"series", "movie", "album", "book", "play"}:
        return {"error": "unknown title — pass the playHref from search_catalogue"}
    key = parts[1]
    if parts[0] == "album":
        tracks = media_index.tracks_for_album(key)
        if not tracks:
            return {"error": "unknown title"}
        first = tracks[0]
        return {
            "title": getattr(first, "album_title", "") or first.title,
            "kind": "album",
            "artist": getattr(first, "artist", "") or "",
            "year": first.year,
            "trackCount": len(tracks),
            "tracks": [t.title for t in tracks[:_MAX_RESULTS]],
        }
    if parts[0] == "book":
        for item in media_index._items.values():
            if (getattr(item, "media_kind", "") or "") == "book" \
                    and str(getattr(item, "book_source_key", "") or "") == key:
                return {
                    "title": item.title,
                    "kind": "book",
                    "authors": list(getattr(item, "book_authors", None) or [])[:3],
                    "pageCount": getattr(item, "book_page_count", 0) or 0,
                    "language": getattr(item, "book_language", "") or "",
                    "subjects": list(getattr(item, "book_subjects", None) or [])[:6],
                    "overview": (getattr(item, "overview", "") or "")[:_OVERVIEW_CAP],
                }
        return {"error": "unknown title"}
    if parts[0] == "series":
        episodes = media_index.episodes_for_series(key)
        if not episodes:
            return {"error": "unknown title"}
        first = episodes[0]
        return {
            "title": first.series_title or first.title,
            "kind": "series",
            "year": first.year,
            "overview": (getattr(first, "overview", "") or "")[:_OVERVIEW_CAP],
            "genres": list(getattr(first, "tmdb_genres", None) or first.tags or [])[:8],
            "cast": [str(c) for c in (getattr(first, "cast", None) or [])[:5]],
            "episodeCount": len({(e.season, e.episode) for e in episodes if e.episode is not None}) or len(episodes),
        }
    if parts[0] == "movie":
        variants = media_index.variants_for_movie(key)
        if not variants:
            return {"error": "unknown title"}
        item = variants[0]
        return {
            "title": item.title,
            "kind": "movie",
            "year": item.year,
            "overview": (getattr(item, "overview", "") or "")[:_OVERVIEW_CAP],
            "genres": list(getattr(item, "tmdb_genres", None) or item.tags or [])[:8],
            "cast": [str(c) for c in (getattr(item, "cast", None) or [])[:5]],
            "runtimeMinutes": getattr(item, "tmdb_runtime_minutes", 0) or 0,
        }
    try:
        item = media_index.get_item(int(key))
    except (TypeError, ValueError):
        item = None
    if item is None:
        return {"error": "unknown title"}
    return _suggestion_row(item)


async def my_taste(user_id: int) -> dict:
    """The user's taste profile — the same cached signals AI Picks ranks on.

    Exposes only summarisable preference data (genres, directors, keywords,
    counts); never raw ids, so a prompt-injected reply still can't leak
    anything the user hasn't already watched in-app.
    """
    from main.utils import rec_engine
    try:
        profile = await rec_engine._collect_signal_profile(user_id)
    except Exception:
        logging.exception("buddy_tools: my_taste profile failed uid=%d", user_id)
        return {"error": "profile unavailable"}

    def top(counter, n=6):
        return [name for name, _ in counter.most_common(n) if name]

    return {
        "favouriteGenres": top(profile.get("seed_genres") or Counter()),
        "favouriteDirectors": top(profile.get("seed_directors") or Counter(), 4),
        "favouriteKeywords": top(profile.get("seed_keywords") or Counter(), 8),
        "genresToAvoid": top(profile.get("negative_genres") or Counter(), 4),
        "likedCount": len(profile.get("liked_tmdb") or ()),
        "dislikedCount": len(profile.get("disliked_tmdb") or ()),
        "partwayThroughCount": len(profile.get("partial_tmdb") or ()),
        "inLibraryCount": len(profile.get("exclude_tmdb") or ()),
    }


async def where_was_i(user_id: int) -> dict:
    """The user's live position across media, from the trusted stores."""
    in_progress: list[dict] = []
    try:
        cw = await cw_store.get_all(user_id)
        for key, entry in (cw or {}).items():
            item = _item_for_cw_key(key)
            if item is None:
                continue
            row = _suggestion_row(item)
            row["progress"] = round(min(1.0, max(0.0, _ratio(entry))), 3)
            in_progress.append(row)
            if len(in_progress) >= _MAX_RESULTS:
                break
    except Exception:
        logging.exception("buddy_tools: where_was_i cw lookup failed uid=%d", user_id)
    recent: list[dict] = []
    try:
        wh = await wh_store.get_recent(user_id, limit=_MAX_RESULTS)
        for entry in wh or []:
            item = _item_for_cw_key(entry.get("cw_key", ""))
            if item is None:
                continue
            recent.append(_suggestion_row(item))
    except Exception:
        logging.exception("buddy_tools: where_was_i wh lookup failed uid=%d", user_id)
    books: list[dict] = []
    try:
        from main.utils import book_progress_store
        progress = await book_progress_store.get_all(user_id)
        for book_id, entry in (progress or {}).items():
            item = next((it for it in media_index._items.values()
                         if (getattr(it, "media_kind", "") or "") == "book"
                         and str(it.message_id) == str(book_id)), None)
            if item is None:
                continue
            row = _suggestion_row(item)
            row["progress"] = round(min(1.0, max(0.0, float(entry.get("progress", 0) or 0))), 3)
            books.append(row)
            if len(books) >= _MAX_RESULTS:
                break
    except Exception:
        logging.exception("buddy_tools: where_was_i book lookup failed uid=%d", user_id)
    return {"inProgress": in_progress, "recentlyFinished": recent, "reading": books}


def execute(name: str, args: dict, user_id: int) -> dict:
    """Run one tool call server-side. Async tools are not exposed to this
    dispatcher's sync callers; where_was_i is awaited by the route directly."""
    args = args or {}
    if name == "search_catalogue":
        return search_catalogue(str(args.get("query", "")), str(args.get("kind", "") or ""))
    if name == "title_details":
        return title_details(str(args.get("playHref", "")))
    return {"error": f"unknown tool {name}"}
