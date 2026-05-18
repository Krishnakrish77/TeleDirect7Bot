"""Series/episode parsing for the hub.

Catalogue entries are individual videos, but when they belong to a TV
series we want to surface them as a single collapsible group on the hub
landing page. This module extracts ``(series_title, season, episode)``
from a filename or caption title.

The parser recognises the common release-style patterns:

    Show.Name.S01E03.1080p.WEB-DL    → ("Show Name", 1, 3)
    Show Name - S01E03 - The Pilot   → ("Show Name", 1, 3)
    Show.Name.1x03                    → ("Show Name", 1, 3)
    Show Name Season 1 Episode 3     → ("Show Name", 1, 3)

A returned ``series_key`` is a slug suitable for URLs (``the-office``).
``None`` is returned for titles that don't match any pattern, so movies and
standalone uploads stay as individual hub cards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Ordered most-specific first; the first match wins.
_PATTERNS = [
    # S01E03 / s1e3 with optional dot/space/dash separators before/after
    re.compile(
        r"^(?P<title>.+?)[\s._\-]+s(?P<season>\d{1,2})\s*e(?P<episode>\d{1,3})\b",
        re.IGNORECASE,
    ),
    # 1x03 / 01x003
    re.compile(
        r"^(?P<title>.+?)[\s._\-]+(?P<season>\d{1,2})x(?P<episode>\d{1,3})\b",
        re.IGNORECASE,
    ),
    # Season 1 Episode 3 (spelled out)
    re.compile(
        r"^(?P<title>.+?)[\s._\-]+season\s*(?P<season>\d{1,2})[\s._\-]+episode\s*(?P<episode>\d{1,3})\b",
        re.IGNORECASE,
    ),
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class SeriesMatch:
    title: str
    season: int
    episode: int
    key: str


def parse(text: str) -> Optional[SeriesMatch]:
    """Return a SeriesMatch if ``text`` looks like an episode title.

    The detection is intentionally strict: ambiguous things like a bare
    ``S2`` or ``2x`` without an episode component are ignored, so a movie
    titled ``X-Men`` isn't misclassified as a series.
    """
    if not text:
        return None
    # Normalise common release separators to spaces for the title portion.
    candidate = text.strip()
    for pat in _PATTERNS:
        m = pat.match(candidate)
        if not m:
            continue
        raw_title = m.group("title")
        title = _humanise_title(raw_title)
        if not title:
            continue
        return SeriesMatch(
            title=title,
            season=int(m.group("season")),
            episode=int(m.group("episode")),
            key=slugify(title),
        )
    return None


def slugify(text: str) -> str:
    """Lowercase, alphanumeric, dash-separated. ``The Office (US)`` → ``the-office-us``."""
    if not text:
        return ""
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def _humanise_title(raw: str) -> str:
    """Turn ``Show.Name`` or ``show_name`` into ``Show Name``.

    Release filenames almost universally use ``.`` / ``_`` as word
    separators; ``-`` is sometimes used as a separator and sometimes as
    part of the title itself (``X-Men``), so leave hyphens alone except at
    the boundaries.
    """
    if not raw:
        return ""
    # Strip wrapping brackets that some scene names ship with.
    text = raw.strip().strip("[]()")
    # Normalize separators.
    text = re.sub(r"[._]+", " ", text)
    # Collapse runs of whitespace.
    text = re.sub(r"\s+", " ", text).strip(" -_")
    if not text:
        return ""
    # Title-case words that look all-lowercase or all-uppercase. Preserve
    # mixed-case tokens like "iPod" or scene tags.
    parts = []
    for word in text.split(" "):
        if word and (word.islower() or word.isupper()):
            parts.append(word.capitalize())
        else:
            parts.append(word)
    return " ".join(parts)
