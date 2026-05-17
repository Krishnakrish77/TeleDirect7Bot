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

_YEAR_RE = re.compile(r"\s*\((\d{4})\)\s*$")
_TAG_LINE_RE = re.compile(r"^#\S")
_TAG_RE = re.compile(r"#(\w+)")
_FILE_RE = re.compile(
    r"^📁\s*"
    r"(?:(?P<quality>[^·]+?)\s*·\s*)?"
    r"(?:(?P<size>[^·]+?)\s*·\s*)?"
    r"bin:(?P<bin_id>\d+)\s*$"
)

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

    return "\n".join(parts)


def title_from_filename(filename: Optional[str]) -> str:
    """Derive a starter title from a media filename. Strips the common video
    extensions; admin can /edit_index to clean further. Falls back to a
    generic placeholder when no name is available."""
    if not filename:
        return "(untitled)"
    cleaned = _VIDEO_EXT_RE.sub("", filename).strip()
    return cleaned or "(untitled)"
