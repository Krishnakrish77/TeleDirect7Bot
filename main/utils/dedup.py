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

# Words that are NEVER legitimate title content — acronyms, pirate-site
# brand names, abbreviations. Safe to strip from the start of the title
# in any context.
_LEADING_JUNK_ALWAYS = {
    "idiots", "hdt", "mm", "wmr", "fbm", "snr",
    "tamilblasters", "1tamilblasters", "tamilmv", "tamilmoviez",
    "movieztamil", "moviezzclub", "hevchubx", "breakfreemovies",
    "kickass", "kickass_torrents", "a2dxmovies", "gangtamil",
    "uhd_tamil", "moviesda",
    "predvdrip", "prehd", "untouched", "ds4k",
}

# Words that double as legitimate English title words (Red Sparrow, The
# Cinematic Universe, ...) but also appear as channel/release markers.
# Only stripped when an @-prefix or www-domain was already stripped
# earlier — meaning the cleaner is now inside the channel-noise region.
_LEADING_JUNK_AFTER_PREFIX = {
    "red", "rodeo", "world", "mobile", "team", "cinematic",
    "real", "cf", "mc", "mp", "ms",
}

# Run-on initialisms left behind by release-group prefixes like
# ``@R_A_R_B_G_<title>`` once underscores become spaces — strips a
# leading run of 2+ single-letter tokens.
_LEADING_INITIALS_RE = re.compile(r"^\s*(?:[a-zA-Z]\s+){2,}")

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

    # Strip leading channel/group prefixes BEFORE year detection.
    # ``@R_A_R_B_G_2001_A_Space_Odyssey_1968_…`` carries the year 2001
    # inside the release-group name; cutting on it would lose the
    # actual title. Stripping prefixes first lets the year scan land
    # on a real year.
    stripped_prefix = False
    for _ in range(4):
        new = re.sub(r"^\s*\[\w+\]\s*[-·:]?\s*", "", text)
        new = re.sub(r"^\s*@\w+(?:\s+\w+)*?\s+[-·:]\s+", "", new)
        if new == text:
            break
        text = new
        stripped_prefix = True

    new = re.sub(r"^\s*@\w+\s+", "", text)
    if new != text:
        text = new
        stripped_prefix = True

    new = re.sub(
        r"^\s*www\s+\S+(?:\s+(?:buzz|net|com|org|info|io|cc))?\s+",
        "", text, flags=re.IGNORECASE,
    )
    if new != text:
        text = new
        stripped_prefix = True

    # Strip leading single-letter runs (``A R B G`` from @R_A_R_B_G).
    text = _LEADING_INITIALS_RE.sub("", text)

    # Year detect + cut, in a loop. If the first year is at the very
    # start (a prefix year), keep walking forward looking for a year
    # that has actual title text before it — that's the real release
    # year. Also handles ``1959 - Sleeping Beauty`` (single prefix
    # year, no second year — the after-side becomes the title).
    m = _YEAR_RE.search(text)
    while m:
        before = text[: m.start()].strip(" -—:·")
        after = text[m.end():].strip(" -—:·")
        if before:
            text = before
            break
        if after:
            text = after
            m = _YEAR_RE.search(text)
            continue
        break

    # Strip @-handles that appear mid-string (trailing handles like
    # ``Good Witch S07E02 @T4TVSeries mkv``).
    text = _INLINE_HANDLE_RE.sub(" ", text)

    # Drop leading junk markers. ALWAYS-list runs unconditionally —
    # these are acronyms / pirate-site brand names that never appear
    # in titles. AFTER_PREFIX-list only runs when a channel/domain
    # prefix was already stripped, so legitimate titles starting with
    # ``Red`` or ``World`` survive when there's no prefix context.
    words = text.split()
    while words and words[0].lower() in _LEADING_JUNK_ALWAYS:
        words = words[1:]
        stripped_prefix = True
    # ALSO strip an AFTER_PREFIX word when it leaks in without an
    # @-prefix but is clearly a channel handle: leading word is
    # entirely lowercase and the next word starts uppercase. Real
    # titles ``Red Sparrow``/``World War Z``/``Cinematic Universe``
    # always have the leading word title-cased, so this leaves them
    # alone while catching ``rodeo When Life Gives You Tangerines``.
    if (not stripped_prefix and len(words) >= 2
            and words[0].islower()
            and words[0] in _LEADING_JUNK_AFTER_PREFIX
            and words[1][:1].isupper()):
        words = words[1:]
        stripped_prefix = True
    if stripped_prefix:
        while words and words[0].lower() in _LEADING_JUNK_AFTER_PREFIX:
            words = words[1:]
    text = " ".join(words)

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
    #
    #    Special case: if the year is at the very start of the text
    #    (``1959 - Sleeping Beauty.mp4`` → ``1959 Sleeping Beauty mp4``)
    #    cutting at the year leaves nothing, but the actual title sits
    #    AFTER the year. Detect that and cut from the other side.
    detected_year = year
    m = _YEAR_RE.search(text)
    if m:
        detected_year = int(m.group(0))
        before = text[: m.start()].strip(" -—:·")
        after = text[m.end():].strip(" -—:·")
        # Prefer the side with actual content. If the pre-year side is
        # empty or just punctuation, use the post-year side as the
        # title instead.
        if not before and after:
            text = after
        else:
            text = before or after
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
