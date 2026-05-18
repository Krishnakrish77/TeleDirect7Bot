"""External subtitle sidecars: SRT→VTT conversion and pairing helpers.

Sidecar .srt / .vtt files uploaded to BIN_CHANNEL are paired with a video
by two mechanisms:

1. Reply-to: if the upload was sent as a reply to a video the bot already
   indexed, the reply's file_unique_id[:6] identifies the target HubItem.
2. Filename stem: ``Movie.2024.1080p.en.srt`` pairs with any indexed video
   whose stem normalises to ``movie.2024.1080p``. Language suffix is
   stripped before comparison.
"""

from __future__ import annotations

import re
from typing import Optional


_SRT_TS_RE = re.compile(r"(\d{2}:\d{2}:\d{2}),(\d{3})")
# Trailing language code: .en, .eng, .pt-BR, .zh_CN, etc.
_LANG_SUFFIX_RE = re.compile(r"\.([a-z]{2,3}(?:[-_][A-Za-z]{2,4})?)$", re.IGNORECASE)
_SUB_EXT_RE = re.compile(r"\.(srt|vtt|sbv|ass|ssa)$", re.IGNORECASE)


def is_subtitle_filename(filename: str) -> bool:
    if not filename:
        return False
    return bool(_SUB_EXT_RE.search(filename))


def is_subtitle_mime(mime: str) -> bool:
    if not mime:
        return False
    m = mime.lower()
    return m in {
        "application/x-subrip",
        "application/x-srt",
        "text/srt",
        "text/vtt",
        "text/plain",  # many clients upload .srt as text/plain
    }


def srt_to_vtt(data: bytes) -> bytes:
    """Convert SRT bytes to WebVTT. Idempotent for already-VTT input.

    SRT timestamps use ``HH:MM:SS,mmm``; WebVTT requires ``HH:MM:SS.mmm``
    and a ``WEBVTT`` header line. Anything else (cue numbers, blank lines,
    cue text) is structurally identical between the two formats.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Legacy encodings often ship with .srt from European distros.
        text = data.decode("latin-1", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    if text.lstrip().startswith("WEBVTT"):
        return text.encode("utf-8")

    text = _SRT_TS_RE.sub(r"\1.\2", text)
    return ("WEBVTT\n\n" + text.lstrip()).encode("utf-8")


def language_from_filename(filename: str) -> str:
    """``movie.en.srt`` → ``en``; ``movie.srt`` → ``""``."""
    if not filename:
        return ""
    stem = _SUB_EXT_RE.sub("", filename)
    m = _LANG_SUFFIX_RE.search(stem)
    if not m:
        return ""
    return m.group(1).lower().replace("_", "-")


def stem_for_pairing(filename: str) -> str:
    """Canonical stem that should match between a video and its sidecar.

    Strips the file extension and a trailing language code so
    ``Movie.2024.1080p.mkv`` and ``Movie.2024.1080p.en.srt`` collapse to the
    same key.
    """
    if not filename:
        return ""
    name = _SUB_EXT_RE.sub("", filename)
    # Strip any non-subtitle extension too (.mkv, .mp4, .avi, etc.)
    if "." in name:
        head, tail = name.rsplit(".", 1)
        if 2 <= len(tail) <= 5 and tail.lower() not in {"1080p", "720p", "480p", "2160p"}:
            # Looks like a file extension; strip it.
            name = head
    lang_match = _LANG_SUFFIX_RE.search(name)
    if lang_match:
        name = name[: lang_match.start()]
    return name.lower().strip(" .-_")


def derive_label(language: str, filename: str) -> str:
    """Human-friendly label for the track picker."""
    if language:
        return language.upper()
    if filename:
        base = _SUB_EXT_RE.sub("", filename)
        return base or "Subtitles"
    return "Subtitles"
