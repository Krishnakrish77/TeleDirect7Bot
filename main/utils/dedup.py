"""Identify same-movie uploads under different filenames.

Two scene releases of "Samsaram Adhu Minsaram (1986)" might land in
BIN_CHANNEL as ``@Team_HDT - Samsaram.Adhu.Minsaram.1986.Tamil.720p`` and
``Samsaram_Adhu_Minsaram_1986Tamil`` — same film, different uploaders.
``movie_key`` collapses both to ``samsaram-adhu-minsaram::1986`` so the
hub can show a single card per movie with a "pick a variant" page behind
it.

Series episodes have their own grouping (see ``main.utils.series``); this
module deliberately returns "" for any title that ``series.parse`` matches
so we never collapse two different episodes into a movie group.
"""

from __future__ import annotations

import re
from typing import Optional

from main.utils import series


# Tokens that always appear in scene/release filenames and never in the
# actual film title. Stripping them tightens key matches between very
# different-looking filenames of the same movie. Anchored on word
# boundaries so "Hindi" in a title isn't taken for the language tag.
_NOISE_TOKENS = re.compile(
    r"\b("
    r"2160p|1080p|720p|480p|360p|240p|4k|uhd|fhd|hd|sd|"
    r"hdrip|webrip|web[-]?dl|brrip|bluray|bdrip|dvdrip|hdtv|"
    r"x264|x265|h264|h265|hevc|avc|10bit|8bit|"
    r"aac|ac3|eac3|dts|ddp|2ch|"
    r"esub|esubs|dual[-]?audio|multi|"
    r"tamil|english|hindi|malayalam|telugu|kannada|"
    r"untouched|proper|repack|extended|directors?|cut|"
    r"hq|sunnxt|amzn|dsnp|nf|"
    # Container extensions — appear as standalone words after the
    # dot-to-space normalization step.
    r"mkv|mp4|avi|mov|m4v|wmv|flv|webm|mpg|mpeg|ts"
    r")\b",
    re.IGNORECASE,
)

# Channel handle tokens like ``@T4TVSeries`` or ``@MoviesShop`` appearing
# anywhere in the filename. Auto-indexer carries them through from the
# uploader's filename if there's no clear separator anchoring them to
# the start.
_INLINE_HANDLE_RE = re.compile(r"@\w+")

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def clean_for_search(title: str, file_name: str = "") -> str:
    """Reduce a filename-shaped title to a clean human-readable query.

    Like ``movie_key`` but keeps the result as a spaced string instead of
    a slug — TMDB's search endpoint expects natural language. Strips
    leading channel tags, the trailing year + release flags, and runs
    of dots/underscores. Returns the title in its original case so TMDB
    can score the candidates correctly.
    """
    candidate = title or file_name
    if not candidate:
        return ""

    text = re.sub(r"[._]+", " ", candidate)
    text = re.sub(r"(\d{4,})([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])(\d{4,})", r"\1 \2", text)

    m = _YEAR_RE.search(text)
    if m:
        text = text[: m.start()]

    # Strip leading channel/group prefixes (same patterns movie_key uses).
    for _ in range(4):
        new = re.sub(r"^\s*\[\w+\]\s*[-·:]?\s*", "", text)
        new = re.sub(r"^\s*@\w+(?:\s+\w+)*?\s+[-·:]\s+", "", new)
        if new == text:
            break
        text = new

    # Fallback for @-prefixes without a separator (``@CC The Perks``,
    # ``@UnixLinks Jana Nayagan``) — strip just the first @-word so we
    # don't accidentally eat the whole title.
    text = re.sub(r"^\s*@\w+\s+", "", text)

    # ``www TamilBlasters buzz Hey Sinamika`` — drop the www and the
    # uploader-domain words that follow it. Pattern: ``www`` followed by
    # a few alnum tokens, then ``buzz`` / ``net`` / ``com`` / etc.
    text = re.sub(
        r"^\s*www\s+\S+(?:\s+(?:buzz|net|com|org|info|io|cc))?\s+",
        "", text, flags=re.IGNORECASE,
    )

    # Strip @channelname tokens wherever they appear. Earlier passes
    # only caught leading @-prefixes; trailing ones like
    # ``Good Witch S07E02 @T4TVSeries mkv`` slipped through.
    text = _INLINE_HANDLE_RE.sub(" ", text)

    # Strip the release-noise tokens but leave the rest of the title's
    # casing/punctuation intact for natural-language search.
    text = _NOISE_TOKENS.sub(" ", text)

    # Drop anything inside lingering brackets — usually noise the year-cut
    # didn't catch.
    text = re.sub(r"[\[\(].*?[\]\)]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -—·:()[]")
    return text


def movie_key(title: str, year: Optional[int], file_name: str = "") -> str:
    """Canonical key collapsing same-movie uploads.

    Returns "" for titles that look like TV episodes (let the series
    grouping handle those) or that produce no recognisable name after
    noise removal. ``file_name`` is consulted as a backup year source for
    captions that lost the year.
    """
    if not title:
        return ""

    # Episodes are series; never produce a movie key for them.
    if series.parse(title) or (file_name and series.parse(file_name)):
        return ""

    # 1. Normalize separators. Dots and underscores become spaces so the
    #    year regex's \b can fire. Then split year-shaped digit runs that
    #    are glued to letters (``1986Tamil`` → ``1986 Tamil``,
    #    ``Minsaram1986`` → ``Minsaram 1986``). Limit the split to 4+ digit
    #    runs so movie titles like ``F1`` and quality tokens like ``720p``
    #    survive intact.
    text = re.sub(r"[._]+", " ", title)
    text = re.sub(r"(\d{4,})([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])(\d{4,})", r"\1 \2", text)

    # 2. Year detect + cut. Release flags always trail the year, so
    #    everything after it is throwaway. file_name provides a backup
    #    year for hand-edited captions that dropped it.
    detected_year = year
    m = _YEAR_RE.search(text)
    if m:
        detected_year = int(m.group(0))
        text = text[: m.start()]
    elif detected_year is None and file_name:
        m2 = _YEAR_RE.search(file_name)
        if m2:
            detected_year = int(m2.group(0))

    # 3. Strip leading channel/group tags. Three patterns cover the
    #    common shapes; loop until stable so stacked tags fall off.
    for _ in range(4):
        new = re.sub(r"^\s*\[\w+\]\s*[-·:]?\s*", "", text)
        new = re.sub(r"^\s*@\w+(?:\s+\w+)*?\s+[-·:]\s+", "", new)
        if new == text:
            break
        text = new

    # 4. Strip recognized noise tokens.
    text = _NOISE_TOKENS.sub(" ", text)

    # 5. Slugify whatever's left.
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).strip().lower()
    if not text:
        return ""
    slug = re.sub(r"\s+", "-", text)
    return f"{slug}::{detected_year}" if detected_year else slug
