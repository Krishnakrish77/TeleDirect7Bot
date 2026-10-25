"""Bounded Open Library metadata lookup for book enrichment.

Open Library is a catalogue (like TMDB for films), not a storefront: it
has no API key, generous rate limits, and stable work/edition IDs. Used
as the second enrichment provider for books — after embedded file
metadata, before/alongside Google Books (which needs a key and only
covers titles in its store).

The normalised candidate shape mirrors ``main/utils/google_books.py`` so
the admin manual-match flow and the enrichment sweep can treat both
providers interchangeably.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp


_SEARCH_URL = "https://openlibrary.org/search.json"
_ISBN_URL = "https://openlibrary.org/isbn/{isbn}.json"
_TIMEOUT = aiohttp.ClientTimeout(total=8, sock_connect=3, sock_read=6)
_OL_ID_RE = re.compile(r"^OL[A-Za-z0-9]{2,32}(?:W|M|X)$")  # works …W, editions …M
_COVER_ID_RE = re.compile(r"^\d{1,12}$")
_ISBN_RE = re.compile(r"^(?:97[89][- ]?)?\d{1,5}[- ]?\d+[- ]?\d+[- ]?[\dXx]$")


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


def clean_isbn(value: str) -> str:
    """Normalise a raw ISBN-10/13 to bare digits (or digits + X)."""
    isbn = re.sub(r"[^0-9Xx]", "", str(value or ""))
    if len(isbn) in (10, 13):
        return isbn.upper()
    return ""


def looks_like_isbn(value: str) -> bool:
    return bool(_ISBN_RE.fullmatch(str(value or "").strip()))


def _year_from(value: Any) -> int | None:
    m = re.match(r"^(\d{4})", _text(value, 32))
    year = int(m.group(1)) if m else 0
    return year if 1000 <= year <= 2100 else None


def normalise_doc(doc: dict[str, Any] | Any) -> dict[str, Any] | None:
    """Normalise an Open Library work/edition doc into a book candidate.

    Works with the search endpoint's flat docs (``author_name``,
    ``cover_i``) and the editions endpoint's nested shape (``authors``
    as key dicts, ``covers`` as id list).
    """
    if not isinstance(doc, dict):
        return None
    work_key = _text(doc.get("key"), 64)  # search docs: /works/OL…W
    work_id = work_key.rsplit("/", 1)[-1] if work_key else ""
    if not _OL_ID_RE.fullmatch(work_id):
        return None
    title = _text(doc.get("title"))
    if not title:
        return None
    authors = _strings(doc.get("author_name") or doc.get("authors"), limit=6)
    cover_id = ""
    cover_raw = doc.get("cover_i") or (doc.get("covers")[0] if isinstance(doc.get("covers"), list) and doc["covers"] else None)
    if cover_raw is not None and _COVER_ID_RE.fullmatch(str(cover_raw)):
        cover_id = str(cover_raw)
    isbn = ""
    raw_isbns = doc.get("isbn") or doc.get("isbn_13") or []
    if isinstance(raw_isbns, list):
        # Prefer ISBN-13 (longer), fall back to whatever parses.
        parsed = [clean_isbn(str(v)) for v in raw_isbns]
        parsed = [v for v in parsed if v]
        isbn = max(parsed, key=len) if parsed else ""
    elif isinstance(raw_isbns, str):
        isbn = clean_isbn(raw_isbns)
    publishers = _strings(doc.get("publisher"), limit=1)
    languages = _strings(doc.get("language"), limit=1, item_limit=20)
    return {
        "key": f"openlibrary:{work_id}",
        "source": "Open Library",
        "title": title,
        "authors": authors,
        "year": _year_from(doc.get("first_publish_year") or doc.get("publish_date")),
        "coverId": int(cover_id) if cover_id else 0,
        "coverUrl": f"openlibrary-cover:{cover_id}" if cover_id else "",
        "isbn": isbn,
        "publisher": publishers[0] if publishers else "",
        "language": languages[0] if languages else "",
        "pageCount": max(0, min(100_000, int(doc.get("number_of_pages_median") or doc.get("number_of_pages") or 0))) if str(doc.get("number_of_pages_median") or doc.get("number_of_pages") or "").isdigit() else 0,
        "description": _text(doc.get("first_sentence"), 1200) if isinstance(doc.get("first_sentence"), str) else _text((doc.get("first_sentence") or {}).get("value"), 1200) if isinstance(doc.get("first_sentence"), dict) else "",
        "subjects": _strings(doc.get("subject"), limit=8, item_limit=80),
    }


def normalise_search_doc(doc: dict[str, Any] | Any) -> dict[str, Any] | None:
    """Round-trip a candidate dict (e.g. from the admin UI) back through
    normalise_doc — guards the manual-match endpoint against tampering.
    Accepts the ``openlibrary:`` key shape produced by normalise_doc.
    """
    if not isinstance(doc, dict):
        return None
    key = _text(doc.get("key"), 80)
    work_id = key.removeprefix("openlibrary:")
    if key == work_id or not _OL_ID_RE.fullmatch(work_id):
        return None
    cover_url = _text(doc.get("coverUrl"), 80)
    cover_id = cover_url.removeprefix("openlibrary-cover:")
    return normalise_doc({
        "key": f"/works/{work_id}",
        "title": doc.get("title"),
        "author_name": doc.get("authors"),
        "first_publish_year": doc.get("year"),
        "cover_i": cover_id if _COVER_ID_RE.fullmatch(cover_id) else None,
        "isbn": [doc.get("isbn")] if doc.get("isbn") else [],
        "publisher": [doc.get("publisher")] if doc.get("publisher") else [],
        "language": [doc.get("language")] if doc.get("language") else [],
        "number_of_pages_median": doc.get("pageCount"),
        "first_sentence": doc.get("description"),
        "subject": doc.get("subjects"),
    })


async def search_books(query: str, limit: int = 8) -> list[dict[str, Any]]:
    query = _text(query, 250)
    if len(query) < 2:
        return []
    params = {"q": query, "limit": str(max(1, min(limit, 12))),
              "fields": "key,title,author_name,first_publish_year,cover_i,isbn,publisher,language,number_of_pages_median,first_sentence,subject"}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(_SEARCH_URL, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError):
        logging.warning("open_library: search unavailable (%s)", "network error")
        return []
    return [candidate for doc in data.get("docs", []) if (candidate := normalise_doc(doc))]


async def lookup_isbn(isbn: str) -> dict[str, Any] | None:
    """Exact edition lookup by ISBN. Returns a candidate whose author
    names are resolved, or None when the ISBN is unknown/unparseable."""
    clean = clean_isbn(isbn)
    if not clean:
        return None
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(_ISBN_URL.format(isbn=clean)) as response:
                if response.status == 404:
                    return None
                response.raise_for_status()
                doc = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError):
        logging.warning("open_library: isbn lookup unavailable (%s)", "network error")
        return None
    if not isinstance(doc, dict):
        return None
    # Edition docs lack author names; resolve them so candidates always
    # carry displayable authors.
    authors: list[str] = []
    for ref in (doc.get("authors") or [])[:6]:
        author_key = ""
        if isinstance(ref, dict):
            author_key = _text(ref.get("key"), 64)
        elif isinstance(ref, str):
            author_key = ref
        author_key = author_key.rsplit("/", 1)[-1]
        if author_key and _OL_ID_RE.fullmatch(author_key):
            name = await _author_name(author_key)
            if name:
                authors.append(name)
    work_key = ""
    for ref in (doc.get("works") or [])[:1]:
        if isinstance(ref, dict):
            work_key = _text(ref.get("key"), 64)
    work_id = work_key.rsplit("/", 1)[-1] if work_key else ""
    if not work_id:
        # Edition without a work record can't form a stable candidate key.
        return None
    covers = doc.get("covers") if isinstance(doc.get("covers"), list) else []
    cover_id = ""
    for raw in covers:
        if _COVER_ID_RE.fullmatch(str(raw)):
            cover_id = str(raw)
            break
    return {
        "key": f"openlibrary:{work_id}",
        "source": "Open Library",
        "title": _text(doc.get("title")),
        "authors": authors,
        "year": _year_from(doc.get("publish_date")),
        "coverId": int(cover_id) if cover_id else 0,
        "coverUrl": f"openlibrary-cover:{cover_id}" if cover_id else "",
        "isbn": clean,
        "publisher": (_strings(doc.get("publishers"), limit=1) or [""])[0],
        "language": "",
        "pageCount": max(0, min(100_000, int(doc.get("number_of_pages") or 0))) if str(doc.get("number_of_pages") or "").isdigit() else 0,
        "description": "",
        "subjects": [],
    }


async def _author_name(author_key: str) -> str:
    url = f"https://openlibrary.org/authors/{author_key}.json"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    return ""
                doc = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError):
        return ""
    return _text(doc.get("name"), 160) if isinstance(doc, dict) else ""


async def fetch_work_details(work_id: str) -> dict[str, Any] | None:
    """Fill description/subjects for an already-matched work id."""
    if not _OL_ID_RE.fullmatch(work_id):
        return None
    url = f"https://openlibrary.org/works/{work_id}.json"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    return None
                doc = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError):
        return None
    if not isinstance(doc, dict):
        return None
    description = doc.get("description")
    if isinstance(description, dict):
        description = description.get("value")
    return {
        "description": _text(description, 1200),
        "subjects": _strings(doc.get("subjects"), limit=8, item_limit=80),
    }
