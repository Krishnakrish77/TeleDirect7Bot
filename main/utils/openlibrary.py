"""Small, bounded Open Library client used by the admin book workflow."""

from __future__ import annotations

import re
from typing import Any

import aiohttp


_SEARCH_URL = "https://openlibrary.org/search.json"
_WORK_KEY_RE = re.compile(r"^/works/OL\d+W$")
_TIMEOUT = aiohttp.ClientTimeout(total=10)
_FIELDS = ",".join((
    "key", "title", "author_name", "first_publish_year", "cover_i", "isbn",
    "publisher", "language", "number_of_pages_median", "first_sentence", "subject",
))


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _strings(value: Any, limit: int = 8, item_limit: int = 160) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        cleaned = _text(item, item_limit)
        if cleaned and cleaned not in values:
            values.append(cleaned)
        if len(values) >= limit:
            break
    return values


def normalise_search_doc(doc: dict[str, Any] | Any) -> dict[str, Any] | None:
    """Return only the fields that the admin UI may submit back to us."""
    if not isinstance(doc, dict):
        return None
    key = _text(doc.get("key"), 40)
    title = _text(doc.get("title"))
    if not _WORK_KEY_RE.fullmatch(key) or not title:
        return None
    cover_id = doc.get("cover_i", doc.get("coverId"))
    try:
        cover_id = int(cover_id) if cover_id else 0
    except (TypeError, ValueError):
        cover_id = 0
    pages = doc.get("number_of_pages_median", doc.get("pageCount"))
    try:
        pages = max(0, min(100_000, int(pages or 0)))
    except (TypeError, ValueError):
        pages = 0
    year = doc.get("first_publish_year", doc.get("year"))
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    return {
        "key": key,
        "title": title,
        "authors": _strings(doc.get("author_name", doc.get("authors")), limit=6),
        "year": year if year and 1000 <= year <= 2100 else None,
        "coverId": cover_id,
        "isbn": (_strings(doc.get("isbn") if isinstance(doc.get("isbn"), list) else [doc.get("isbn")], limit=1, item_limit=32) or [""])[0],
        "publisher": (_strings(doc.get("publisher") if isinstance(doc.get("publisher"), list) else [doc.get("publisher")], limit=1) or [""])[0],
        "language": (_strings(doc.get("language") if isinstance(doc.get("language"), list) else [doc.get("language")], limit=1, item_limit=20) or [""])[0],
        "pageCount": pages,
        "description": _text(doc.get("first_sentence", doc.get("description")), 1200),
        "subjects": _strings(doc.get("subject", doc.get("subjects")), limit=8, item_limit=80),
    }


async def search_books(query: str, limit: int = 8) -> list[dict[str, Any]]:
    query = _text(query, 250)
    if not query:
        return []
    params = {"q": query, "limit": str(max(1, min(limit, 12))), "fields": _FIELDS}
    headers = {"User-Agent": "TeleDirect/1.0 (admin book metadata)"}
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=headers) as session:
        async with session.get(_SEARCH_URL, params=params) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
    hits: list[dict[str, Any]] = []
    for doc in data.get("docs", []) if isinstance(data, dict) else []:
        if isinstance(doc, dict):
            hit = normalise_search_doc(doc)
            if hit:
                hits.append(hit)
    return hits


def cover_url(cover_id: int) -> str:
    return f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id > 0 else ""
