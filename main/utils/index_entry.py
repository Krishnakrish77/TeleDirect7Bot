"""
Caption format for INDEX_CHANNEL entries.

The INDEX_CHANNEL is the hub's "database" — each message is one curated
media entry. The caption is structured enough to parse but readable enough
for humans to edit via /edit_index. Round-trips through parse(render(entry))
so auto-generated entries remain editable.

Format:

    🎬 [MS] Dhurandhar (2025)

    Tamil HDRip release.

    #movie #tamil #2025

    📁 720p HD · 950 MB · bin:185

- First line starts with 🎬 then the title; optional ``(YEAR)`` at the end
  becomes ``entry.year`` and is stripped from the title.
- Tag lines start with ``#``; multiple ``#tag`` on the same line are split.
- File lines start with 📁 and are ``quality · size · bin:N`` (quality and
  size optional; ``bin:N`` is required and points at a BIN_CHANNEL message).
- Anything else between the title and the first tag line is description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


TITLE_MARKER = "🎬"
FILE_MARKER = "📁"
ID_MARKER = "🆔"

_YEAR_RE = re.compile(r"\s*\((\d{4})\)\s*$")
_TAG_LINE_RE = re.compile(r"^#\S")
_TAG_RE = re.compile(r"#(\w+)")
_FILE_RE = re.compile(
    r"^📁\s*"
    r"(?:(?P<quality>[^·]+?)\s*·\s*)?"
    r"(?:(?P<size>[^·]+?)\s*·\s*)?"
    r"bin:(?P<bin_id>\d+)\s*$"
)
# Durable provider data on the 🆔 caption line:
#   🆔 tmdb-tv:200931 imdb:tt19378684 poster:/abc.jpg backdrop:/def.jpg
# Lets a re-seed-from-BIN recover the canonical record IDs *and* poster
# art without having to re-search TMDB. Poster/backdrop are TMDB relative
# paths so they're short — the whole line stays well under Telegram's
# 1024-char caption cap.
_ID_TMDB_RE = re.compile(r"\btmdb-(?P<kind>movie|tv):(?P<id>\d+)\b")
_ID_IMDB_RE = re.compile(r"\bimdb:(?P<id>tt\d+)\b")
_ID_POSTER_RE = re.compile(r"\bposter:(?P<path>\S+)")
_ID_BACKDROP_RE = re.compile(r"\bbackdrop:(?P<path>\S+)")

_VIDEO_EXT_RE = re.compile(
    r"\.(mkv|mp4|avi|mov|m4v|wmv|flv|webm|mpg|mpeg|ts)$", re.IGNORECASE
)


@dataclass
class FileVariant:
    bin_id: int
    quality: str = ""
    size: str = ""


@dataclass
class IndexEntry:
    title: str
    year: Optional[int] = None
    description: str = ""
    tags: List[str] = field(default_factory=list)
    files: List[FileVariant] = field(default_factory=list)
    # Durable provider record IDs round-tripped via the 🆔 caption line.
    tmdb_id: Optional[int] = None
    tmdb_kind: str = ""        # "movie" or "tv"; "" if unknown
    imdb_id: str = ""
    # TMDB-relative poster + backdrop paths. Short strings, kept inline so
    # a re-seed-from-BIN restores cards with real artwork — not just IDs.
    poster_path: str = ""
    backdrop_path: str = ""


def parse(caption: str) -> Optional[IndexEntry]:
    """Parse an INDEX_CHANNEL caption into structured data. Returns None if
    no recognisable index content is present (so callers can skip non-index
    messages in the channel)."""
    if not caption:
        return None

    title: Optional[str] = None
    year: Optional[int] = None
    tags: List[str] = []
    files: List[FileVariant] = []
    description_lines: List[str] = []
    tmdb_id: Optional[int] = None
    tmdb_kind: str = ""
    imdb_id: str = ""
    poster_path: str = ""
    backdrop_path: str = ""

    for line in caption.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(TITLE_MARKER) and title is None:
            title_text = stripped[len(TITLE_MARKER):].strip()
            year_match = _YEAR_RE.search(title_text)
            if year_match:
                year = int(year_match.group(1))
                title_text = title_text[: year_match.start()].rstrip()
            title = title_text
        elif stripped.startswith(ID_MARKER):
            tmdb_m = _ID_TMDB_RE.search(stripped)
            if tmdb_m:
                tmdb_kind = tmdb_m.group("kind")
                tmdb_id = int(tmdb_m.group("id"))
            imdb_m = _ID_IMDB_RE.search(stripped)
            if imdb_m:
                imdb_id = imdb_m.group("id")
            poster_m = _ID_POSTER_RE.search(stripped)
            if poster_m:
                poster_path = poster_m.group("path")
            backdrop_m = _ID_BACKDROP_RE.search(stripped)
            if backdrop_m:
                backdrop_path = backdrop_m.group("path")
        elif _TAG_LINE_RE.match(stripped):
            tags.extend(t.lower() for t in _TAG_RE.findall(stripped))
        elif stripped.startswith(FILE_MARKER):
            m = _FILE_RE.match(stripped)
            if m:
                files.append(FileVariant(
                    bin_id=int(m.group("bin_id")),
                    quality=(m.group("quality") or "").strip(),
                    size=(m.group("size") or "").strip(),
                ))
        else:
            description_lines.append(stripped)

    if title is None and not files:
        return None

    return IndexEntry(
        title=title or "(untitled)",
        year=year,
        description="\n".join(description_lines).strip(),
        tags=tags,
        files=files,
        tmdb_id=tmdb_id,
        tmdb_kind=tmdb_kind,
        imdb_id=imdb_id,
        poster_path=poster_path,
        backdrop_path=backdrop_path,
    )


def render(entry: IndexEntry) -> str:
    """Serialize IndexEntry into a caption string. parse(render(e)) == e."""
    parts: List[str] = []

    title_line = f"{TITLE_MARKER} {entry.title}".rstrip()
    if entry.year:
        title_line += f" ({entry.year})"
    parts.append(title_line)

    if entry.description:
        parts.append("")
        parts.append(entry.description)

    if entry.tags:
        parts.append("")
        parts.append(" ".join(f"#{t}" for t in entry.tags))

    if entry.files:
        parts.append("")
        for fv in entry.files:
            bits = []
            if fv.quality:
                bits.append(fv.quality)
            if fv.size:
                bits.append(fv.size)
            bits.append(f"bin:{fv.bin_id}")
            parts.append(f"{FILE_MARKER} {' · '.join(bits)}")

    id_bits: List[str] = []
    if entry.tmdb_id and entry.tmdb_kind:
        id_bits.append(f"tmdb-{entry.tmdb_kind}:{entry.tmdb_id}")
    if entry.imdb_id:
        id_bits.append(f"imdb:{entry.imdb_id}")
    if entry.poster_path:
        id_bits.append(f"poster:{entry.poster_path}")
    if entry.backdrop_path:
        id_bits.append(f"backdrop:{entry.backdrop_path}")
    if id_bits:
        parts.append("")
        parts.append(f"{ID_MARKER} {' '.join(id_bits)}")

    return "\n".join(parts)


def title_from_filename(filename: Optional[str]) -> str:
    """Derive a starter title from a media filename. Returns the readable
    portion of the name with release-noise + channel prefixes stripped —
    so even unenriched entries display "F1 The Movie" instead of the raw
    "[MS] F1 The Movie 2025 720p HDRip" once they land in the catalogue.
    Falls back to a generic placeholder when no name is available.
    """
    if not filename:
        return "(untitled)"
    # Lazy import to avoid a cycle (dedup → series; series stays leaf).
    from main.utils.dedup import clean_for_search
    cleaned = clean_for_search(filename)
    if cleaned:
        return cleaned
    bare = _VIDEO_EXT_RE.sub("", filename).strip()
    return bare or "(untitled)"


def year_from_filename(filename: Optional[str]) -> Optional[int]:
    """Pull a 4-digit year out of a filename, ignoring resolution-shaped
    numbers like 1080. Used at index time so the entry's ``year`` field
    is populated even before TMDB enrichment runs.
    """
    if not filename:
        return None
    # Match years between 1900 and 2099, requiring word boundaries — runs
    # of digits glued to letters (1986Tamil) are handled by the same
    # split logic clean_for_search applies.
    text = filename
    text = text.replace(".", " ").replace("_", " ")
    import re as _re
    m = _re.search(r"\b(19|20)\d{2}\b", text)
    if m:
        return int(m.group(0))
    # Fall back: digits-stuck-to-letters case (Minsaram1986)
    m = _re.search(r"(?<=\D)((?:19|20)\d{2})(?=\D|$)", filename)
    return int(m.group(1)) if m else None
