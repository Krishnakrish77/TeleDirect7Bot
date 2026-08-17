"""Bounded Google Books metadata lookup for the admin book workflow."""

from __future__ import annotations

import re
from typing import Any

import aiohttp

from main.vars import Var


_SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"
_TIMEOUT = aiohttp.ClientTimeout(total=5, sock_connect=2, sock_read=4)
_VOLUME_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


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


def normalise_volume(volume: dict[str, Any] | Any) -> dict[str, Any] | None:
    if not isinstance(volume, dict):
        return None
    volume_id = _text(volume.get("id"), 64)
    info = volume.get("volumeInfo")
    if not _VOLUME_ID_RE.fullmatch(volume_id) or not isinstance(info, dict):
        return None
    title = _text(info.get("title"))
    if not title:
        return None
    year_match = re.match(r"^(\d{4})", _text(info.get("publishedDate"), 32))
    identifiers = info.get("industryIdentifiers") if isinstance(info.get("industryIdentifiers"), list) else []
    isbn = next((_text(entry.get("identifier"), 32) for entry in identifiers if isinstance(entry, dict) and entry.get("type") in {"ISBN_13", "ISBN_10"}), "")
    images = info.get("imageLinks") if isinstance(info.get("imageLinks"), dict) else {}
    return {
        "key": f"google:{volume_id}", "source": "Google Books", "title": title,
        "authors": _strings(info.get("authors"), limit=6),
        "year": int(year_match.group(1)) if year_match and 1000 <= int(year_match.group(1)) <= 2100 else None,
        "coverId": 0, "coverUrl": f"google-books:{volume_id}" if images else "",
        "isbn": isbn, "publisher": _text(info.get("publisher")), "language": _text(info.get("language"), 20),
        "pageCount": max(0, min(100_000, int(info.get("pageCount") or 0))) if str(info.get("pageCount") or "").isdigit() else 0,
        "description": _text(info.get("description"), 1200), "subjects": _strings(info.get("categories"), limit=8, item_limit=80),
    }


async def search_books(query: str, limit: int = 8) -> list[dict[str, Any]]:
    if not Var.GOOGLE_BOOKS_API_KEY:
        return []
    params = {"q": _text(query, 250), "maxResults": str(max(1, min(limit, 12))), "printType": "books", "key": Var.GOOGLE_BOOKS_API_KEY}
    if not params["q"]:
        return []
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.get(_SEARCH_URL, params=params) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
    return [candidate for volume in data.get("items", []) if (candidate := normalise_volume(volume))]


def normalise_search_doc(doc: dict[str, Any] | Any) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return None
    key = _text(doc.get("key"), 80)
    volume_id = key.removeprefix("google:")
    if key == volume_id or not _VOLUME_ID_RE.fullmatch(volume_id):
        return None
    return normalise_volume({"id": volume_id, "volumeInfo": {
        "title": doc.get("title"), "authors": doc.get("authors"), "publishedDate": doc.get("year"), "publisher": doc.get("publisher"),
        "language": doc.get("language"), "pageCount": doc.get("pageCount"), "description": doc.get("description"), "categories": doc.get("subjects"),
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": doc.get("isbn")}],
        "imageLinks": {"thumbnail": "present"} if doc.get("coverUrl") == f"google-books:{volume_id}" else {},
    }})
