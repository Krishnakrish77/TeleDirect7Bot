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

# Looser episode-number patterns used when a filename lacks the SxxEyy
# pattern but the catalogue already knows the upload is part of a series
# (via TMDB tmdb_kind=tv or an existing series_key). Strict prefixes
# first; bare-number fallback runs only when nothing else matches and
# the candidate number is plausibly an episode (1-99).
_LOOSE_EPISODE_PATTERNS = [
    re.compile(r"\b(?:episode|ep)[\s._-]*(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\be(\d{1,3})\b", re.IGNORECASE),
]
_NUMERIC_TOKEN_RE = re.compile(r"(?<![\d.])(\d{1,2})(?!\d)")
# Tokens that look numeric but are definitely NOT an episode locator.
_FALSE_POSITIVE_NUMBERS = {
    "264", "265", "720", "1080", "480", "360", "2160", "240", "5", "1",  # codecs/res
}


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


def infer_episode_loose(filename: str) -> Optional[int]:
    """Best-effort episode-number extraction from a filename that lacks a
    full SxxEyy pattern.

    Only call this when the catalogue already knows the upload is an
    episode (TMDB says TV, or a sibling has SxxEyy). Tries explicit
    ``Episode N`` / ``EpN`` / ``ENN`` markers first; if none match,
    falls back to the first plausibly-episode-sized bare integer in the
    name. Returns None if no candidate looks credible.
    """
    if not filename:
        return None
    # Normalise separators so word boundaries fire correctly.
    text = re.sub(r"[._]+", " ", filename)

    for pat in _LOOSE_EPISODE_PATTERNS:
        m = pat.search(text)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 999:
                return n

    # Bare-number fallback. Drop anything inside parens/brackets first —
    # year-in-parens fragments shouldn't seed false matches.
    cleaned = re.sub(r"[\[\(].*?[\]\)]", " ", text)
    candidates = [
        int(m.group(1))
        for m in _NUMERIC_TOKEN_RE.finditer(cleaned)
        if m.group(1) not in _FALSE_POSITIVE_NUMBERS
    ]
    # Filter to plausibly-episode-sized values. Last numeric token in
    # the filename is often the episode (after the series title), so
    # prefer that.
    plausible = [n for n in candidates if 1 <= n <= 99]
    return plausible[-1] if plausible else None


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
