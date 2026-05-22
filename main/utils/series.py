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
import unicodedata
from dataclasses import dataclass
from typing import Optional


# Ordered most-specific first; the first match wins.
_PATTERNS = [
    # S01E03 / s1e3 / S16 EP351 with optional dot/space/dash separators
    # between the season and episode tokens, and an optional ``P``
    # after the episode marker (``EP`` is common in anime releases).
    # Optional episode-end for multi-episode files:
    #   S01E01-E02  (dash + E prefix)
    #   S01E01-02   (dash, no prefix)
    #   S01E01E02   (concatenated, no separator — the E acts as delimiter)
    # Triple-episode ranges (S01E01-E03) are supported; only the first
    # end is captured (S01E01-E02-E03 → episode=1, episode_end=2).
    re.compile(
        r"^(?P<title>.+?)[\s._\-]+s(?P<season>\d{1,2})[\s._\-]*ep?(?P<episode>\d{1,4})"
        r"(?:[-E]e?(?P<episode_end>\d{1,4}))?\b",
        re.IGNORECASE,
    ),
    # 1x03 / 01x003 (episode range not common in this format, skipped)
    re.compile(
        r"^(?P<title>.+?)[\s._\-]+(?P<season>\d{1,2})x(?P<episode>\d{1,3})\b",
        re.IGNORECASE,
    ),
    # Season 1 Episode 3 (spelled out)
    re.compile(
        r"^(?P<title>.+?)[\s._\-]+season\s*(?P<season>\d{1,2})[\s._\-]+episode\s*(?P<episode>\d{1,3})\b",
        re.IGNORECASE,
    ),
    # Daily-aired serial format: ``<EpNum><Title> MM-DD-YY``
    # (e.g. ``61Mahabharatham 01-02-14``). Common for Tamil/Indian
    # TV serials where each upload is a single day's episode. The
    # leading number is the episode counter, the trailing piece
    # is the air date. Season isn't expressed; we synthesise it
    # from the date's year for stable grouping per-season.
    # Episode number is optional (some uploads skip the prefix).
    re.compile(
        r"^(?P<episode>\d{1,4})?\s*"
        r"(?P<title>[A-Za-z][A-Za-z\s.]+?)\s+"
        r"(?P<m>\d{1,2})[-_/\s](?P<d>\d{1,2})[-_/\s](?P<y>\d{2,4})\s*$",
        re.IGNORECASE,
    ),
]

# Patterns above that don't carry a season group — parse() synthesises
# season=1 (or a date-derived value) for these so SeriesMatch.season
# remains an int.
_DAILY_PATTERN_INDEX = 3

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
    # The unprocessed title portion captured by the regex, with separators
    # normalised but case untouched. Callers that want to run a further
    # cleaner (e.g. dedup.clean_for_search) need the original casing so
    # heuristics like "leading lowercase word = channel handle" still fire
    # — the humanised ``title`` field has already capitalised everything.
    raw_title: str = ""
    # Last episode in a range for multi-episode files (e.g. S01E01-E03).
    # None for normal single-episode entries.
    episode_end: Optional[int] = None


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
    for idx, pat in enumerate(_PATTERNS):
        m = pat.match(candidate)
        if not m:
            continue
        raw_title = m.group("title")
        # Preserve original casing in raw_title — humanise capitalises
        # leading lowercase words, which downstream cleaners rely on to
        # detect channel-handle prefixes.
        #
        # Strip ``@<handle>`` BEFORE underscore normalisation so multi-
        # token handles like ``@Filmbox_Studios`` get consumed whole.
        # If we ran ``[._]+ → ' '`` first, the handle would split into
        # ``@Filmbox Studios`` and only ``@Filmbox`` would match the
        # cleaner's @-strip — ``Studios`` would leak into the title.
        raw_no_handle = re.sub(r"^\s*@[\w_]+\s*", "", raw_title)
        raw_separator_normalised = re.sub(r"[._]+", " ", raw_no_handle).strip()
        title = _humanise_title(raw_no_handle)
        if not title:
            continue
        # Daily-aired serial pattern doesn't carry an explicit season —
        # synthesise one from the air-date year so each broadcast year
        # is its own season bucket. Fallback to season=1 when no date
        # group is present.
        if idx == _DAILY_PATTERN_INDEX:
            year_grp = m.groupdict().get("y") or ""
            try:
                year_num = int(year_grp)
                # 2-digit years → assume 20xx (this format is post-2000
                # daily TV; older serials don't usually surface here).
                synthetic_season = year_num if year_num >= 100 else 2000 + year_num
            except ValueError:
                synthetic_season = 1
            ep_grp = m.groupdict().get("episode") or "0"
            try:
                episode_num = int(ep_grp)
            except ValueError:
                episode_num = 0
            return SeriesMatch(
                title=title,
                season=synthetic_season,
                episode=episode_num,
                key=slugify(title),
                raw_title=raw_separator_normalised,
            )
        ep_end_grp = m.groupdict().get("episode_end")
        ep_end = int(ep_end_grp) if ep_end_grp else None
        # Discard episode_end when it's not actually higher than episode
        # (e.g. a false-positive capture like S01E12-720p matching -72).
        ep_start = int(m.group("episode"))
        if ep_end is not None and ep_end <= ep_start:
            ep_end = None
        return SeriesMatch(
            title=title,
            season=int(m.group("season")),
            episode=ep_start,
            episode_end=ep_end,
            key=slugify(title),
            raw_title=raw_separator_normalised,
        )
    return None


def slugify(text: str) -> str:
    """Lowercase, alphanumeric, dash-separated. ``The Office (US)`` → ``the-office-us``.

    Unicode letters with diacritics/macrons (e.g. ū, ō, ā common in
    romanized Japanese titles) are decomposed to their ASCII base before
    stripping so ``Shippūden`` → ``shippuden`` not ``shipp-den``.
    """
    if not text:
        return ""
    # NFKD decomposition splits combined chars (ū → u + combining macron);
    # encoding to ASCII with 'ignore' then drops the combining marks.
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _SLUG_RE.sub("-", ascii_text.lower()).strip("-")


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
